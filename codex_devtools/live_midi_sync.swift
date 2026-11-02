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
    var pid: pid_t = 0
    var bundleID = ""
    var expectedCount = 0
    var operation = ""
    var baselinePath = ""
    var baselineHash = ""
    var maximumBaselineAgeSeconds = 300.0
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

func parseOptions() throws -> Options {
    var result = Options()
    var index = 1
    let args = CommandLine.arguments
    while index < args.count {
        let key = args[index]
        guard index + 1 < args.count else { throw SyncFailure(message: "missing value for \(key)") }
        let value = args[index + 1]
        switch key {
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
        case "--max-baseline-age-seconds":
            guard let parsed = Double(value), parsed.isFinite, parsed > 0, parsed <= 300 else {
                throw SyncFailure(message: "maximum baseline age must be positive and at most 300 seconds")
            }
            result.maximumBaselineAgeSeconds = parsed
        default: throw SyncFailure(message: "unknown option: \(key)")
        }
        index += 2
    }
    guard result.pid > 0, !result.bundleID.isEmpty, result.expectedCount > 0,
          ["isolate", "restore"].contains(result.operation),
          !result.baselinePath.isEmpty, result.baselineHash.count == 64,
          result.maximumBaselineAgeSeconds > 0 else {
        throw SyncFailure(message: "incomplete native MIDI Sync arguments")
    }
    return result
}

func axValue(_ element: AXUIElement, _ attribute: String) throws -> CFTypeRef? {
    AXUIElementSetMessagingTimeout(element, 2.0)
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    if error == .success { return value }
    if error == .noValue { return nil }
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
    guard process.isActive, NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid,
          try focusedSettingsWindow(app, expected: settings) else {
        throw SyncFailure(message: "exact Live Settings window is not foreground and focused")
    }
    phase = "actions"
    for name in candidates {
        guard try targetCheck(options.pid, bundleID: options.bundleID).isActive,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid,
              try focusedSettingsWindow(app, expected: settings) else {
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
        guard try targetCheck(options.pid, bundleID: options.bundleID).isActive,
              NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid,
              try focusedSettingsWindow(app, expected: freshSettings) else {
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
    let result = try run()
    emit(result, exitCode: 0)
} catch {
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
