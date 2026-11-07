// Narrow native AX control for Ableton Live's MIDI Output Sync checkboxes.
import ApplicationServices
import AppKit
import CryptoKit
import Foundation

let maximumWindows = 64
let maximumElements = 1500
let maximumDepth = 12
let outputSuffix = ": MIDI Output: Sync"
let receiptFormat = "codex-devtools-live-midi-sync-receipt-v1"

struct Options {
    var inputMode = ""
    var inputExpected = ""
    var inputIdentity = ""
    var inputDesired = -1
    var pid: pid_t = 0
    var bundleID = ""
    var expectedCount = 0
    var operation = ""
    var baselinePath = ""
    var baselineHash = ""
    var historicalCurrentPath = ""
    var historicalCurrentHash = ""
    var maximumBaselineAgeSeconds = 300.0
    var deadlineUnixSeconds = 0.0
}

struct SyncFailure: Error {
    let message: String
}

struct OutputControl {
    let name: String
    let description: String
    let element: AXUIElement
    let value: Int
}

var phase = "startup"
var receipt: [String: Any]?
var options = Options()

func pressDeadlineIsValid(
    _ deadlineUnixSeconds: TimeInterval,
    nowUnixSeconds: TimeInterval = Date().timeIntervalSince1970
) -> Bool {
    deadlineUnixSeconds.isFinite && nowUnixSeconds.isFinite
        && deadlineUnixSeconds > nowUnixSeconds
        && deadlineUnixSeconds - nowUnixSeconds <= 180
}

func parseOptions() throws -> Options {
    var result = Options()
    var index = 1
    let args = CommandLine.arguments
    while index < args.count {
        let key = args[index]
        guard index + 1 < args.count else { throw SyncFailure(message: "missing value for \(key)") }
        let value = args[index + 1]
        switch key {
        case "--input-inventory": result.inputMode = value
        case "--input-expected": result.inputExpected = value
        case "--input-identity": result.inputIdentity = value
        case "--input-desired": result.inputDesired = Int(value) ?? -1
        case "--pid":
            guard let parsed = Int32(value), parsed > 0 else { throw SyncFailure(message: "PID must be positive") }
            result.pid = pid_t(parsed)
        case "--bundle-id": result.bundleID = value
        case "--expected-count":
            guard let parsed = Int(value), (1...128).contains(parsed) else { throw SyncFailure(message: "expected count must be 1–128") }
            result.expectedCount = parsed
        case "--operation": result.operation = value
        case "--baseline": result.baselinePath = value
        case "--baseline-sha256": result.baselineHash = value
        case "--historical-current-inventory": result.historicalCurrentPath = value
        case "--historical-current-sha256": result.historicalCurrentHash = value
        case "--max-baseline-age-seconds":
            guard let parsed = Double(value), parsed.isFinite, parsed > 0, parsed <= 300 else {
                throw SyncFailure(message: "maximum baseline age must be positive and at most 300 seconds")
            }
            result.maximumBaselineAgeSeconds = parsed
        case "--deadline-unix-seconds":
            let now = Date().timeIntervalSince1970
            guard let parsed = Double(value), parsed.isFinite,
                  pressDeadlineIsValid(parsed, nowUnixSeconds: now) else {
                throw SyncFailure(message: "absolute press deadline is invalid or expired")
            }
            result.deadlineUnixSeconds = parsed
        default: throw SyncFailure(message: "unknown option: \(key)")
        }
        index += 2
    }
    if !result.inputMode.isEmpty {
        guard result.pid > 0, !result.bundleID.isEmpty, result.expectedCount > 0,
              ["inspect", "press"].contains(result.inputMode) else {
            throw SyncFailure(message: "invalid input inventory arguments")
        }
        if result.inputMode == "press" {
            guard ["isolate", "restore"].contains(result.operation),
                  !result.baselinePath.isEmpty, result.baselineHash.count == 64,
                  !result.inputExpected.isEmpty, !result.inputIdentity.isEmpty,
                  result.inputDesired == 0 || result.inputDesired == 1,
                  result.deadlineUnixSeconds > 0 else {
                throw SyncFailure(message: "input press requires explicit operation, baseline and exact target")
            }
            if !result.historicalCurrentPath.isEmpty {
                guard result.operation == "restore", result.historicalCurrentHash.count == 64 else {
                    throw SyncFailure(message: "historical current inventory is valid only for restore")
                }
            } else if !result.historicalCurrentHash.isEmpty {
                throw SyncFailure(message: "historical current inventory hash has no file")
            }
        } else if !result.operation.isEmpty || !result.baselinePath.isEmpty || !result.inputExpected.isEmpty || !result.inputIdentity.isEmpty || !result.historicalCurrentPath.isEmpty || !result.historicalCurrentHash.isEmpty || result.inputDesired != -1 || result.deadlineUnixSeconds != 0 {
            throw SyncFailure(message: "inspect never accepts action arguments")
        }
        return result
    }
    guard result.pid > 0, !result.bundleID.isEmpty, result.expectedCount > 0,
          ["isolate", "restore"].contains(result.operation),
          !result.baselinePath.isEmpty, result.baselineHash.count == 64,
          result.maximumBaselineAgeSeconds > 0 else {
        throw SyncFailure(message: "incomplete native MIDI Sync arguments")
    }
    return result
}

func axValue(_ element: AXUIElement, _ attribute: String, allowUnsupported: Bool = false) throws -> CFTypeRef? {
    AXUIElementSetMessagingTimeout(element, 2.0)
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    if error == .success { return value }
    if error == .noValue || (allowUnsupported && error == .attributeUnsupported) { return nil }
    throw SyncFailure(message: "AX read \(attribute) failed (\(error.rawValue))")
}

func requiredString(_ element: AXUIElement, _ attribute: String) throws -> String {
    guard let value = try axValue(element, attribute), let string = value as? String else {
        throw SyncFailure(message: "required AX string is unavailable: \(attribute)")
    }
    return string
}

func childElements(_ element: AXUIElement) throws -> [AXUIElement] {
    guard let value = try axValue(element, kAXChildrenAttribute as String) else { return [] }
    guard let children = value as? [AXUIElement] else {
        throw SyncFailure(message: "AXChildren has an unexpected type")
    }
    return children
}

func checkboxValue(_ element: AXUIElement) throws -> Int {
    guard let value = try axValue(element, kAXValueAttribute as String), let number = value as? NSNumber else {
        throw SyncFailure(message: "output Sync checkbox value is unavailable")
    }
    let integer = number.intValue
    guard (integer == 0 || integer == 1), number.doubleValue == Double(integer) else {
        throw SyncFailure(message: "output Sync checkbox value is not 0 or 1")
    }
    return integer
}

func enabled(_ element: AXUIElement) throws -> Bool {
    guard let value = try axValue(element, kAXEnabledAttribute as String), let number = value as? NSNumber else {
        throw SyncFailure(message: "output Sync checkbox enabled state is unavailable")
    }
    let integer = number.intValue
    guard (integer == 0 || integer == 1), number.doubleValue == Double(integer) else {
        throw SyncFailure(message: "output Sync checkbox enabled state is not boolean")
    }
    return integer == 1
}

func matchingOutputs(_ app: AXUIElement) throws -> (AXUIElement, [String: OutputControl], Int, Int) {
    guard let rawWindows = try axValue(app, kAXWindowsAttribute as String),
          let windows = rawWindows as? [AXUIElement] else {
        throw SyncFailure(message: "target application windows are unavailable")
    }
    guard !windows.isEmpty, windows.count <= maximumWindows else {
        throw SyncFailure(message: "window inventory is empty or exceeds native bounds")
    }
    var settingsWindows: [AXUIElement] = []
    for window in windows {
        if try requiredString(window, kAXTitleAttribute as String) == "Settings" {
            settingsWindows.append(window)
        }
    }
    guard settingsWindows.count == 1 else {
        throw SyncFailure(message: "exact Settings window matched \(settingsWindows.count) windows; expected one")
    }
    let settings = settingsWindows[0]
    var seen = Set<CFHashCode>()
    var queue: [(AXUIElement, Int)] = [(settings, 0)]
    var visited = 0
    var controls: [String: OutputControl] = [:]
    while !queue.isEmpty {
        let (element, depth) = queue.removeFirst()
        let identity = CFHash(element)
        if !seen.insert(identity).inserted { continue }
        guard visited < maximumElements else {
            throw SyncFailure(message: "Settings AX tree exceeds native element bound")
        }
        visited += 1
        guard let roleValue = try axValue(element, kAXRoleAttribute as String),
              let role = roleValue as? String else {
            throw SyncFailure(message: "Settings AX element role is unreadable")
        }
        if role == (kAXCheckBoxRole as String) {
            if let descriptionValue = try axValue(element, kAXDescriptionAttribute as String),
               let description = descriptionValue as? String,
               description.hasSuffix(outputSuffix) {
                let name = String(description.dropLast(outputSuffix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
                guard !name.isEmpty else { throw SyncFailure(message: "output Sync checkbox has an empty device name") }
                guard controls[name] == nil else { throw SyncFailure(message: "duplicate output Sync name: \(name)") }
                guard try enabled(element) else { throw SyncFailure(message: "output Sync checkbox is disabled: \(name)") }
                controls[name] = OutputControl(name: name, description: description, element: element, value: try checkboxValue(element))
            }
        }
        let children = try childElements(element)
        if depth < maximumDepth {
            for child in children {
                if visited + queue.count >= maximumElements {
                    throw SyncFailure(message: "Settings AX tree would exceed native element bound")
                }
                queue.append((child, depth + 1))
            }
        } else if !children.isEmpty {
            throw SyncFailure(message: "Settings AX tree exceeds native depth bound")
        }
    }
    return (settings, controls, visited, windows.count)
}

func validateBaselineFreshness(_ date: Date, now: Date = Date()) throws {
    let age = now.timeIntervalSince(date)
    guard age >= -5, age <= options.maximumBaselineAgeSeconds else {
        throw SyncFailure(message: "baseline is stale or from the future")
    }
}

func readBaseline() throws -> (Data, [String: Any], [String: Int], [String: String], Date) {
    phase = "baseline-read"
    let url = URL(fileURLWithPath: options.baselinePath)
    let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
    guard values.isRegularFile == true, values.isSymbolicLink != true else {
        throw SyncFailure(message: "baseline must be a regular file, not a symlink")
    }
    let data = try Data(contentsOf: url)
    let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    guard hash == options.baselineHash else { throw SyncFailure(message: "baseline changed after wrapper validation") }
    guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          root["format"] as? String == "codex-devtools-live-midi-sync-baseline-v1",
          root["status"] as? String == "observed",
          root["read_only"] as? Bool == true,
          root["expected_output_count"] as? Int == options.expectedCount,
          let target = root["target"] as? [String: Any],
          target["pid"] as? Int == Int(options.pid),
          target["bundle_identifier"] as? String == options.bundleID,
          let rows = root["outputs"] as? [[String: Any]], rows.count == options.expectedCount,
          let window = root["settings_window"] as? [String: Any], window["title"] as? String == "Settings" else {
        throw SyncFailure(message: "baseline identity, bounds or output schema is invalid")
    }
    guard let bounds = root["bounds"] as? [String: Any],
          bounds["max_elements"] as? Int == maximumElements,
          bounds["max_depth"] as? Int == maximumDepth,
          bounds["max_windows"] as? Int == maximumWindows,
          bounds["window_title_filter"] as? String == "Settings",
          bounds["truncated"] as? Bool == false,
          bounds["windows_truncated"] as? Bool == false,
          let observedCount = bounds["window_count"] as? Int,
          bounds["windows_observed"] as? Int == observedCount,
          observedCount > 0, observedCount <= maximumWindows,
          let elementCount = bounds["elements_observed"] as? Int,
          elementCount > 0, elementCount <= maximumElements,
          let windowIndex = window["index"] as? Int,
          windowIndex >= 0, windowIndex < observedCount,
          let observedAt = root["observed_at_utc"] as? String else {
        throw SyncFailure(message: "baseline bounds or Settings window index is invalid")
    }
    let timestampParser = ISO8601DateFormatter()
    timestampParser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let date = timestampParser.date(from: observedAt) else {
        throw SyncFailure(message: "baseline observation timestamp is invalid")
    }
    try validateBaselineFreshness(date)
    var original: [String: Int] = [:]
    var descriptions: [String: String] = [:]
    for row in rows {
        guard let name = row["name"] as? String, !name.isEmpty,
              let description = row["description"] as? String,
              description == "\(name)\(outputSuffix)",
              row["role"] as? String == (kAXCheckBoxRole as String),
              row["enabled"] as? Bool == true,
              let value = row["value"] as? Int, value == 0 || value == 1,
              original[name] == nil else {
            throw SyncFailure(message: "baseline contains an invalid or duplicate output row")
        }
        original[name] = value
        descriptions[name] = description
    }
    guard original.count == options.expectedCount else { throw SyncFailure(message: "baseline output count is inconsistent") }
    return (data, root, original, descriptions, date)
}

func setAction(
    _ index: Int,
    attempted: Any,
    outcome: String,
    actual: Any,
    error: String? = nil,
    prepressElements: Int? = nil,
    prepressWindows: Int? = nil
) {
    guard var current = receipt, var actions = current["actions"] as? [[String: Any]], actions.indices.contains(index) else { return }
    actions[index]["attempted"] = attempted
    actions[index]["outcome"] = outcome
    actions[index]["actual_value"] = actual
    if let error { actions[index]["error"] = error }
    if let prepressElements { actions[index]["prepress_elements"] = prepressElements }
    if let prepressWindows { actions[index]["prepress_windows"] = prepressWindows }
    current["actions"] = actions
    receipt = current
}

func hasPressAction(_ element: AXUIElement) throws -> Bool {
    var actions: CFArray?
    AXUIElementSetMessagingTimeout(element, 2.0)
    let error = AXUIElementCopyActionNames(element, &actions)
    guard error == .success, let names = actions as? [String] else {
        throw SyncFailure(message: "could not read output Sync checkbox actions (\(error.rawValue))")
    }
    return names.contains(kAXPressAction as String)
}

func focusedSettingsWindow(_ app: AXUIElement, expected: AXUIElement) throws -> Bool {
    guard let value = try axValue(app, kAXFocusedWindowAttribute as String),
          CFGetTypeID(value) == AXUIElementGetTypeID() else {
        return false
    }
    let window = unsafeBitCast(value, to: AXUIElement.self)
    return CFEqual(window, expected)
}

func focusObserved(_ value: Any) -> [String: Any] {
    ["status": "observed", "value": value]
}

func focusUnavailable(_ reason: String) -> [String: Any] {
    ["status": "unavailable", "reason": reason]
}

func focusError(_ reason: String) -> [String: Any] {
    ["status": "error", "reason": reason]
}

func focusError(_ error: AXError) -> [String: Any] {
    ["status": "error", "error_code": Int(error.rawValue)]
}

func focusedWindowString(_ window: AXUIElement, attribute: String) -> [String: Any] {
    AXUIElementSetMessagingTimeout(window, 2.0)
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(window, attribute as CFString, &value)
    switch error {
    case .success:
        guard let text = value as? String else { return focusError("unexpected_type") }
        guard !text.isEmpty, text.unicodeScalars.count <= 128 else { return focusError("value_out_of_bounds") }
        return focusObserved(text)
    case .noValue:
        return focusUnavailable("no_value")
    case .attributeUnsupported:
        return focusUnavailable("attribute_unsupported")
    default:
        return focusError(error)
    }
}

func focusRefusalDiagnostic(app: AXUIElement, expectedSettings: AXUIElement, pid: pid_t, bundleID: String) -> [String: Any] {
    let processFact: [String: Any]
    if let process = NSRunningApplication(processIdentifier: pid) {
        processFact = process.bundleIdentifier == bundleID
            ? focusObserved(process.isActive)
            : focusUnavailable("process_identity_changed")
    } else {
        processFact = focusUnavailable("process_unavailable")
    }

    let frontmostFact: [String: Any]
    if let frontmost = NSWorkspace.shared.frontmostApplication {
        frontmostFact = focusObserved(Int(frontmost.processIdentifier))
    } else {
        frontmostFact = focusUnavailable("no_frontmost_application")
    }

    AXUIElementSetMessagingTimeout(app, 2.0)
    var focusedValue: CFTypeRef?
    let focusedError = AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &focusedValue)
    let focused: [String: Any]
    switch focusedError {
    case .success:
        if let focusedValue, CFGetTypeID(focusedValue) == AXUIElementGetTypeID() {
            let window = unsafeBitCast(focusedValue, to: AXUIElement.self)
            focused = [
                "status": "observed",
                "role": focusedWindowString(window, attribute: kAXRoleAttribute as String),
                "title": focusedWindowString(window, attribute: kAXTitleAttribute as String),
                "matches_settings": focusObserved(CFEqual(window, expectedSettings)),
            ]
        } else {
            let error = focusError("unexpected_type")
            focused = [
                "status": "error",
                "reason": "unexpected_type",
                "role": error,
                "title": error,
                "matches_settings": error,
            ]
        }
    case .noValue:
        let unavailable = focusUnavailable("focused_window_unavailable")
        focused = ["status": "unavailable", "reason": "no_value",
                   "role": unavailable, "title": unavailable, "matches_settings": unavailable]
    case .attributeUnsupported:
        let unavailable = focusUnavailable("focused_window_unavailable")
        focused = ["status": "unavailable", "reason": "attribute_unsupported",
                   "role": unavailable, "title": unavailable, "matches_settings": unavailable]
    default:
        let error = focusError(focusedError)
        let unavailable = focusUnavailable("focused_window_unavailable")
        focused = ["status": "error", "error_code": Int(focusedError.rawValue),
                   "role": error, "title": error, "matches_settings": unavailable]
    }

    return [
        "format": "codex-devtools-live-midi-sync-focus-diagnostics-v1",
        "captured_after_guard_refusal": true,
        "process_is_active": processFact,
        "frontmost_pid": frontmostFact,
        "focused_window": focused,
    ]
}

func recordFocusRefusal(app: AXUIElement, settings: AXUIElement, pid: pid_t, bundleID: String) {
    guard var current = receipt else { return }
    current["focus_diagnostics"] = focusRefusalDiagnostic(
        app: app, expectedSettings: settings, pid: pid, bundleID: bundleID
    )
    receipt = current
}

func focusedSettingsWindowOrRecord(app: AXUIElement, settings: AXUIElement, pid: pid_t, bundleID: String) throws -> Bool {
    do {
        return try focusedSettingsWindow(app, expected: settings)
    } catch {
        recordFocusRefusal(app: app, settings: settings, pid: pid, bundleID: bundleID)
        throw error
    }
}

func targetCheck(_ pid: pid_t, bundleID: String) throws -> NSRunningApplication {
    guard let process = NSRunningApplication(processIdentifier: pid), process.bundleIdentifier == bundleID else {
        throw SyncFailure(message: "exact target PID/bundle identity is no longer running")
    }
    return process
}

func run() throws -> [String: Any] {
    phase = "baseline-read"
    let (_, baseline, original, descriptions, baselineDate) = try readBaseline()
    let baselineTarget = baseline["target"] as! [String: Any]
    let processName = baselineTarget["process_name"] as! String
    let baselineHash = options.baselineHash
    let baseRows = (baseline["outputs"] as! [[String: Any]]).sorted { ($0["name"] as! String) < ($1["name"] as! String) }
    var actionRows: [[String: Any]] = baseRows.map { row in
        let name = row["name"] as! String
        let value = original[name]!
        return [
            "name": name,
            "description": descriptions[name]!,
            "baseline_value": value,
            "desired_value": options.operation == "isolate" ? 0 : value,
            "attempted": false,
            "outcome": "not_attempted",
            "actual_value": NSNull(),
        ]
    }
    receipt = [
        "format": receiptFormat,
        "status": "stopped",
        "phase": "preflight",
        "operation": options.operation,
        "read_only": false,
        "target": ["pid": options.pid, "bundle_identifier": options.bundleID, "process_name": processName],
        "baseline_sha256": baselineHash,
        "expected_output_count": options.expectedCount,
        "actions": actionRows,
        "manual_inspection_required": false,
        "error": NSNull(),
    ]
    phase = "identity"
    let process = try targetCheck(options.pid, bundleID: options.bundleID)
    guard process.localizedName == processName else { throw SyncFailure(message: "target process name changed from the baseline") }
    guard AXIsProcessTrusted() else { throw SyncFailure(message: "Accessibility is not trusted; no prompt was requested") }
    phase = "preflight"
    guard process.isTerminated == false else { throw SyncFailure(message: "target process already exited") }
    let app = AXUIElementCreateApplication(options.pid)
    let (settings, controls, observedElements, observedWindows) = try matchingOutputs(app)
    guard controls.count == options.expectedCount else {
        throw SyncFailure(message: "native output Sync count \(controls.count) differs from expected \(options.expectedCount)")
    }
    guard Set(controls.keys) == Set(original.keys) else {
        throw SyncFailure(message: "native output Sync identities differ from baseline")
    }
    for (name, control) in controls where control.description != descriptions[name] {
        throw SyncFailure(message: "native output Sync description differs from baseline: \(name)")
    }
    var current = Dictionary(uniqueKeysWithValues: controls.map { ($0.key, $0.value.value) })
    for index in actionRows.indices {
        let name = actionRows[index]["name"] as! String
        actionRows[index]["actual_value"] = current[name]!
    }
    if var initialized = receipt { initialized["actions"] = actionRows; receipt = initialized }
    var desired: [String: Int] = [:]
    for name in original.keys {
        let value = current[name]!
        if options.operation == "isolate" {
            guard value == original[name]! else { throw SyncFailure(message: "current Sync value changed from baseline before isolation: \(name)") }
            desired[name] = 0
        } else {
            guard original[name] == 0 ? value == 0 : (value == 0 || value == 1) else {
                throw SyncFailure(message: "current Sync value is outside partial-isolation restore states: \(name)")
            }
            desired[name] = original[name]!
        }
    }
    let candidates = original.keys.sorted().filter { current[$0] != desired[$0] }
    for name in candidates {
        guard try hasPressAction(controls[name]!.element) else {
            throw SyncFailure(message: "required AXPress action is unavailable: \(name)")
        }
    }
    let processActive = process.isActive
    let frontmostMatches = processActive
        && NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid
    let focusedSettingsMatches = processActive && frontmostMatches
        ? try focusedSettingsWindowOrRecord(
            app: app, settings: settings, pid: options.pid, bundleID: options.bundleID
        )
        : false
    guard processActive, frontmostMatches, focusedSettingsMatches else {
        recordFocusRefusal(app: app, settings: settings, pid: options.pid, bundleID: options.bundleID)
        throw SyncFailure(message: "exact Live Settings window is not foreground and focused")
    }
    phase = "actions"
    for name in candidates {
        let currentProcess = try targetCheck(options.pid, bundleID: options.bundleID)
        let active = currentProcess.isActive
        let foregroundMatches = active
            && NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid
        let windowMatches = active && foregroundMatches
            ? try focusedSettingsWindowOrRecord(
                app: app, settings: settings, pid: options.pid, bundleID: options.bundleID
            )
            : false
        guard active, foregroundMatches, windowMatches else {
            recordFocusRefusal(app: app, settings: settings, pid: options.pid, bundleID: options.bundleID)
            throw SyncFailure(message: "target focus or process identity changed before \(name)")
        }
        let (freshSettings, freshControls, freshElementCount, freshWindowCount) = try matchingOutputs(app)
        guard CFEqual(freshSettings, settings),
              freshControls.count == options.expectedCount,
              freshElementCount == observedElements,
              freshWindowCount == observedWindows,
              Set(freshControls.keys) == Set(original.keys) else {
            throw SyncFailure(message: "complete Settings output inventory changed before \(name)")
        }
        for rowName in original.keys {
            guard let previous = controls[rowName], let fresh = freshControls[rowName],
                  CFEqual(fresh.element, previous.element),
                  fresh.description == descriptions[rowName],
                  fresh.value == current[rowName] else {
                throw SyncFailure(message: "output Sync control identity or value changed before \(name): \(rowName)")
            }
        }
        let freshControl = freshControls[name]!
        guard try hasPressAction(freshControl.element) else {
            throw SyncFailure(message: "required AXPress action is unavailable before \(name)")
        }
        for rowName in original.keys {
            let observed = try checkboxValue(freshControls[rowName]!.element)
            guard observed == current[rowName] else {
                throw SyncFailure(message: "output Sync state changed during prepress validation: \(rowName)")
            }
        }
        let finalProcess = try targetCheck(options.pid, bundleID: options.bundleID)
        let finalActive = finalProcess.isActive
        let finalForegroundMatches = finalActive
            && NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid
        let finalWindowMatches = finalActive && finalForegroundMatches
            ? try focusedSettingsWindowOrRecord(
                app: app, settings: freshSettings, pid: options.pid, bundleID: options.bundleID
            )
            : false
        guard finalActive, finalForegroundMatches, finalWindowMatches else {
            recordFocusRefusal(app: app, settings: freshSettings, pid: options.pid, bundleID: options.bundleID)
            throw SyncFailure(message: "target focus or process identity changed before pressing \(name)")
        }
        try validateBaselineFreshness(baselineDate)
        guard try checkboxValue(freshControl.element) == current[name] else {
            throw SyncFailure(message: "output Sync value changed before press: \(name)")
        }
        let index = baseRows.firstIndex { ($0["name"] as? String) == name }!
        setAction(
            index,
            attempted: true,
            outcome: "unknown",
            actual: NSNull(),
            prepressElements: freshElementCount,
            prepressWindows: freshWindowCount
        )
        phase = "press:\(name)"
        AXUIElementSetMessagingTimeout(freshControl.element, 2.0)
        let pressError = AXUIElementPerformAction(freshControl.element, kAXPressAction as CFString)
        if pressError != .success {
            setAction(index, attempted: true, outcome: "unknown", actual: NSNull(), error: "AXPress failed (\(pressError.rawValue)); it may have taken effect")
            throw SyncFailure(message: "AXPress outcome is unknown for \(name)")
        }
        let actual: Int
        do {
            actual = try checkboxValue(freshControl.element)
        } catch {
            setAction(index, attempted: true, outcome: "unknown", actual: NSNull(), error: String(describing: error))
            throw error
        }
        setAction(index, attempted: true, outcome: "known", actual: actual)
        guard actual == desired[name] else {
            throw SyncFailure(message: "AXPress completed but readback did not reach desired value for \(name)")
        }
        current[name] = actual
    }
    phase = "final-readback"
    let (_, finalControls, finalElementCount, finalWindowCount) = try matchingOutputs(app)
    guard finalControls.count == options.expectedCount, Set(finalControls.keys) == Set(original.keys) else {
        throw SyncFailure(message: "final output Sync inventory differs from baseline")
    }
    var finalValues: [String: Int] = [:]
    for (name, control) in finalControls {
        guard control.description == descriptions[name] else { throw SyncFailure(message: "final output Sync description differs from baseline: \(name)") }
        finalValues[name] = control.value
        guard control.value == desired[name] else { throw SyncFailure(message: "final output Sync readback differs from requested state: \(name)") }
    }
    guard var result = receipt else { throw SyncFailure(message: "receipt state was lost") }
    result["status"] = "complete"
    result["phase"] = "complete"
    result["error"] = NSNull()
    result["settings_window"] = ["title": "Settings"]
    result["observed_elements_before"] = observedElements
    result["observed_elements_after"] = finalElementCount
    result["observed_windows_before"] = observedWindows
    result["observed_windows_after"] = finalWindowCount
    result["observed_values"] = finalValues
    result["manual_inspection_required"] = false
    receipt = result
    return result
}


// Input mode deliberately uses the same bounded AX read/press/focus primitives above.
// Its separate complete inventory protects every Settings checkbox, including outputs.
let inputFormat = "codex-devtools-live-midi-inputs-baseline-v1"
let inputFlags = Set(["Track", "Sync", "Remote Control"])

func inputInventory(_ app: AXUIElement) throws -> ([String: Any], AXUIElement, [String: AXUIElement]) {
    let process = try targetCheck(options.pid, bundleID: options.bundleID)
    guard let launch = process.launchDate, let name = process.localizedName, !name.isEmpty else {
        throw SyncFailure(message: "input target launch identity is unavailable")
    }
    guard let windows = try axValue(app, kAXWindowsAttribute as String) as? [AXUIElement],
          !windows.isEmpty, windows.count <= maximumWindows else {
        throw SyncFailure(message: "complete input Settings window inventory unavailable")
    }
    let settings = try windows.filter { try requiredString($0, kAXTitleAttribute as String) == "Settings" }
    guard settings.count == 1 else { throw SyncFailure(message: "unique Settings window required") }
    var queue: [(AXUIElement, Int)] = [(settings[0], 0)]
    var seen = Set<CFHashCode>()
    var controls: [String: AXUIElement] = [:]
    var checks: [[String: Any]] = []
    var groups: [String: Set<String>] = [:]
    var order: [String] = []
    var descriptions = Set<String>()
    var visited = 0
    while !queue.isEmpty {
        let (element, depth) = queue.removeFirst()
        let hash = CFHash(element)
        guard seen.insert(hash).inserted, visited < maximumElements else {
            throw SyncFailure(message: "duplicate AX identity or incomplete input tree")
        }
        let index = visited
        visited += 1
        let role = try requiredString(element, kAXRoleAttribute as String)
        let optionalDescription = try axValue(element, kAXDescriptionAttribute as String, allowUnsupported: role != (kAXCheckBoxRole as String)) as? String
        if role != (kAXCheckBoxRole as String), optionalDescription?.contains(": MIDI Input:") == true {
            throw SyncFailure(message: "input description on unexpected non-checkbox role")
        }
        if role == (kAXCheckBoxRole as String) {
            guard let description = optionalDescription, !description.isEmpty,
                  descriptions.insert(description).inserted, checks.count < 512 else {
                throw SyncFailure(message: "checkbox description unknown, duplicate or over bound")
            }
            let value = try checkboxValue(element)
            let isEnabled = try enabled(element)
            let identity = String(hash)
            checks.append(["index": index, "identity": identity, "description": description,
                           "role": role, "enabled": isEnabled, "value": value])
            controls[identity] = element
            if description.contains(": MIDI Input:") {
                let parts = description.components(separatedBy: ": MIDI Input: ")
                guard parts.count == 2, !parts[0].isEmpty,
                      parts[0] == parts[0].trimmingCharacters(in: .whitespacesAndNewlines),
                      inputFlags.contains(parts[1]),
                      isEnabled || value == 0 else {
                    throw SyncFailure(message: "ambiguous or unmodifiable input checkbox")
                }
                if groups[parts[0]] == nil { groups[parts[0]] = []; order.append(parts[0]) }
                guard groups[parts[0]]!.insert(parts[1]).inserted else {
                    throw SyncFailure(message: "duplicate input flag")
                }
            }
        }
        let children = try childElements(element)
        guard depth < maximumDepth || children.isEmpty,
              visited + queue.count + children.count <= maximumElements else {
            throw SyncFailure(message: "input Settings tree exceeds bounds")
        }
        queue.append(contentsOf: children.map { ($0, depth + 1) })
    }
    guard groups.count == options.expectedCount,
          groups.values.allSatisfy({ $0 == inputFlags }) else {
        throw SyncFailure(message: "input groups are missing, partial or differ from caller count")
    }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let result: [String: Any] = [
        "format": inputFormat, "status": "observed", "read_only": true,
        "observed_at_utc": formatter.string(from: Date()),
        "target": ["pid": Int(options.pid), "bundle_identifier": options.bundleID,
                   "process_name": name, "launch_date": formatter.string(from: launch)],
        "settings_window": ["title": "Settings", "identity": String(CFHash(settings[0]))],
        "expected_input_count": options.expectedCount, "input_order": order,
        "bounds": ["max_elements": maximumElements, "max_depth": maximumDepth,
                   "max_windows": maximumWindows, "elements_observed": visited,
                   "windows_observed": windows.count, "truncated": false],
        "checkboxes": checks,
    ]
    return (result, settings[0], controls)
}

func inputJSON(_ path: String) throws -> (Data, [String: Any]) {
    let url = URL(fileURLWithPath: path)
    let attrs = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
    guard attrs.isRegularFile == true, attrs.isSymbolicLink != true else {
        throw SyncFailure(message: "input baseline/state must be a regular file")
    }
    let data = try Data(contentsOf: url)
    guard data.count <= 1_000_000,
          let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw SyncFailure(message: "input baseline/state is invalid or over bound")
    }
    return (data, root)
}

func inputRows(_ value: [String: Any]) throws -> [[String: Any]] {
    guard value["format"] as? String == inputFormat,
          value["status"] as? String == "observed", value["read_only"] as? Bool == true,
          value["expected_input_count"] as? Int == options.expectedCount,
          let rows = value["checkboxes"] as? [[String: Any]], !rows.isEmpty, rows.count <= 512 else {
        throw SyncFailure(message: "invalid complete input schema")
    }
    return rows
}

func inputEqual(_ first: Any?, _ second: Any?) -> Bool {
    guard let first, let second else { return false }
    return NSDictionary(dictionary: ["v": first]).isEqual(to: ["v": second])
}

func actionDeadlineAllowsPress(
    _ deadlineUnixSeconds: TimeInterval,
    nowUnixSeconds: TimeInterval = Date().timeIntervalSince1970
) -> Bool {
    pressDeadlineIsValid(deadlineUnixSeconds, nowUnixSeconds: nowUnixSeconds)
}

func compareInputs(_ original: [String: Any], _ current: [String: Any], values: [String: Int]? = nil) throws {
    for key in ["target", "settings_window", "bounds", "input_order", "expected_input_count"] {
        guard inputEqual(original[key], current[key]) else {
            throw SyncFailure(message: "complete input inventory changed: \(key)")
        }
    }
    let before = try inputRows(original)
    let after = try inputRows(current)
    guard before.count == after.count else { throw SyncFailure(message: "checkbox inventory changed") }
    for (a, b) in zip(before, after) {
        for key in ["identity", "index", "description", "role", "enabled"] {
            guard inputEqual(a[key], b[key]) else { throw SyncFailure(message: "checkbox identity/order/role changed") }
        }
        guard let identity = a["identity"] as? String,
              let expected = values?[identity] ?? (a["value"] as? Int),
              b["value"] as? Int == expected else {
            throw SyncFailure(message: "checkbox value changed outside known actions")
        }
    }
}

func historicalRestoreValues(_ original: [String: Any], _ current: [String: Any]) throws -> [String: Int] {
    let before = try inputRows(original)
    let after = try inputRows(current)
    guard before.count == after.count else {
        throw SyncFailure(message: "historical current checkbox inventory changed")
    }
    var values: [String: Int] = [:]
    for (a, b) in zip(before, after) {
        guard let identity = a["identity"] as? String,
              let originalValue = a["value"] as? Int,
              let value = b["value"] as? Int,
              let description = a["description"] as? String else {
            throw SyncFailure(message: "historical current checkbox is malformed")
        }
        let mutable = description.contains(": MIDI Input: ") && originalValue == 1
        guard mutable ? [0, 1].contains(value) : value == originalValue else {
            throw SyncFailure(message: "historical current inventory differs outside originally-on MIDI inputs")
        }
        values[identity] = value
    }
    try compareInputs(original, current, values: values)
    return values
}

func runInputs() throws -> [String: Any] {
    guard AXIsProcessTrusted() else { throw SyncFailure(message: "Accessibility is not trusted; no prompt requested") }
    let app = AXUIElementCreateApplication(options.pid)
    let (initial, _, _) = try inputInventory(app)
    if options.inputMode == "inspect" { return initial }
    let (data, baseline) = try inputJSON(options.baselinePath)
    guard SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == options.baselineHash else {
        throw SyncFailure(message: "baseline changed after wrapper validation")
    }
    let parser = ISO8601DateFormatter()
    parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let stamp = baseline["observed_at_utc"] as? String, let date = parser.date(from: stamp) else {
        throw SyncFailure(message: "invalid baseline time")
    }
    let historicalRestore = !options.historicalCurrentPath.isEmpty
    if !historicalRestore { try validateBaselineFreshness(date) }
    let (_, expected) = try inputJSON(options.inputExpected)
    let baselineRows = try inputRows(baseline)
    let expectedRows = try inputRows(expected)
    guard baselineRows.count == expectedRows.count else { throw SyncFailure(message: "expected inventory changed") }
    var permitted: [String: Int] = [:]
    var targetOriginal: [String: Any]?
    for (original, row) in zip(baselineRows, expectedRows) {
        guard let identity = original["identity"] as? String, permitted[identity] == nil,
              let originalValue = original["value"] as? Int, [0, 1].contains(originalValue),
              let value = row["value"] as? Int, [0, 1].contains(value),
              let description = original["description"] as? String else {
            throw SyncFailure(message: "unknown or duplicate baseline checkbox")
        }
        let mutable = description.contains(": MIDI Input: ") && originalValue == 1
        guard mutable || value == originalValue else {
            throw SyncFailure(message: "non-target baseline value changed")
        }
        permitted[identity] = value
        if identity == options.inputIdentity { targetOriginal = original }
    }
    try compareInputs(baseline, expected, values: permitted)
    var historicalDate: Date?
    if historicalRestore {
        let (currentData, historicalCurrent) = try inputJSON(options.historicalCurrentPath)
        let currentHash = SHA256.hash(data: currentData).map { String(format: "%02x", $0) }.joined()
        guard currentHash == options.historicalCurrentHash else {
            throw SyncFailure(message: "historical current inventory changed after wrapper validation")
        }
        guard let stamp = historicalCurrent["observed_at_utc"] as? String,
              let currentDate = parser.date(from: stamp) else {
            throw SyncFailure(message: "historical current observation time is invalid")
        }
        try validateBaselineFreshness(currentDate)
        _ = try historicalRestoreValues(baseline, historicalCurrent)
        try compareInputs(historicalCurrent, expected, values: permitted)
        historicalDate = currentDate
    }
    try compareInputs(expected, initial)
    guard let target = targetOriginal,
          let description = target["description"] as? String, description.contains(": MIDI Input: "),
          target["value"] as? Int == 1, target["enabled"] as? Bool == true,
          permitted[options.inputIdentity] != options.inputDesired,
          options.inputDesired == (options.operation == "isolate" ? 0 : 1) else {
        throw SyncFailure(message: "press target is not an originally on input flag")
    }
    // Re-enumerate after baseline parsing and before every single explicit press.
    let (fresh, settings, controls) = try inputInventory(app)
    try compareInputs(expected, fresh)
    let process = try targetCheck(options.pid, bundleID: options.bundleID)
    guard process.isActive, NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid,
          try focusedSettingsWindow(app, expected: settings),
          let control = controls[options.inputIdentity], try hasPressAction(control) else {
        throw SyncFailure(message: "exact input Settings control is not focused/enabled/pressable")
    }
    // Read all values once more; unavailable values stay errors rather than becoming Off.
    for row in try inputRows(fresh) {
        let identity = row["identity"] as! String
        guard let element = controls[identity], try checkboxValue(element) == permitted[identity] else {
            throw SyncFailure(message: "checkbox changed during prepress readback")
        }
    }
    let finalProcess = try targetCheck(options.pid, bundleID: options.bundleID)
    guard finalProcess.launchDate == process.launchDate, finalProcess.isActive,
          NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid,
          try focusedSettingsWindow(app, expected: settings), try enabled(control),
          try checkboxValue(control) == permitted[options.inputIdentity],
          try hasPressAction(control) else {
        throw SyncFailure(message: "input target/focus/state changed immediately before press")
    }
    try validateBaselineFreshness(historicalDate ?? date)
    guard actionDeadlineAllowsPress(options.deadlineUnixSeconds) else {
        throw SyncFailure(message: "bounded input press deadline exceeded")
    }
    phase = "input-press-outcome-unknown"
    AXUIElementSetMessagingTimeout(control, 2.0)
    guard actionDeadlineAllowsPress(options.deadlineUnixSeconds) else {
        throw SyncFailure(message: "bounded input press deadline exceeded")
    }
    guard AXUIElementPerformAction(control, kAXPressAction as CFString) == .success else {
        throw SyncFailure(message: "input AXPress failed; outcome unknown")
    }
    let (after, _, _) = try inputInventory(app)
    permitted[options.inputIdentity] = options.inputDesired
    try compareInputs(expected, after, values: permitted)
    return after
}

func emit(_ value: [String: Any], exitCode: Int32) -> Never {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]),
          let text = String(data: data, encoding: .utf8) else {
        fputs("native Live MIDI Sync helper could not encode its receipt\n", stderr)
        exit(2)
    }
    print(text)
    exit(exitCode)
}

do {
    options = try parseOptions()
    let result = try options.inputMode.isEmpty ? run() : runInputs()
    emit(result, exitCode: 0)
} catch {
    if !options.inputMode.isEmpty {
        emit(["format": "codex-devtools-live-midi-inputs-native-error-v1",
              "status": "stopped", "phase": phase,
              "error": String(describing: error)], exitCode: 1)
    }
    if var current = receipt {
        current["status"] = "stopped"
        current["phase"] = phase
        current["error"] = String(describing: error)
        let actions = current["actions"] as? [[String: Any]] ?? []
        current["manual_inspection_required"] = actions.contains { ($0["attempted"] as? Bool) != false }
        receipt = current
        emit(current, exitCode: 1)
    }
    let fallback: [String: Any] = [
        "format": receiptFormat,
        "status": "stopped",
        "phase": phase,
        "operation": options.operation,
        "target": ["pid": options.pid, "bundle_identifier": options.bundleID],
        "baseline_sha256": options.baselineHash,
        "expected_output_count": options.expectedCount,
        "actions": [],
        "manual_inspection_required": true,
        "error": String(describing: error),
    ]
    emit(fallback, exitCode: 1)
}
