// Narrow AX path for opening Ableton Live Settings on its Tempo & MIDI tab.
import ApplicationServices
import AppKit
import CoreGraphics
import Foundation

let maxWindows = 64
let maxElements = 512
let maxDepth = 16
let menuMaxElements = 128
let menuMaxDepth = 8
let bundleIDRequired = "com.ableton.live"
let settingsMenuTitle = "Settings..."
let settingsWindowTitle = "Settings"
let tempoMidiTabIdentifier = "midiTabButton"

struct Options {
    var pid: pid_t = 0
    var bundleID = ""
    var windowTitle = ""
    var expectedGeneration: Int64?
    var execute = false
}

struct SettingsFailure: Error {
    let message: String
}

var options = Options()
var phase = "arguments"
var processGeneration: Int64?
var actions: [String: String] = [
    "activate": "not_attempted",
    "raise_source_window": "not_attempted",
    "settings_menu": "not_attempted",
    "tempo_midi_tab": "not_attempted",
]
var sourceWindowOnDesktop = false
var sourceWindowNumber: Int?
var settingsWindowObserved = false
var tabControlObserved = false
var tabSelectedStateRead = false
var tabSelected: Bool?

func require(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {
    if try !condition() { throw SettingsFailure(message: message) }
}

func parseOptions() throws -> Options {
    var result = Options()
    var index = 1
    let args = CommandLine.arguments
    while index < args.count {
        let key = args[index]
        if key == "--execute" {
            result.execute = true
            index += 1
            continue
        }
        guard index + 1 < args.count else { throw SettingsFailure(message: "missing value for \(key)") }
        let value = args[index + 1]
        switch key {
        case "--pid":
            guard let parsed = Int32(value), parsed > 0 else { throw SettingsFailure(message: "PID must be a positive 32-bit integer") }
            result.pid = pid_t(parsed)
        case "--bundle-id": result.bundleID = value
        case "--window-title": result.windowTitle = value
        case "--expected-generation":
            guard let parsed = Int64(value), parsed > 0 else { throw SettingsFailure(message: "expected process generation must be a positive integer") }
            result.expectedGeneration = parsed
        default: throw SettingsFailure(message: "unknown option: \(key)")
        }
        index += 2
    }
    try require(result.pid > 0, "--pid is required")
    try require(result.bundleID == bundleIDRequired, "--bundle-id must identify Ableton Live")
    try require(!result.windowTitle.isEmpty && result.windowTitle.utf8.count <= 256,
                "--window-title must contain 1 to 256 UTF-8 bytes")
    if result.execute {
        try require(result.expectedGeneration != nil, "--execute requires the generation from a prior read-only inspection")
    }
    return result
}

func generation(_ process: NSRunningApplication) throws -> Int64 {
    guard let launchDate = process.launchDate else {
        throw SettingsFailure(message: "target process launch generation is unavailable")
    }
    let value = launchDate.timeIntervalSince1970 * 1_000_000
    guard value.isFinite, value > 0, value < Double(Int64.max) else {
        throw SettingsFailure(message: "target process launch generation is invalid")
    }
    return Int64(value.rounded(.towardZero))
}

func targetProcess() throws -> NSRunningApplication {
    guard let process = NSRunningApplication(processIdentifier: options.pid),
          process.bundleIdentifier == options.bundleID,
          !process.isTerminated else {
        throw SettingsFailure(message: "exact target PID/bundle identity is no longer running")
    }
    let matching = NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == options.bundleID }
    guard matching.count == 1, matching[0].processIdentifier == options.pid else {
        throw SettingsFailure(message: "expected exactly one running process for the Live bundle")
    }
    let currentGeneration = try generation(process)
    if let pinned = options.expectedGeneration, currentGeneration != pinned {
        throw SettingsFailure(message: "target process generation differs from the read-only inspection")
    }
    if let initial = processGeneration, currentGeneration != initial {
        throw SettingsFailure(message: "target process generation changed during the operation")
    }
    processGeneration = currentGeneration
    return process
}

func axValue(_ element: AXUIElement, _ attribute: String) throws -> CFTypeRef? {
    AXUIElementSetMessagingTimeout(element, 2.0)
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    if error == .success { return value }
    if error == .noValue { return nil }
    throw SettingsFailure(message: "AX read \(attribute) failed (\(error.rawValue))")
}

func requiredString(_ element: AXUIElement, _ attribute: String) throws -> String {
    guard let value = try axValue(element, attribute), let string = value as? String else {
        throw SettingsFailure(message: "required AX string is unavailable: \(attribute)")
    }
    return string
}

func childElements(_ element: AXUIElement) throws -> [AXUIElement] {
    guard let value = try axValue(element, kAXChildrenAttribute as String) else { return [] }
    guard let children = value as? [AXUIElement] else {
        throw SettingsFailure(message: "AXChildren has an unexpected type")
    }
    return children
}

func allElements(_ roots: [AXUIElement], maxElements: Int, maxDepth: Int) throws -> [AXUIElement] {
    var result: [AXUIElement] = []
    var queue = roots.map { ($0, 0) }
    var seen = Set<CFHashCode>()
    while !queue.isEmpty {
        let (element, depth) = queue.removeFirst()
        if !seen.insert(CFHash(element)).inserted { continue }
        guard result.count < maxElements else {
            throw SettingsFailure(message: "AX subtree exceeds the \(maxElements)-element bound")
        }
        result.append(element)
        let children = try childElements(element)
        if depth < maxDepth {
            guard result.count + queue.count + children.count <= maxElements else {
                throw SettingsFailure(message: "AX subtree would exceed the \(maxElements)-element bound")
            }
            queue.append(contentsOf: children.map { ($0, depth + 1) })
        } else if !children.isEmpty {
            throw SettingsFailure(message: "AX subtree exceeds the \(maxDepth)-level depth bound")
        }
    }
    return result
}

func role(_ element: AXUIElement) throws -> String {
    try requiredString(element, kAXRoleAttribute as String)
}

func title(_ element: AXUIElement) throws -> String? {
    try axValue(element, kAXTitleAttribute as String) as? String
}

func enabled(_ element: AXUIElement) throws -> Bool {
    guard let value = try axValue(element, kAXEnabledAttribute as String), let number = value as? NSNumber else {
        throw SettingsFailure(message: "AX enabled state is unavailable")
    }
    return number.boolValue
}

func actionNames(_ element: AXUIElement) throws -> [String] {
    var names: CFArray?
    AXUIElementSetMessagingTimeout(element, 2.0)
    let error = AXUIElementCopyActionNames(element, &names)
    guard error == .success, let names = names as? [String] else {
        throw SettingsFailure(message: "AX action names are unavailable (\(error.rawValue))")
    }
    return names
}

func press(_ element: AXUIElement, action: String, beforePress: (() throws -> Void)? = nil) throws {
    try require(try enabled(element), "exact AX target is disabled")
    try require(try actionNames(element).contains(kAXPressAction as String), "exact AX target does not support AXPress")
    try beforePress?()
    actions[action] = "outcome_unknown"
    AXUIElementSetMessagingTimeout(element, 2.0)
    let error = AXUIElementPerformAction(element, kAXPressAction as CFString)
    try require(error == .success, "AXPress failed (\(error.rawValue)); outcome unknown")
    actions[action] = "AXPress_succeeded"
}

struct ScreenWindow {
    let windowNumber: Int
    let title: String
}

func currentDesktopWindow(pid: pid_t, expectedTitle: String) throws -> ScreenWindow {
    let flags: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let entries = CGWindowListCopyWindowInfo(flags, kCGNullWindowID) as? [[String: Any]] else {
        throw SettingsFailure(message: "current-Desktop on-screen window inventory is unavailable")
    }
    let matches = entries.compactMap { entry -> ScreenWindow? in
        guard (entry[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
              (entry[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
              (entry[kCGWindowName as String] as? String) == expectedTitle,
              let number = (entry[kCGWindowNumber as String] as? NSNumber)?.intValue,
              let bounds = entry[kCGWindowBounds as String] as? [String: NSNumber],
              (bounds["Width"]?.doubleValue ?? 0) > 0,
              (bounds["Height"]?.doubleValue ?? 0) > 0 else { return nil }
        return ScreenWindow(windowNumber: number, title: expectedTitle)
    }
    try require(matches.count == 1, "exact PID/window title is not uniquely visible on the current Desktop")
    return matches[0]
}

func appWindows(_ app: AXUIElement) throws -> [AXUIElement] {
    guard let value = try axValue(app, kAXWindowsAttribute as String), let windows = value as? [AXUIElement],
          !windows.isEmpty, windows.count <= maxWindows else {
        throw SettingsFailure(message: "target AX window inventory is empty, unreadable, or over bound")
    }
    return windows
}

func exactWindow(_ app: AXUIElement, expectedTitle: String) throws -> AXUIElement {
    let matches = try appWindows(app).filter { try role($0) == (kAXWindowRole as String) && title($0) == expectedTitle }
    try require(matches.count == 1, "exact AX window title matched \(matches.count); expected one: \(expectedTitle)")
    return matches[0]
}

func isFocused(_ app: AXUIElement, window: AXUIElement) throws -> Bool {
    guard let value = try axValue(app, kAXFocusedWindowAttribute as String),
          CFGetTypeID(value) == AXUIElementGetTypeID() else { return false }
    return CFEqual(unsafeBitCast(value, to: AXUIElement.self), window)
}

func waitForFrontmost(_ pid: pid_t, seconds: TimeInterval = 5) throws {
    let deadline = ProcessInfo.processInfo.systemUptime + seconds
    while ProcessInfo.processInfo.systemUptime < deadline {
        _ = try targetProcess()
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid { return }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    throw SettingsFailure(message: "exact Live PID did not become frontmost before the bounded deadline")
}

func focusExactWindow(_ app: AXUIElement, pid: pid_t, window: AXUIElement, title expectedTitle: String) throws {
    try require(currentDesktopWindow(pid: pid, expectedTitle: expectedTitle).windowNumber > 0,
                "exact window left the current Desktop before AXRaise")
    if try !isFocused(app, window: window) {
        _ = try targetProcess()
        try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                    "Live lost foreground before AXRaise")
        _ = try currentDesktopWindow(pid: pid, expectedTitle: expectedTitle)
        let supported = try actionNames(window)
        try require(supported.contains(kAXRaiseAction as String), "exact AX window does not support AXRaise")
        actions["raise_source_window"] = "outcome_unknown"
        let error = AXUIElementPerformAction(window, kAXRaiseAction as CFString)
        try require(error == .success, "raising the exact AX window failed (\(error.rawValue)); outcome unknown")
        actions["raise_source_window"] = "AXRaise_succeeded"
    }
    let deadline = ProcessInfo.processInfo.systemUptime + 5
    while ProcessInfo.processInfo.systemUptime < deadline {
        _ = try targetProcess()
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
           try isFocused(app, window: window),
           (try? currentDesktopWindow(pid: pid, expectedTitle: expectedTitle)) != nil { return }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    throw SettingsFailure(message: "exact Live window did not become focused on the current Desktop")
}

func exactSettingsMenuItem(_ app: AXUIElement) throws -> AXUIElement {
    guard let rawMenuBar = try axValue(app, kAXMenuBarAttribute as String),
          CFGetTypeID(rawMenuBar) == AXUIElementGetTypeID() else {
        throw SettingsFailure(message: "Live AX menu bar is unavailable")
    }
    let menuBar = unsafeBitCast(rawMenuBar, to: AXUIElement.self)
    let menuItems = try childElements(menuBar).filter { try role($0) == (kAXMenuBarItemRole as String) && title($0) == "Live" }
    try require(menuItems.count == 1, "expected exactly one Live application menu")
    let elements = try allElements([menuItems[0]], maxElements: menuMaxElements, maxDepth: menuMaxDepth)
    let matches = try elements.filter { try role($0) == (kAXMenuItemRole as String) && title($0) == settingsMenuTitle }
    try require(matches.count == 1, "expected exactly one Live > Settings... menu item")
    try require(try enabled(matches[0]), "Live > Settings... is disabled")
    try require(try actionNames(matches[0]).contains(kAXPressAction as String), "Live > Settings... does not support AXPress")
    return matches[0]
}

func exactTempoMidiTab(_ settings: AXUIElement) throws -> AXUIElement {
    let elements = try allElements([settings], maxElements: maxElements, maxDepth: maxDepth)
    let matches = try elements.filter {
        try role($0) == (kAXRadioButtonRole as String) &&
            (try axValue($0, "AXIdentifier") as? String) == tempoMidiTabIdentifier &&
            (try axValue($0, kAXDescriptionAttribute as String) as? String) == "Tempo & MIDI"
    }
    try require(matches.count == 1, "expected exactly one Tempo & MIDI AXRadioButton with identifier midiTabButton")
    try require(try enabled(matches[0]), "Tempo & MIDI tab radio button is disabled")
    try require(try actionNames(matches[0]).contains(kAXPressAction as String), "Tempo & MIDI tab radio button does not support AXPress")
    tabSelectedStateRead = true
    tabSelected = try radioValue(matches[0])
    return matches[0]
}

func radioValue(_ element: AXUIElement) throws -> Bool {
    guard let value = try axValue(element, kAXValueAttribute as String), let number = value as? NSNumber,
          number.doubleValue == 0 || number.doubleValue == 1 else {
        throw SettingsFailure(message: "Tempo & MIDI radio-button selection value is unreadable")
    }
    return number.boolValue
}

func run() throws -> [String: Any] {
    phase = "preflight"
    try require(AXIsProcessTrusted(), "Accessibility permission is not already trusted; no prompt was requested")
    let process = try targetProcess()
    let initialGeneration = try generation(process)
    processGeneration = initialGeneration
    if let expected = options.expectedGeneration {
        try require(expected == initialGeneration, "target process generation differs from the read-only inspection")
    }
    let desktopWindow = try currentDesktopWindow(pid: options.pid, expectedTitle: options.windowTitle)
    sourceWindowOnDesktop = true
    sourceWindowNumber = desktopWindow.windowNumber
    let app = AXUIElementCreateApplication(options.pid)
    let sourceWindow = try exactWindow(app, expectedTitle: options.windowTitle)
    _ = try exactSettingsMenuItem(app)

    if !options.execute {
        let existingSettings = try appWindows(app).filter { try role($0) == (kAXWindowRole as String) && title($0) == settingsWindowTitle }
        try require(existingSettings.count <= 1, "Settings AX window is ambiguous")
        if let settings = existingSettings.first {
            settingsWindowObserved = true
            _ = try currentDesktopWindow(pid: options.pid, expectedTitle: settingsWindowTitle)
            _ = try exactTempoMidiTab(settings)
            tabControlObserved = true
        }
        return receipt(status: "ready", readOnly: true, windowNumber: desktopWindow.windowNumber, error: nil)
    }

    phase = "activate-target"
    // This current-Desktop proof is deliberately immediately before activation.
    _ = try currentDesktopWindow(pid: options.pid, expectedTitle: options.windowTitle)
    let currentProcess = try targetProcess()
    actions["activate"] = "outcome_unknown"
    try require(currentProcess.activate(), "activation of the exact Live process was refused")
    try waitForFrontmost(options.pid)
    actions["activate"] = "frontmost_verified"
    _ = try targetProcess()
    _ = try currentDesktopWindow(pid: options.pid, expectedTitle: options.windowTitle)
    try focusExactWindow(app, pid: options.pid, window: sourceWindow, title: options.windowTitle)
    try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid && (try isFocused(app, window: sourceWindow)),
                "exact Live PID/window is not frontmost and focused before menu press")

    phase = "open-settings-menu"
    let freshMenuItem = try exactSettingsMenuItem(app)
    try press(freshMenuItem, action: "settings_menu") {
        _ = try targetProcess()
        try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid &&
                    (try isFocused(app, window: sourceWindow)),
                    "exact Live PID/window lost foreground before menu press")
    }
    _ = try targetProcess()

    phase = "wait-settings-window"
    let deadline = ProcessInfo.processInfo.systemUptime + 5
    var settings: AXUIElement?
    while ProcessInfo.processInfo.systemUptime < deadline {
        _ = try targetProcess()
        let matches = try appWindows(app).filter { try role($0) == (kAXWindowRole as String) && title($0) == settingsWindowTitle }
        try require(matches.count <= 1, "Settings AX window is ambiguous after opening")
        if let found = matches.first,
           (try? currentDesktopWindow(pid: options.pid, expectedTitle: settingsWindowTitle)) != nil {
            settings = found
            break
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    guard let settings else { throw SettingsFailure(message: "unique Settings window did not appear on the current Desktop before the deadline") }
    settingsWindowObserved = true
    try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid && (try isFocused(app, window: settings)),
                "Settings window is not foreground/focused after opening; no tab action attempted")
    _ = try targetProcess()
    let tab = try exactTempoMidiTab(settings)
    tabControlObserved = true
    try require(currentDesktopWindow(pid: options.pid, expectedTitle: settingsWindowTitle).windowNumber > 0,
                "Settings window left the current Desktop before tab press")
    try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid && (try isFocused(app, window: settings)),
                "Settings window lost foreground/focus before tab press")
    _ = try targetProcess()

    phase = "press-tempo-midi-tab"
    try press(tab, action: "tempo_midi_tab") {
        _ = try targetProcess()
        _ = try currentDesktopWindow(pid: options.pid, expectedTitle: settingsWindowTitle)
        try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid &&
                    (try isFocused(app, window: settings)),
                    "Settings lost foreground/focus before Tempo & MIDI tab press")
    }
    _ = try targetProcess()
    try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid,
                "Live lost foreground after the Tempo & MIDI AXPress")
    phase = "verify-tempo-midi-tab"
    try require(currentDesktopWindow(pid: options.pid, expectedTitle: settingsWindowTitle).windowNumber > 0,
                "Settings window left the current Desktop after tab press")
    try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid && (try isFocused(app, window: settings)),
                "Settings window lost foreground/focus after tab press")
    let selectedValue = try radioValue(tab)
    tabSelectedStateRead = true
    tabSelected = selectedValue
    try require(selectedValue, "Tempo & MIDI tab AXValue did not read back selected after AXPress")
    return receipt(status: "selected", readOnly: false, windowNumber: desktopWindow.windowNumber, error: nil)
}

func receipt(status: String, readOnly: Bool, windowNumber: Int?, error: String?) -> [String: Any] {
    let target: [String: Any] = [
        "pid": Int(options.pid),
        "bundle_identifier": options.bundleID,
        "process_generation": processGeneration.map { String($0) } as Any? ?? NSNull(),
    ]
    return [
        "format": "codex-devtools-live-settings-tab-v1",
        "status": status,
        "phase": phase,
        "read_only": readOnly,
        "target": target,
        "expected_generation": options.expectedGeneration.map { String($0) } as Any? ?? NSNull(),
        "source_window": [
            "title": options.windowTitle,
            "on_current_desktop": sourceWindowOnDesktop,
            "window_number": (windowNumber ?? sourceWindowNumber) as Any? ?? NSNull(),
        ],
        "settings": [
            "window_title": settingsWindowTitle,
            "window_observed": settingsWindowObserved,
            "tab_identifier": tempoMidiTabIdentifier,
            "tab_control_observed": tabControlObserved,
            "tab_selected_state_read": tabSelectedStateRead,
            "tab_selected": tabSelected as Any? ?? NSNull(),
        ],
        "actions": actions,
        "midi_preference_values_read": false,
        "midi_preference_values_changed": false,
        "manual_inspection_required": !readOnly,
        "error": error as Any? ?? NSNull(),
        "evidence_limit": "Tempo & MIDI radio-button AXValue was read back selected; MIDI preference checkbox values were neither read nor changed",
    ]
}

func emit(_ value: [String: Any], code: Int32) -> Never {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]),
          let text = String(data: data, encoding: .utf8) else {
        fputs("Live Settings AX helper could not encode its receipt\n", stderr)
        exit(2)
    }
    print(text)
    exit(code)
}

do {
    options = try parseOptions()
    emit(try run(), code: 0)
} catch let error as SettingsFailure {
    phase = phase == "arguments" ? "arguments" : phase
    let attempted = actions.values.contains { $0 != "not_attempted" }
    let report = receipt(status: "stopped", readOnly: !options.execute, windowNumber: nil, error: error.message)
    var stopped = report
    stopped["manual_inspection_required"] = attempted
    emit(stopped, code: 2)
} catch {
    let attempted = actions.values.contains { $0 != "not_attempted" }
    var stopped = receipt(status: "stopped", readOnly: !options.execute, windowNumber: nil, error: String(describing: error))
    stopped["manual_inspection_required"] = attempted
    emit(stopped, code: 2)
}
