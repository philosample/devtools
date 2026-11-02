// Bounded native Accessibility control for one exact Ableton Live Set transport.
import ApplicationServices
import AppKit
import CoreGraphics
import CryptoKit
import Darwin
import Foundation

let maxWindows = 64
let maxElements = 1500
let maxDepth = 12
let outputSuffix = ": MIDI Output: Sync"
let receiptFormat = "codex-devtools-live-transport-receipt-v1"

struct Options {
    var pid: pid_t = 0
    var bundleID = ""
    var setPath = ""
    var action = "inspect"
    var execute = false
    var playForSeconds = 0
    var expectedCount = 0
    var baselinePath = ""
    var baselineHash = ""
}

struct TransportFailure: Error {
    let phase: String
    let message: String
}

struct Control {
    let name: String
    let element: AXUIElement
    let value: Int?
}

var options = Options()
var phase = "arguments"
var actions: [String: [String: Any]] = [:]
var receipt: [String: Any] = [:]
var manualInspectionRequired = false
var canonicalSetURL: URL?

func require(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {
    if try !condition() { throw TransportFailure(phase: phase, message: message) }
}

func parseOptions() throws -> Options {
    var result = Options()
    var index = 1
    let args = CommandLine.arguments
    while index < args.count {
        let key = args[index]
        if key == "--help" {
            print("Usage: live_transport.swift --pid PID --bundle-id ID --set /path/Set.als [--execute --action play --play-for-seconds 1..15 --expected-count N --baseline FILE --baseline-sha256 SHA256 | --execute --action stop]")
            exit(0)
        }
        if key == "--execute" {
            result.execute = true
            index += 1
            continue
        }
        guard index + 1 < args.count else {
            throw TransportFailure(phase: "arguments", message: "missing value for \(key)")
        }
        let value = args[index + 1]
        switch key {
        case "--pid":
            guard let parsed = Int32(value), parsed > 0 else {
                throw TransportFailure(phase: "arguments", message: "PID must be positive")
            }
            result.pid = pid_t(parsed)
        case "--bundle-id": result.bundleID = value
        case "--set": result.setPath = value
        case "--action": result.action = value
        case "--play-for-seconds":
            guard let parsed = Int(value), (1...15).contains(parsed) else {
                throw TransportFailure(phase: "arguments", message: "play duration must be an integer from 1 to 15 seconds")
            }
            result.playForSeconds = parsed
        case "--expected-count":
            guard let parsed = Int(value), (1...128).contains(parsed) else {
                throw TransportFailure(phase: "arguments", message: "expected output count must be 1–128")
            }
            result.expectedCount = parsed
        case "--baseline": result.baselinePath = value
        case "--baseline-sha256": result.baselineHash = value
        default:
            throw TransportFailure(phase: "arguments", message: "unknown option: \(key)")
        }
        index += 2
    }
    guard result.pid > 0, !result.bundleID.isEmpty, !result.setPath.isEmpty,
          ["inspect", "play", "stop"].contains(result.action) else {
        throw TransportFailure(phase: "arguments", message: "PID, bundle ID, exact Set path and a supported action are required")
    }
    if result.action == "inspect" {
        guard !result.execute else { throw TransportFailure(phase: "arguments", message: "inspect is read-only") }
    } else {
        guard result.execute else { throw TransportFailure(phase: "arguments", message: "mutating actions require --execute") }
    }
    if result.action == "play" {
        guard (1...15).contains(result.playForSeconds), result.expectedCount > 0,
              !result.baselinePath.isEmpty, result.baselineHash.count == 64 else {
            throw TransportFailure(phase: "arguments", message: "play requires a 1–15 second duration, output count and checked baseline")
        }
    } else {
        guard result.playForSeconds == 0 else {
            throw TransportFailure(phase: "arguments", message: "play duration is valid only for play")
        }
    }
    return result
}

func axValue(_ element: AXUIElement, _ attribute: String) throws -> CFTypeRef? {
    AXUIElementSetMessagingTimeout(element, 2.0)
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    if error == .success { return value }
    if error == .noValue { return nil }
    throw TransportFailure(phase: phase, message: "AX read \(attribute) failed (\(error.rawValue))")
}

func stringAttribute(_ element: AXUIElement, _ attribute: String) throws -> String {
    guard let raw = try axValue(element, attribute), let value = raw as? String else {
        throw TransportFailure(phase: phase, message: "required AX string is unavailable: \(attribute)")
    }
    return value
}

func role(_ element: AXUIElement) throws -> String {
    try stringAttribute(element, kAXRoleAttribute as String)
}

func optionalString(_ element: AXUIElement, _ attribute: String) -> String? {
    (try? axValue(element, attribute)) as? String
}

func children(_ element: AXUIElement) throws -> [AXUIElement] {
    guard let raw = try axValue(element, kAXChildrenAttribute as String) else { return [] }
    guard let result = raw as? [AXUIElement] else {
        throw TransportFailure(phase: phase, message: "AXChildren is not an element array")
    }
    return result
}

func elementTree(_ roots: [AXUIElement]) throws -> [AXUIElement] {
    var queue = roots.map { ($0, 0) }
    var output: [AXUIElement] = []
    var seen = Set<CFHashCode>()
    while !queue.isEmpty {
        let (element, depth) = queue.removeFirst()
        guard seen.insert(CFHash(element)).inserted else { continue }
        output.append(element)
        try require(output.count <= maxElements, "AX tree exceeds element bound")
        let nested = try children(element)
        if depth < maxDepth {
            if output.count + queue.count + nested.count > maxElements {
                throw TransportFailure(phase: phase, message: "AX tree would exceed element bound")
            }
            queue.append(contentsOf: nested.map { ($0, depth + 1) })
        } else if !nested.isEmpty {
            throw TransportFailure(phase: phase, message: "AX tree exceeds depth bound")
        }
    }
    return output
}

func checkboxValue(_ element: AXUIElement) throws -> Int {
    guard try role(element) == (kAXCheckBoxRole as String),
          let raw = try axValue(element, kAXValueAttribute as String),
          let number = raw as? NSNumber else {
        throw TransportFailure(phase: phase, message: "required transport checkbox value is unreadable")
    }
    let value = number.intValue
    guard value == 0 || value == 1, number.doubleValue == Double(value) else {
        throw TransportFailure(phase: phase, message: "transport checkbox value is not 0 or 1")
    }
    return value
}

func enabled(_ element: AXUIElement) throws -> Bool {
    guard let raw = try axValue(element, kAXEnabledAttribute as String),
          let value = raw as? NSNumber,
          value.doubleValue == 0 || value.doubleValue == 1 else {
        throw TransportFailure(phase: phase, message: "transport control enabled state is unreadable")
    }
    return value.boolValue
}

func actionNames(_ element: AXUIElement) throws -> [String] {
    var raw: CFArray?
    AXUIElementSetMessagingTimeout(element, 2.0)
    let error = AXUIElementCopyActionNames(element, &raw)
    guard error == .success, let raw, let names = raw as? [String] else {
        throw TransportFailure(phase: phase, message: "AX action list unavailable (\(error.rawValue))")
    }
    return names
}

func setAction(_ name: String, outcome: String, detail: String? = nil) {
    var item = actions[name] ?? ["attempted": false, "outcome": "not_attempted"]
    item["attempted"] = outcome != "not_attempted" && outcome != "verified_noop"
    item["outcome"] = outcome
    if let detail { item["detail"] = detail }
    actions[name] = item
    manualInspectionRequired = actions.values.contains { $0["outcome"] as? String == "unknown" }
}

func press(_ element: AXUIElement, actionName: String) throws {
    try require(try enabled(element), "exact \(actionName) control is disabled")
    try require(try actionNames(element).contains(kAXPressAction as String), "exact \(actionName) control does not support AXPress")
    setAction(actionName, outcome: "unknown")
    AXUIElementSetMessagingTimeout(element, 2.0)
    let error = AXUIElementPerformAction(element, kAXPressAction as CFString)
    guard error == .success else {
        setAction(actionName, outcome: "unknown", detail: "AXPress returned \(error.rawValue); effect is uncertain")
        throw TransportFailure(phase: phase, message: "AXPress outcome is uncertain for \(actionName)")
    }
}

func exactControls(_ window: AXUIElement) throws -> [String: Control] {
    let identifiers = [
        "Play": "Transport.Play",
        "Stop": "Transport.Stop",
        "Link": "Transport.AbletonLink",
        "Arrangement Record": "Transport.GlobalRecord",
        "Session Record": "Transport.SessionRecord",
    ]
    let tree = try elementTree([window])
    var found: [String: Control] = [:]
    for element in tree {
        let desc = optionalString(element, kAXDescriptionAttribute as String) ?? ""
        guard let expectedID = identifiers[desc] else { continue }
        let expectedRole = desc == "Stop" ? (kAXButtonRole as String) : (kAXCheckBoxRole as String)
        guard try role(element) == expectedRole,
              optionalString(element, "AXIdentifier") == expectedID else {
            throw TransportFailure(phase: phase, message: "transport control role or identity mismatch: \(desc)")
        }
        guard found[desc] == nil else {
            throw TransportFailure(phase: phase, message: "duplicate exact transport control: \(desc)")
        }
        let value: Int? = desc == "Stop" ? nil : try checkboxValue(element)
        guard try enabled(element) else {
            throw TransportFailure(phase: phase, message: "transport control is disabled: \(desc)")
        }
        found[desc] = Control(name: desc, element: element, value: value)
    }
    guard Set(found.keys) == Set(identifiers.keys) else {
        throw TransportFailure(phase: phase, message: "one or more exact transport controls are missing")
    }
    return found
}

func validateControlIdentity(_ element: AXUIElement, name: String) throws {
    let identifiers = [
        "Play": ("AXCheckBox", "Transport.Play"),
        "Stop": ("AXButton", "Transport.Stop"),
        "Link": ("AXCheckBox", "Transport.AbletonLink"),
        "Arrangement Record": ("AXCheckBox", "Transport.GlobalRecord"),
        "Session Record": ("AXCheckBox", "Transport.SessionRecord"),
    ]
    guard let (expectedRole, expectedID) = identifiers[name],
          try role(element) == expectedRole,
          optionalString(element, kAXDescriptionAttribute as String) == name,
          optionalString(element, "AXIdentifier") == expectedID,
          try enabled(element),
          try actionNames(element).contains(kAXPressAction as String) else {
        throw TransportFailure(phase: phase, message: "exact transport control identity, enabled state or AXPress action changed: \(name)")
    }
}

func appWindows(_ app: AXUIElement) throws -> [AXUIElement] {
    guard let raw = try axValue(app, kAXWindowsAttribute as String),
          let windows = raw as? [AXUIElement], !windows.isEmpty, windows.count <= maxWindows else {
        throw TransportFailure(phase: phase, message: "target AX window inventory is unavailable or out of bounds")
    }
    for window in windows {
        let kind = try role(window)
        let subrole = optionalString(window, kAXSubroleAttribute as String) ?? ""
        if kind == "AXDialog" || kind == "AXSheet" || kind == "AXPopover" ||
            ["AXDialog", "AXSystemDialog", "AXFloatingWindow"].contains(subrole) {
            throw TransportFailure(phase: phase, message: "unexpected dialog, sheet or popover is open")
        }
    }
    return windows
}

func lexicalPath(_ path: String) throws -> String {
    let parts = path.split(separator: "/", omittingEmptySubsequences: false)
    guard path.hasPrefix("/"), !path.hasSuffix("/"), parts.count >= 2,
          parts[0].isEmpty, parts.dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }),
          !path.contains("\\"), !path.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else {
        throw TransportFailure(phase: phase, message: "normalized absolute Set path required")
    }
    return path
}

func setURL() throws -> URL {
    let path = try lexicalPath(options.setPath)
    guard path.lowercased().hasSuffix(".als") else {
        throw TransportFailure(phase: phase, message: "exact selected Set path must end in .als")
    }
    var info = stat()
    guard path.withCString({ lstat($0, &info) }) == 0,
          (info.st_mode & S_IFMT) == S_IFREG else {
        throw TransportFailure(phase: phase, message: "selected Set must be an existing regular file, not a symlink")
    }
    let parts = path.split(separator: "/").map(String.init)
    var current = URL(fileURLWithPath: "/")
    for part in parts.dropLast() {
        current.appendPathComponent(part, isDirectory: true)
        var linkInfo = stat()
        guard current.path.withCString({ lstat($0, &linkInfo) }) == 0,
              (linkInfo.st_mode & S_IFMT) == S_IFDIR else {
            throw TransportFailure(phase: phase, message: "Set parent path contains an unavailable directory or symlink")
        }
    }
    return URL(fileURLWithPath: path)
}

func fileURLPath(_ raw: String) throws -> String {
    guard let url = URL(string: raw), url.isFileURL, url.query == nil, url.fragment == nil else {
        throw TransportFailure(phase: phase, message: "AXDocument is not a local file URL")
    }
    return try lexicalPath(url.path)
}

func sameSetURL(_ raw: String, expected: URL) throws -> Bool {
    let reported = try fileURLPath(raw)
    if reported == expected.path { return true }
    let privatePrefix = "/private/tmp/"
    let publicPrefix = "/tmp/"
    let aliasPath: String?
    if expected.path.hasPrefix(privatePrefix) && reported == publicPrefix + String(expected.path.dropFirst(privatePrefix.count)) {
        aliasPath = reported
    } else if reported.hasPrefix(privatePrefix) && expected.path == publicPrefix + String(reported.dropFirst(privatePrefix.count)) {
        aliasPath = reported
    } else {
        aliasPath = nil
    }
    guard let aliasPath else { return false }
    var left = stat()
    var right = stat()
    guard expected.path.withCString({ stat($0, &left) }) == 0,
          aliasPath.withCString({ stat($0, &right) }) == 0 else { return false }
    return left.st_dev == right.st_dev && left.st_ino == right.st_ino
}

func exactSetWindow(_ app: AXUIElement, url: URL) throws -> AXUIElement {
    var matches: [AXUIElement] = []
    for window in try appWindows(app) where try role(window) == (kAXWindowRole as String) {
        guard let document = optionalString(window, kAXDocumentAttribute as String) else { continue }
        if try sameSetURL(document, expected: url) { matches.append(window) }
    }
    try require(matches.count == 1, "exact selected Set URL must identify one AX window")
    return matches[0]
}

func validateWindowDocument(_ window: AXUIElement, url: URL) throws {
    try require(try role(window) == (kAXWindowRole as String), "target AX element is no longer a window")
    guard let raw = optionalString(window, kAXDocumentAttribute as String),
          try sameSetURL(raw, expected: url) else {
        throw TransportFailure(phase: phase, message: "focused AX window no longer identifies the exact selected Set URL")
    }
}

func focusedWindowIs(_ app: AXUIElement, _ window: AXUIElement) -> Bool {
    do {
        guard let value = try axValue(app, kAXFocusedWindowAttribute as String),
              CFGetTypeID(value) == AXUIElementGetTypeID() else { return false }
        return CFEqual(value as! AXUIElement, window)
    } catch {
        return false
    }
}

func frontmost(_ pid: pid_t) -> Bool {
    NSWorkspace.shared.frontmostApplication?.processIdentifier == pid
}

func currentProcessMatches(_ pid: pid_t, bundleID: String) -> Bool {
    guard let current = NSRunningApplication(processIdentifier: pid) else { return false }
    return current.bundleIdentifier == bundleID && !current.isTerminated
}

func waitUntil(_ predicate: () -> Bool, seconds: TimeInterval) -> Bool {
    let deadline = DispatchTime.now().uptimeNanoseconds + UInt64(seconds * 1_000_000_000)
    while DispatchTime.now().uptimeNanoseconds < deadline {
        if predicate() { return true }
        Thread.sleep(forTimeInterval: 0.05)
    }
    return predicate()
}

func onscreenWindow(pid: pid_t, title: String) throws -> Bool {
    let flags: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let entries = CGWindowListCopyWindowInfo(flags, kCGNullWindowID) as? [[String: Any]] else {
        throw TransportFailure(phase: phase, message: "current-desktop on-screen window inventory is unavailable")
    }
    let matches = entries.filter { entry in
        guard (entry[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
              (entry[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
              entry[kCGWindowName as String] as? String == title,
              let bounds = entry[kCGWindowBounds as String] as? [String: NSNumber],
              (bounds["Width"]?.doubleValue ?? 0) > 0,
              (bounds["Height"]?.doubleValue ?? 0) > 0 else { return false }
        return true
    }
    return matches.count == 1
}

func raiseAndFocus(_ app: AXUIElement, pid: pid_t, process: NSRunningApplication, window: AXUIElement, title: String) throws {
    try require(onscreenWindow(pid: pid, title: title), "exact target window is not proven on the current desktop")
    if !frontmost(pid) {
        try require(process.activate(), "exact Live process activation failed")
    }
    try require(waitUntil({ frontmost(pid) }, seconds: 5), "exact Live PID did not become frontmost")
    try require(onscreenWindow(pid: pid, title: title), "exact target window left the current desktop during activation")
    if !focusedWindowIs(app, window) {
        let supported = try actionNames(window)
        try require(supported.contains(kAXRaiseAction as String), "exact target window does not support AXRaise")
        let error = AXUIElementPerformAction(window, kAXRaiseAction as CFString)
        try require(error == .success, "raising the exact target window failed (\(error.rawValue))")
    }
    try require(waitUntil({
        frontmost(pid) && focusedWindowIs(app, window) && ((try? onscreenWindow(pid: pid, title: title)) == true)
    }, seconds: 5), "exact target window did not become focused on the current desktop")
}

func controlsState(_ controls: [String: Control]) -> [String: Any] {
    [
        "play": controls["Play"]?.value as Any? ?? NSNull(),
        "link": controls["Link"]?.value as Any? ?? NSNull(),
        "arrangement_record": controls["Arrangement Record"]?.value as Any? ?? NSNull(),
        "session_record": controls["Session Record"]?.value as Any? ?? NSNull(),
    ]
}

func checkedBaseline() throws -> [String: Any] {
    let url = URL(fileURLWithPath: options.baselinePath)
    var info = stat()
    guard options.baselinePath.withCString({ lstat($0, &info) }) == 0,
          (info.st_mode & S_IFMT) == S_IFREG,
          let data = try? Data(contentsOf: url),
          SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == options.baselineHash,
          let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          root["format"] as? String == "codex-devtools-live-midi-sync-baseline-v1",
          root["read_only"] as? Bool == true,
          root["status"] as? String == "observed",
          let target = root["target"] as? [String: Any],
          (target["pid"] as? NSNumber)?.intValue == Int(options.pid),
          target["bundle_identifier"] as? String == options.bundleID,
          (root["expected_output_count"] as? NSNumber)?.intValue == options.expectedCount,
          let observed = root["observed_at_utc"] as? String,
          let rows = root["outputs"] as? [[String: Any]], rows.count == options.expectedCount else {
        throw TransportFailure(phase: phase, message: "complete exact-PID output Sync baseline is invalid")
    }
    try validateBaselineAge(observed)
    var names = Set<String>()
    for row in rows {
        guard let name = row["name"] as? String, !name.isEmpty,
              row["description"] as? String == name + outputSuffix,
              row["role"] as? String == "AXCheckBox",
              row["enabled"] as? Bool == true,
              (row["value"] as? NSNumber)?.intValue == 0,
              names.insert(name).inserted else {
            throw TransportFailure(phase: phase, message: "output Sync baseline is incomplete, duplicated or not all off")
        }
    }
    guard names.count == options.expectedCount else {
        throw TransportFailure(phase: phase, message: "output Sync baseline count is not exact")
    }
    return root
}

func validateBaselineAge(_ observed: String) throws {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let date = formatter.date(from: observed), Date().timeIntervalSince(date) >= -5,
          Date().timeIntervalSince(date) <= 30 else {
        throw TransportFailure(phase: phase, message: "fresh output Sync baseline is stale or from the future")
    }
}

func validateCurrentOutputs(_ settings: AXUIElement, baseline: [String: Any]) throws {
    let rows = baseline["outputs"] as! [[String: Any]]
    let expected = Dictionary(uniqueKeysWithValues: rows.map { ($0["name"] as! String, $0["description"] as! String) })
    let elements = try elementTree([settings])
    var observed: [String: Int] = [:]
    for element in elements where try role(element) == (kAXCheckBoxRole as String) {
        guard let description = optionalString(element, kAXDescriptionAttribute as String), description.hasSuffix(outputSuffix) else { continue }
        let name = String(description.dropLast(outputSuffix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
        try require(!name.isEmpty && observed[name] == nil, "output Sync inventory has an empty or duplicate row")
        try require(expected[name] == description && (try enabled(element)), "output Sync inventory differs from the complete baseline")
        let value = try checkboxValue(element)
        try require(value == 0, "every output Sync checkbox must remain off before Play")
        observed[name] = value
    }
    try require(Set(observed.keys) == Set(expected.keys), "output Sync inventory names differ from the fresh baseline")
}

func closeSettingsAfterIsolation(_ app: AXUIElement, pid: pid_t, process: NSRunningApplication, settings: AXUIElement, setWindow: AXUIElement, setTitle: String, baseline: [String: Any]) throws -> AXUIElement {
    phase = "settings-isolation"
    try validateCurrentOutputs(settings, baseline: baseline)
    try require(currentProcessMatches(pid, bundleID: options.bundleID), "Live process identity changed before Settings close")
    try require(onscreenWindow(pid: pid, title: setTitle), "exact Set window is not proven on the current desktop")
    try raiseAndFocus(app, pid: pid, process: process, window: settings, title: "Settings")
    try validateBaselineAge(baseline["observed_at_utc"] as! String)
    let closeButtons = try elementTree([settings]).filter {
        (try? role($0)) == (kAXButtonRole as String) && optionalString($0, kAXSubroleAttribute as String) == "AXCloseButton"
    }
    try require(closeButtons.count == 1 && (try enabled(closeButtons[0])), "Settings has no unique enabled AXCloseButton")
    try require(frontmost(pid) && focusedWindowIs(app, settings), "exact Settings window lost focus before close")
    try press(closeButtons[0], actionName: "settings_close")
    let closed = waitUntil({
        guard let windows = try? appWindows(app) else { return false }
        return !windows.contains { optionalString($0, kAXTitleAttribute as String) == "Settings" }
    }, seconds: 5)
    try require(closed, "exact Settings window did not close after AXCloseButton")
    setAction("settings_close", outcome: "known")
    let freshSetWindow = try exactSetWindow(app, url: setURL())
    try require(CFEqual(freshSetWindow, setWindow), "exact Set window identity changed after closing Settings")
    try raiseAndFocus(app, pid: pid, process: process, window: freshSetWindow, title: setTitle)
    return freshSetWindow
}

func emit(_ value: [String: Any], code: Int32) -> Never {
    guard JSONSerialization.isValidJSONObject(value),
          let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]),
          let text = String(data: data, encoding: .utf8) else {
        fputs("native transport helper could not encode its receipt\n", stderr)
        exit(2)
    }
    print(text)
    exit(code)
}

func transportReport(
    status: String,
    phase reportPhase: String,
    action: String,
    readOnly: Bool,
    pid: pid_t,
    bundleID: String,
    processName: String,
    setURL: URL,
    title: String,
    transport: [String: Any]?,
    error: String? = nil
) -> [String: Any] {
    var result: [String: Any] = [
        "format": receiptFormat,
        "status": status,
        "phase": reportPhase,
        "action": action,
        "read_only": readOnly,
        "target": ["pid": pid, "bundle_identifier": bundleID, "process_name": processName],
        "set_url": setURL.absoluteString,
        "set_window_title": title,
        "actions": actions,
        "manual_inspection_required": manualInspectionRequired,
    ]
    if let transport { result["transport"] = transport }
    if let error { result["error"] = error }
    if action == "play" { result["play_for_seconds"] = options.playForSeconds }
    return result
}

func run() throws -> [String: Any] {
    phase = "identity"
    guard AXIsProcessTrusted() else {
        throw TransportFailure(phase: phase, message: "Accessibility is not trusted; no permission prompt was requested")
    }
    let pathURL = try setURL()
    canonicalSetURL = pathURL
    guard let process = NSRunningApplication(processIdentifier: options.pid),
          process.bundleIdentifier == options.bundleID, !process.isTerminated else {
        throw TransportFailure(phase: phase, message: "exact Live PID or bundle identity is unavailable")
    }
    let app = AXUIElementCreateApplication(options.pid)
    var setWindow = try exactSetWindow(app, url: pathURL)
    let setTitle = try stringAttribute(setWindow, kAXTitleAttribute as String)
    var controls = try exactControls(setWindow)
    let initialState = controlsState(controls)

    if options.action == "inspect" {
        return transportReport(
            status: "observed", phase: "complete", action: "inspect", readOnly: true,
            pid: options.pid, bundleID: options.bundleID, processName: process.localizedName ?? "",
            setURL: pathURL, title: setTitle, transport: initialState
        )
    }

    actions = [
        "settings_close": ["attempted": false, "outcome": "not_attempted"],
        "play": ["attempted": false, "outcome": "not_attempted"],
        "stop": ["attempted": false, "outcome": "not_attempted"],
    ]
    receipt = transportReport(
        status: "stopped", phase: phase, action: options.action, readOnly: false,
        pid: options.pid, bundleID: options.bundleID, processName: process.localizedName ?? "",
        setURL: pathURL, title: setTitle, transport: initialState
    )

    if options.action == "stop" {
        phase = "stop-preflight"
        try raiseAndFocus(app, pid: options.pid, process: process, window: setWindow, title: setTitle)
        controls = try exactControls(try exactSetWindow(app, url: pathURL))
        try require(currentProcessMatches(options.pid, bundleID: options.bundleID), "Live process identity changed before Stop")
        try require(frontmost(options.pid) && focusedWindowIs(app, setWindow), "exact Set window lost focus before Stop")
        try validateControlIdentity(controls["Stop"]!.element, name: "Stop")
        let playValue = try checkboxValue(controls["Play"]!.element)
        if playValue == 0 {
            setAction("stop", outcome: "verified_noop", detail: "Play was already off")
        } else {
            try require(frontmost(options.pid) && focusedWindowIs(app, setWindow), "exact Set window lost focus immediately before Stop")
            try press(controls["Stop"]!.element, actionName: "stop")
            let freshWindow = try exactSetWindow(app, url: pathURL)
            try require(CFEqual(freshWindow, setWindow) && currentProcessMatches(options.pid, bundleID: options.bundleID) && frontmost(options.pid) && focusedWindowIs(app, freshWindow), "exact Set focus changed during Stop")
            let after = try exactControls(freshWindow)
            try require(after["Play"]?.value == 0, "Stop did not read back Play off")
            setAction("stop", outcome: "known")
        }
        return transportReport(
            status: "complete", phase: "complete", action: "stop", readOnly: false,
            pid: options.pid, bundleID: options.bundleID, processName: process.localizedName ?? "",
            setURL: pathURL, title: setTitle, transport: controlsState(try exactControls(try exactSetWindow(app, url: pathURL)))
        )
    }

    phase = "play-baseline"
    let baseline = try checkedBaseline()
    let observedAt = baseline["observed_at_utc"] as! String
    let settingsMatches = try appWindows(app).filter {
        try role($0) == (kAXWindowRole as String) && optionalString($0, kAXTitleAttribute as String) == "Settings"
    }
    try require(settingsMatches.count == 1, "Play requires exactly one open Settings window for output isolation inspection")
    let settingsWindow = settingsMatches[0]
    try validateCurrentOutputs(settingsWindow, baseline: baseline)
    try require(initialState["play"] as? Int == 0, "Play preflight requires stopped transport")
    try require(initialState["link"] as? Int == 0, "Play preflight requires Link off")
    try require(initialState["arrangement_record"] as? Int == 0, "Play preflight requires Arrangement Record off")
    try require(initialState["session_record"] as? Int == 0, "Play preflight requires Session Record off")

    setWindow = try closeSettingsAfterIsolation(
        app, pid: options.pid, process: process, settings: settingsWindow,
        setWindow: setWindow, setTitle: setTitle, baseline: baseline
    )
    try validateBaselineAge(observedAt)
    controls = try exactControls(setWindow)
    try require(controls["Play"]?.value == 0, "Play preflight requires stopped transport")
    try require(controls["Link"]?.value == 0 && controls["Arrangement Record"]?.value == 0 && controls["Session Record"]?.value == 0,
                "Link or recording state changed before Play")
    try require(frontmost(options.pid) && focusedWindowIs(app, setWindow), "exact Set window is not foreground and focused before Play")
    try require(onscreenWindow(pid: options.pid, title: setTitle), "exact Set window is no longer proven on the current desktop")
    try require(currentProcessMatches(options.pid, bundleID: options.bundleID), "Live process identity changed before Play")
    try validateWindowDocument(setWindow, url: pathURL)
    try validateBaselineAge(observedAt)
    let playBeforePress = try checkboxValue(controls["Play"]!.element)
    let linkBeforePress = try checkboxValue(controls["Link"]!.element)
    let arrangementBeforePress = try checkboxValue(controls["Arrangement Record"]!.element)
    let sessionBeforePress = try checkboxValue(controls["Session Record"]!.element)
    try require(playBeforePress == 0, "Play must still be off immediately before AXPress")
    try require(linkBeforePress == 0 && arrangementBeforePress == 0 && sessionBeforePress == 0,
                "Link or recording state changed immediately before Play")
    try require(frontmost(options.pid) && focusedWindowIs(app, setWindow), "exact Set window lost focus immediately before Play")

    let start = DispatchTime.now().uptimeNanoseconds
    let deadline = start + UInt64(options.playForSeconds) * 1_000_000_000
    phase = "play"
    let playControl = controls["Play"]!.element
    do {
        try press(playControl, actionName: "play")
        let playValue = try checkboxValue(playControl)
        guard playValue == 1 else {
            setAction("play", outcome: "unknown", detail: "Play press returned success but did not read back on")
            throw TransportFailure(phase: phase, message: "Play did not read back on")
        }
        setAction("play", outcome: "known")
    } catch {
        // Once Play is attempted, the independent deadline Stop still runs even
        // if the AX call or immediate readback has an uncertain outcome.
        receipt["play_error"] = String(describing: error)
    }
    if actions["play"]?["attempted"] as? Bool != true {
        receipt["status"] = "stopped"
        receipt["phase"] = "play"
        receipt["error"] = receipt["play_error"] ?? "Play was not attempted"
        receipt["actions"] = actions
        receipt["manual_inspection_required"] = manualInspectionRequired
        return receipt
    }
    receipt["play_started_at_monotonic_ns"] = start
    receipt["stop_deadline_monotonic_ns"] = deadline

    let now = DispatchTime.now().uptimeNanoseconds
    if now < deadline {
        Thread.sleep(forTimeInterval: Double(deadline - now) / 1_000_000_000)
    }

    phase = "timed-stop"
    do {
        _ = try appWindows(app)
        try require(frontmost(options.pid), "Live lost foreground before timed Stop")
        try require(currentProcessMatches(options.pid, bundleID: options.bundleID), "Live process identity changed before timed Stop")
        let stopWindow = setWindow
        try require(CFEqual(stopWindow, setWindow) && focusedWindowIs(app, stopWindow), "exact Set focus changed before timed Stop")
        try validateWindowDocument(stopWindow, url: pathURL)
        try require(onscreenWindow(pid: options.pid, title: setTitle), "exact Set window is not on the current desktop at Stop deadline")
        let beforeStop = controls
        try validateControlIdentity(beforeStop["Stop"]!.element, name: "Stop")
        let linkAtStop = try checkboxValue(beforeStop["Link"]!.element)
        let arrangementAtStop = try checkboxValue(beforeStop["Arrangement Record"]!.element)
        let sessionAtStop = try checkboxValue(beforeStop["Session Record"]!.element)
        receipt["transport_before_stop"] = [
            "play": try checkboxValue(beforeStop["Play"]!.element),
            "link": linkAtStop,
            "arrangement_record": arrangementAtStop,
            "session_record": sessionAtStop,
        ]
        try require(frontmost(options.pid) && focusedWindowIs(app, stopWindow), "exact Set window lost focus before timed Stop")
        try press(beforeStop["Stop"]!.element, actionName: "stop")
        let afterWindow = setWindow
        try require(CFEqual(afterWindow, setWindow) && frontmost(options.pid) && focusedWindowIs(app, afterWindow), "exact Set focus changed after timed Stop")
        try validateWindowDocument(afterWindow, url: pathURL)
        _ = try appWindows(app)
        try require(try checkboxValue(controls["Play"]!.element) == 0, "timed Stop did not read back Play off")
        let linkAfterStop = try checkboxValue(controls["Link"]!.element)
        let arrangementAfterStop = try checkboxValue(controls["Arrangement Record"]!.element)
        let sessionAfterStop = try checkboxValue(controls["Session Record"]!.element)
        setAction("stop", outcome: "known")
        if linkAtStop != 0 || arrangementAtStop != 0 || sessionAtStop != 0
            || linkAfterStop != 0 || arrangementAfterStop != 0 || sessionAfterStop != 0 {
            manualInspectionRequired = true
        }
        receipt["transport"] = [
            "play": 0,
            "link": linkAfterStop,
            "arrangement_record": arrangementAfterStop,
            "session_record": sessionAfterStop,
        ]
        receipt["status"] = "complete"
        receipt["phase"] = "complete"
    } catch {
        let stopWasAttempted = actions["stop"]?["attempted"] as? Bool == true
        if stopWasAttempted {
            setAction("stop", outcome: "unknown", detail: String(describing: error))
        } else {
            setAction("stop", outcome: "not_attempted", detail: String(describing: error))
            manualInspectionRequired = true
        }
        receipt["status"] = "stopped"
        receipt["phase"] = phase
        receipt["error"] = String(describing: error)
        receipt["transport"] = ["play": NSNull(), "link": NSNull(), "arrangement_record": NSNull(), "session_record": NSNull()]
    }
    if actions["play"]?["outcome"] as? String != "known" && receipt["status"] as? String == "complete" {
        receipt["status"] = "stopped"
        receipt["phase"] = "play-outcome-unknown"
        receipt["error"] = receipt["play_error"] ?? "Play outcome could not be confirmed"
    }
    receipt["actions"] = actions
    receipt["manual_inspection_required"] = manualInspectionRequired
    return receipt
}

do {
    options = try parseOptions()
    canonicalSetURL = URL(fileURLWithPath: options.setPath)
    if options.execute {
        actions = [
            "settings_close": ["attempted": false, "outcome": "not_attempted"],
            "play": ["attempted": false, "outcome": "not_attempted"],
            "stop": ["attempted": false, "outcome": "not_attempted"],
        ]
    }
    let result = try run()
    let success = result["status"] as? String == "complete" || result["status"] as? String == "observed"
    emit(result, code: success ? 0 : 1)
} catch {
    let attempted = actions.values.contains { ($0["outcome"] as? String) != "not_attempted" && ($0["outcome"] as? String) != "verified_noop" }
    manualInspectionRequired = manualInspectionRequired || actions.values.contains { $0["outcome"] as? String == "unknown" }
    if options.action == "play",
       actions["play"]?["outcome"] as? String == "known",
       actions["stop"]?["outcome"] as? String != "known" {
        manualInspectionRequired = true
    }
    var result = receipt
    result["format"] = receiptFormat
    result["status"] = "stopped"
    result["phase"] = (error as? TransportFailure)?.phase ?? phase
    result["action"] = options.action
    result["read_only"] = !options.execute
    result["target"] = ["pid": options.pid, "bundle_identifier": options.bundleID]
    result["set_url"] = canonicalSetURL?.absoluteString ?? options.setPath
    result["actions"] = actions
    result["manual_inspection_required"] = manualInspectionRequired
    result["error"] = String(describing: error)
    if options.action == "play" { result["play_for_seconds"] = options.playForSeconds }
    if !attempted { result["manual_inspection_required"] = false }
    emit(result, code: 1)
}
