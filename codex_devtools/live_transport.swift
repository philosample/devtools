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
    var windowIdentity = ""
    var expectedGeneration: Int64?
    var snapshotObservedAtUTC = ""
    var snapshotLaunchDate = ""
    var preflightReceipt = ""
    var preflightHash = ""
    var positionX: Double?
    var positionY: Double?
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

struct WindowSharingOverlayChildSignature: Equatable {
    let role: String
    let title: String?
    let identifier: String?
    let enabled: Bool
}

struct WindowSharingOverlaySignature: Equatable {
    let role: String
    let subrole: String?
    let title: String?
    let identifier: String?
    let children: [WindowSharingOverlayChildSignature]
}

func isObservedWindowSharingOverlay(_ signature: WindowSharingOverlaySignature) -> Bool {
    signature == WindowSharingOverlaySignature(
        role: kAXWindowRole as String,
        subrole: "AXDialog",
        title: "Window",
        identifier: "_NS:8",
        children: [WindowSharingOverlayChildSignature(
            role: kAXButtonRole as String,
            title: "WindowSharingSessionButton",
            identifier: nil,
            enabled: true
        )]
    )
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
            print("Usage: live_transport.swift --pid PID --bundle-id ID --set /path/Set.als [--action focus-window|position-window --window-identity CFHASH --expected-generation GENERATION [--receipt FILE] | --execute --action focus-window|position-window --expected-generation GENERATION --preflight-receipt FILE --preflight-sha256 SHA256 --receipt FILE | --execute --action play|stop]")
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
        case "--window-identity": result.windowIdentity = value
        case "--expected-generation":
            guard let parsed = Int64(value), parsed > 0 else { throw TransportFailure(phase: "arguments", message: "expected generation must be positive") }
            result.expectedGeneration = parsed
        case "--snapshot-observed-at-utc": result.snapshotObservedAtUTC = value
        case "--snapshot-launch-date": result.snapshotLaunchDate = value
        case "--preflight-receipt": result.preflightReceipt = value
        case "--preflight-sha256": result.preflightHash = value
        case "--position-x":
            guard let parsed = Double(value), parsed.isFinite else {
                throw TransportFailure(phase: "arguments", message: "position X must be finite")
            }
            result.positionX = parsed
        case "--position-y":
            guard let parsed = Double(value), parsed.isFinite else {
                throw TransportFailure(phase: "arguments", message: "position Y must be finite")
            }
            result.positionY = parsed
        default:
            throw TransportFailure(phase: "arguments", message: "unknown option: \(key)")
        }
        index += 2
    }
    guard result.pid > 0, !result.bundleID.isEmpty, !result.setPath.isEmpty,
          ["inspect", "play", "stop", "focus-window", "position-window"].contains(result.action) else {
        throw TransportFailure(phase: "arguments", message: "PID, bundle ID, exact Set path and a supported action are required")
    }
    if result.action == "focus-window" || result.action == "position-window" {
        guard result.expectedGeneration != nil else { throw TransportFailure(phase: "arguments", message: "focus-window requires process generation") }
        if result.action == "position-window" {
            if result.execute {
                guard !result.preflightReceipt.isEmpty, result.preflightHash.count == 64,
                      result.windowIdentity.isEmpty, result.positionX == nil, result.positionY == nil else {
                    throw TransportFailure(phase: "arguments", message: "position execution requires only a hashed preflight receipt")
                }
            } else {
                guard !result.windowIdentity.isEmpty, result.windowIdentity.utf8.count <= 64,
                      result.windowIdentity.allSatisfy({ $0.isNumber }),
                      !result.snapshotObservedAtUTC.isEmpty, !result.snapshotLaunchDate.isEmpty,
                      result.positionX != nil, result.positionY != nil,
                      result.preflightReceipt.isEmpty, result.preflightHash.isEmpty else {
                    throw TransportFailure(phase: "arguments", message: "position preflight requires identity, fresh snapshot metadata, and finite X/Y")
                }
            }
        }
        if result.execute {
            if result.action == "focus-window" {
                guard !result.preflightReceipt.isEmpty, result.preflightHash.count == 64, result.windowIdentity.isEmpty else {
                throw TransportFailure(phase: "arguments", message: "focus execution requires a hashed preflight receipt")
                }
            }
        } else if result.action == "focus-window" {
            guard !result.windowIdentity.isEmpty, result.windowIdentity.utf8.count <= 64,
                  result.windowIdentity.allSatisfy({ $0.isNumber }),
                  !result.snapshotObservedAtUTC.isEmpty, !result.snapshotLaunchDate.isEmpty else {
                throw TransportFailure(phase: "arguments", message: "focus preflight requires a CFHash identity and fresh AX snapshot metadata")
            }
        }
    } else if result.action == "inspect" {
        guard !result.execute else { throw TransportFailure(phase: "arguments", message: "inspect is read-only") }
    } else {
        guard result.execute else { throw TransportFailure(phase: "arguments", message: "mutating actions require --execute") }
    }
    if result.action != "position-window" {
        guard result.positionX == nil, result.positionY == nil else {
            throw TransportFailure(phase: "arguments", message: "position coordinates are valid only for position-window preflight")
        }
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
    let windows = try rawAppWindows(app)
    for window in windows {
        let kind = try role(window)
        let subrole = optionalString(window, kAXSubroleAttribute as String) ?? ""
        let isBlockingWindow = kind == "AXDialog" || kind == "AXSheet" || kind == "AXPopover" ||
            ["AXDialog", "AXSystemDialog", "AXFloatingWindow"].contains(subrole)
        if isBlockingWindow {
            let isAllowedOverlay = try isObservedWindowSharingOverlay(window, windowRole: kind, subrole: subrole)
            if !isAllowedOverlay {
                throw TransportFailure(phase: phase, message: "unexpected dialog, sheet or popover is open")
            }
        }
    }
    return windows
}

func rawAppWindows(_ app: AXUIElement) throws -> [AXUIElement] {
    guard let raw = try axValue(app, kAXWindowsAttribute as String),
          let windows = raw as? [AXUIElement], !windows.isEmpty, windows.count <= maxWindows else {
        throw TransportFailure(phase: phase, message: "target AX window inventory is unavailable or out of bounds")
    }
    return windows
}

func focusWindows(_ app: AXUIElement) throws -> [AXUIElement] {
    let windows = try rawAppWindows(app)
    for window in windows {
        let kind = try role(window)
        let subrole = optionalString(window, kAXSubroleAttribute as String) ?? ""
        let blocking = kind == "AXDialog" || kind == "AXSheet" || kind == "AXPopover" ||
            ["AXDialog", "AXSystemDialog"].contains(subrole)
        if blocking {
            let allowed = try isObservedWindowSharingOverlay(window, windowRole: kind, subrole: subrole)
            try require(allowed, "focus-window refuses while an unrecognized dialog, sheet or popover is open")
        }
    }
    return windows
}

func exactFocusSetWindow(_ app: AXUIElement, url: URL) throws -> AXUIElement {
    var matches: [AXUIElement] = []
    for window in try focusWindows(app) where try role(window) == (kAXWindowRole as String) {
        guard let document = optionalString(window, kAXDocumentAttribute as String) else { continue }
        if try sameSetURL(document, expected: url) { matches.append(window) }
    }
    try require(matches.count == 1, "exact current Set URL must identify one AX window")
    return matches[0]
}

func isObservedWindowSharingOverlay(_ window: AXUIElement, windowRole: String, subrole: String) throws -> Bool {
    guard windowRole == (kAXWindowRole as String), subrole == "AXDialog",
          optionalString(window, kAXTitleAttribute as String) == "Window",
          optionalString(window, "AXIdentifier") == "_NS:8" else { return false }
    let childElements = try children(window)
    guard childElements.count == 1 else { return false }
    let button = childElements[0]
    return isObservedWindowSharingOverlay(WindowSharingOverlaySignature(
        role: windowRole,
        subrole: subrole,
        title: optionalString(window, kAXTitleAttribute as String),
        identifier: optionalString(window, "AXIdentifier"),
        children: [WindowSharingOverlayChildSignature(
            role: try role(button),
            title: optionalString(button, kAXTitleAttribute as String),
            identifier: optionalString(button, "AXIdentifier"),
            enabled: try enabled(button)
        )]
    ))
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

func processGeneration(_ process: NSRunningApplication) throws -> Int64 {
    guard let launchDate = process.launchDate else { throw TransportFailure(phase: phase, message: "Live process generation is unavailable") }
    let value = launchDate.timeIntervalSince1970 * 1_000_000
    guard value.isFinite, value > 0, value < Double(Int64.max) else { throw TransportFailure(phase: phase, message: "Live process generation is invalid") }
    return Int64(value.rounded(.towardZero))
}

func pumpRunLoopTurn(until deadline: UInt64) {
    let now = DispatchTime.now().uptimeNanoseconds
    guard now < deadline else { return }
    let remaining = Double(deadline - now) / 1_000_000_000
    RunLoop.current.run(
        mode: .default,
        before: Date().addingTimeInterval(min(0.05, remaining))
    )
}

func pumpRunLoop(until deadline: UInt64) {
    while DispatchTime.now().uptimeNanoseconds < deadline {
        pumpRunLoopTurn(until: deadline)
    }
}

func waitUntil(_ predicate: () -> Bool, seconds: TimeInterval) -> Bool {
    let deadline = DispatchTime.now().uptimeNanoseconds + UInt64(seconds * 1_000_000_000)
    while DispatchTime.now().uptimeNanoseconds < deadline {
        if predicate() { return true }
        pumpRunLoopTurn(until: deadline)
    }
    return predicate()
}

func waitForValueUntilDeadline(
    _ expected: Int,
    deadline: UInt64,
    now: () -> UInt64,
    read: () throws -> Int,
    pump: () -> Void
) throws -> Bool {
    while now() < deadline {
        let value = try read()
        if now() >= deadline { return false }
        if value == expected { return true }
        pump()
    }
    return false
}

func waitForTransportValue(
    _ expected: Int,
    element: AXUIElement,
    controlName: String,
    app: AXUIElement,
    pid: pid_t,
    bundleID: String,
    window: AXUIElement,
    url: URL,
    title: String,
    deadline: UInt64
) throws -> Bool {
    let guardedRead = { () throws -> Int in
        try require(currentProcessMatches(pid, bundleID: bundleID), "Live process identity changed during \(controlName) readback")
        try require(frontmost(pid) && focusedWindowIs(app, window), "exact Set focus changed during \(controlName) readback")
        try validateWindowDocument(window, url: url)
        try require(onscreenWindow(pid: pid, title: title), "exact Set window left the current desktop during \(controlName) readback")
        _ = try appWindows(app)
        try validateControlIdentity(element, name: controlName)
        return try checkboxValue(element)
    }
    return try waitForValueUntilDeadline(
        expected,
        deadline: deadline,
        now: { DispatchTime.now().uptimeNanoseconds },
        read: guardedRead,
        pump: { pumpRunLoopTurn(until: deadline) }
    )
}

func acknowledgementDeadline(maxSeconds: Double, cappedBy deadline: UInt64) -> UInt64 {
    let now = DispatchTime.now().uptimeNanoseconds
    let interval = UInt64(maxSeconds * 1_000_000_000)
    let (uncapped, overflow) = now.addingReportingOverflow(interval)
    return min(deadline, overflow ? UInt64.max : uncapped)
}

func onscreenWindow(pid: pid_t, title: String, number: Int? = nil) throws -> Bool {
    let flags: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let entries = CGWindowListCopyWindowInfo(flags, kCGNullWindowID) as? [[String: Any]] else {
        throw TransportFailure(phase: phase, message: "current-desktop on-screen window inventory is unavailable")
    }
    let matches = entries.filter { entry in
        guard (entry[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
              (entry[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
              entry[kCGWindowName as String] as? String == title,
              number == nil || (entry[kCGWindowNumber as String] as? NSNumber)?.intValue == number,
              let bounds = entry[kCGWindowBounds as String] as? [String: NSNumber],
              (bounds["Width"]?.doubleValue ?? 0) > 0,
              (bounds["Height"]?.doubleValue ?? 0) > 0 else { return false }
        return true
    }
    return matches.count == 1
}

func raiseAndFocus(_ app: AXUIElement, pid: pid_t, process: NSRunningApplication, window: AXUIElement, title: String, number: Int? = nil) throws {
    try require(onscreenWindow(pid: pid, title: title, number: number), "exact target window is not proven on the current desktop")
    if !frontmost(pid) {
        try require(process.activate(), "exact Live process activation failed")
    }
    try require(waitUntil({ frontmost(pid) }, seconds: 5), "exact Live PID did not become frontmost")
    try require(onscreenWindow(pid: pid, title: title, number: number), "exact target window left the current desktop during activation")
    if !focusedWindowIs(app, window) {
        let supported = try actionNames(window)
        try require(supported.contains(kAXRaiseAction as String), "exact target window does not support AXRaise")
        let error = AXUIElementPerformAction(window, kAXRaiseAction as CFString)
        try require(error == .success, "raising the exact target window failed (\(error.rawValue))")
    }
    try require(waitUntil({
        frontmost(pid) && focusedWindowIs(app, window) && ((try? onscreenWindow(pid: pid, title: title, number: number)) == true)
    }, seconds: 5), "exact target window did not become focused on the current desktop")
}

func windowNumber(_ window: AXUIElement) -> Int? {
    guard let raw = try? axValue(window, "AXWindowNumber"), let number = raw as? NSNumber,
          number.doubleValue.isFinite, number.doubleValue >= 0,
          number.doubleValue.rounded(.towardZero) == number.doubleValue else { return nil }
    return number.intValue
}

func windowGeometry(_ window: AXUIElement) throws -> [String: Double] {
    guard let positionRaw = try axValue(window, kAXPositionAttribute as String),
          CFGetTypeID(positionRaw) == AXValueGetTypeID(),
          let sizeRaw = try axValue(window, kAXSizeAttribute as String),
          CFGetTypeID(sizeRaw) == AXValueGetTypeID() else {
        throw TransportFailure(phase: phase, message: "target window geometry is unavailable")
    }
    let position = positionRaw as! AXValue
    let size = sizeRaw as! AXValue
    try require(AXValueGetType(position) == .cgPoint && AXValueGetType(size) == .cgSize,
                "target window geometry has an unexpected type")
    var point = CGPoint.zero
    var dimensions = CGSize.zero
    guard AXValueGetValue(position, .cgPoint, &point), AXValueGetValue(size, .cgSize, &dimensions),
          point.x.isFinite, point.y.isFinite, dimensions.width.isFinite, dimensions.height.isFinite,
          dimensions.width > 0, dimensions.height > 0 else {
        throw TransportFailure(phase: phase, message: "target window geometry is invalid or minimized")
    }
    return ["x": point.x, "y": point.y, "width": dimensions.width, "height": dimensions.height]
}

struct FocusCGObservation {
    let pid: pid_t
    let name: String?
    let number: Int?
    let layer: Int?
    let x: Double?
    let y: Double?
    let width: Double?
    let height: Double?
    let onscreen: Bool
}

struct FocusCGCandidate: Equatable {
    let number: Int
    let layer: Int
}

func focusCGGeometryMatches(_ row: FocusCGObservation, geometry: [String: Double]) -> Bool {
    let pairs: [(Double?, String)] = [
        (row.x, "x"), (row.y, "y"), (row.width, "width"), (row.height, "height"),
    ]
    for (actual, key) in pairs {
        guard let actual, actual.isFinite, let expected = geometry[key], expected.isFinite,
              abs(actual - expected) < 2 else { return false }
    }
    return (row.width ?? 0) > 0 && (row.height ?? 0) > 0
}

func focusCGCandidateSet(_ rows: [FocusCGObservation], pid: pid_t,
                         title: String, geometry: [String: Double]) throws -> [FocusCGCandidate] {
    try require(rows.count <= maxWindows, "current-desktop Live CG inventory exceeds its bound")
    try require(rows.allSatisfy { $0.pid == pid }, "focus CG inventory contains another process")
    let matching = rows.filter {
        $0.onscreen && $0.name == title && focusCGGeometryMatches($0, geometry: geometry)
    }
    try require(!matching.isEmpty, "no current-desktop CG window matches the selected AX window")
    try require(matching.allSatisfy {
        $0.number != nil && ($0.layer == 0 || $0.layer == 3)
    }, "matching CG window has a missing number or an unreviewed layer")
    let candidates = matching.map { FocusCGCandidate(number: $0.number!, layer: $0.layer!) }
        .sorted { ($0.number, $0.layer) < ($1.number, $1.layer) }
    try require(Set(candidates.map(\.number)).count == candidates.count,
                "current-desktop CG candidate numbers are duplicated")
    return candidates
}

func currentFocusCGCandidateSet(pid: pid_t, title: String,
                                geometry: [String: Double]) throws -> [FocusCGCandidate] {
    let flags: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let entries = CGWindowListCopyWindowInfo(flags, kCGNullWindowID) as? [[String: Any]] else {
        throw TransportFailure(phase: phase, message: "current-desktop CG inventory is unavailable")
    }
    let owned = entries.filter { ($0[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid }
    let rows = owned.map { entry -> FocusCGObservation in
        let bounds = entry[kCGWindowBounds as String] as? [String: NSNumber]
        return FocusCGObservation(
            pid: pid,
            name: entry[kCGWindowName as String] as? String,
            number: (entry[kCGWindowNumber as String] as? NSNumber)?.intValue,
            layer: (entry[kCGWindowLayer as String] as? NSNumber)?.intValue,
            x: bounds?["X"]?.doubleValue,
            y: bounds?["Y"]?.doubleValue,
            width: bounds?["Width"]?.doubleValue,
            height: bounds?["Height"]?.doubleValue,
            onscreen: true
        )
    }
    return try focusCGCandidateSet(rows, pid: pid, title: title, geometry: geometry)
}

func focusCGCandidateReport(_ candidates: [FocusCGCandidate]) -> [[String: Int]] {
    candidates.map { ["number": $0.number, "layer": $0.layer] }
}

func focusWindowRecord(_ window: AXUIElement, app: AXUIElement, pid: pid_t, generation: Int64) throws -> [String: Any] {
    try require(try role(window) == (kAXWindowRole as String), "selected AX element is not a window")
    let subrole = optionalString(window, kAXSubroleAttribute as String) ?? ""
    try require(!["AXDialog", "AXSheet", "AXPopover", "AXSystemDialog"].contains(subrole), "dialogs and sheets cannot be focused")
    let document = try validatedFocusWindowDocument(optionalString(window, kAXDocumentAttribute as String))
    guard let minimized = try axValue(window, "AXMinimized") as? NSNumber else {
        throw TransportFailure(phase: phase, message: "target window minimized state is unavailable")
    }
    try require(!minimized.boolValue, "minimized windows cannot be focused")
    let title = try stringAttribute(window, kAXTitleAttribute as String)
    let identifier = optionalString(window, "AXIdentifier")
    let geometry = try windowGeometry(window)
    let number = windowNumber(window)
    let cgCandidates = try currentFocusCGCandidateSet(pid: pid, title: title, geometry: geometry)
    try require(number == nil || cgCandidates.contains { $0.number == number },
                "AX window number is absent from its matching current-desktop CG candidate set")
    return [
        "identity": String(CFHash(window)), "role": try role(window), "subrole": subrole,
        "title": title, "identifier": identifier as Any? ?? NSNull(),
        "document": document as Any? ?? NSNull(),
        "geometry": geometry, "window_number": number as Any? ?? NSNull(),
        "cg_candidates": focusCGCandidateReport(cgCandidates),
    ]
}

func validatedFocusWindowDocument(_ raw: String?) throws -> String? {
    guard let raw else { return nil }
    _ = try fileURLPath(raw)
    return raw
}

func selectedFocusWindow(_ app: AXUIElement, identity: String) throws -> AXUIElement {
    let matches = try focusWindows(app).filter { String(CFHash($0)) == identity }
    try require(matches.count == 1, "selected AX window identity is missing or ambiguous")
    return matches[0]
}

func raiseExactFocusWindow(_ app: AXUIElement, process: NSRunningApplication,
                           pid: pid_t, bundleID: String, generation: Int64,
                           url: URL, setWindowIdentity: String,
                           identity: String, pinnedRecord: [String: Any]) throws {
    try require(currentProcessMatches(pid, bundleID: bundleID) && processGeneration(process) == generation,
                "Live process identity changed before focus")
    if !frontmost(pid) {
        try require(process.activate(), "exact Live process activation failed")
    }
    try require(waitUntil({ frontmost(pid) }, seconds: 5), "exact Live PID did not become frontmost")

    let beforeRaiseWindow = try selectedFocusWindow(app, identity: identity)
    let beforeRaiseRecord = try focusWindowRecord(beforeRaiseWindow, app: app, pid: pid, generation: generation)
    let currentSetWindow = try exactFocusSetWindow(app, url: url)
    try require(String(CFHash(currentSetWindow)) == setWindowIdentity,
                "exact Set window identity changed before AXRaise")
    try require(NSDictionary(dictionary: pinnedRecord).isEqual(to: beforeRaiseRecord),
                "pinned AX window or CG candidate set changed before AXRaise")
    try require(currentProcessMatches(pid, bundleID: bundleID) && processGeneration(process) == generation && frontmost(pid),
                "Live process or foreground changed before AXRaise")
    if !focusedWindowIs(app, beforeRaiseWindow) {
        let supported = try actionNames(beforeRaiseWindow)
        try require(supported.contains(kAXRaiseAction as String), "exact AX window does not support AXRaise")
        let immediateWindow = try selectedFocusWindow(app, identity: identity)
        let immediatelyBeforeRaise = try focusWindowRecord(immediateWindow, app: app, pid: pid, generation: generation)
        try require(NSDictionary(dictionary: pinnedRecord).isEqual(to: immediatelyBeforeRaise),
                    "AX window or CG candidate set changed immediately before AXRaise")
        try require(currentProcessMatches(pid, bundleID: bundleID) && processGeneration(process) == generation && frontmost(pid),
                    "Live process or foreground changed immediately before AXRaise")
        try require(String(CFHash(try exactFocusSetWindow(app, url: url))) == setWindowIdentity,
                    "exact Set window identity changed immediately before AXRaise")
        let error = AXUIElementPerformAction(immediateWindow, kAXRaiseAction as CFString)
        try require(error == .success, "raising exact AX window failed (\(error.rawValue))")
    }
    try require(waitUntil({ frontmost(pid) && focusedWindowIs(app, beforeRaiseWindow) }, seconds: 5),
                "exact AX window did not become focused")

    let afterWindow = try selectedFocusWindow(app, identity: identity)
    let afterRecord = try focusWindowRecord(afterWindow, app: app, pid: pid, generation: generation)
    let finalSetWindow = try exactFocusSetWindow(app, url: url)
    try require(currentProcessMatches(pid, bundleID: bundleID) && processGeneration(process) == generation,
                "Live process identity changed during focus")
    try require(String(CFHash(finalSetWindow)) == setWindowIdentity,
                "exact Set window identity changed during focus")
    try require(NSDictionary(dictionary: pinnedRecord).isEqual(to: afterRecord) && focusedWindowIs(app, afterWindow),
                "AX window focus or pinned CG candidate set changed during focus")
}

func currentDesktopWindow(pid: pid_t, title: String, number: Int?, geometry: [String: Double]) throws -> Int {
    let flags: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let entries = CGWindowListCopyWindowInfo(flags, kCGNullWindowID) as? [[String: Any]] else {
        throw TransportFailure(phase: phase, message: "current desktop window inventory is unavailable")
    }
    let matches = entries.filter { entry in
        guard (entry[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
              (entry[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
              entry[kCGWindowName as String] as? String == title,
              let candidate = entry[kCGWindowNumber as String] as? NSNumber,
              number == nil || candidate.intValue == number,
              let bounds = entry[kCGWindowBounds as String] as? [String: NSNumber],
              (bounds["Width"]?.doubleValue ?? 0) > 0, (bounds["Height"]?.doubleValue ?? 0) > 0 else { return false }
        return cgBoundsMatch(bounds, geometry: geometry)
    }
    try require(matches.count == 1, "target window is missing, ambiguous, minimized or off the current desktop")
    return (matches[0][kCGWindowNumber as String] as! NSNumber).intValue
}

func cgBoundsMatch(_ bounds: [String: NSNumber], geometry: [String: Double]) -> Bool {
    let pairs = [("X", "x"), ("Y", "y"), ("Width", "width"), ("Height", "height")]
    for (cgKey, axKey) in pairs {
        guard let actual = bounds[cgKey]?.doubleValue, actual.isFinite,
              let expected = geometry[axKey], expected.isFinite,
              abs(actual - expected) < 2 else { return false }
    }
    return (bounds["Width"]?.doubleValue ?? 0) > 0 && (bounds["Height"]?.doubleValue ?? 0) > 0
}

func focusReceiptIsCurrent(_ prior: [String: Any], pid: pid_t, bundleID: String,
                           generation: Int64, setURL: String, setWindowIdentity: String, identity: String,
                           currentWindow: [String: Any], now: Date) -> Bool {
    guard prior["format"] as? String == "codex-devtools-live-focus-window-preflight-v1",
          prior["status"] as? String == "preflight_ready",
          prior["read_only"] as? Bool == true,
          prior["set_url"] as? String == setURL,
          let target = prior["target"] as? [String: Any],
          (target["pid"] as? NSNumber)?.intValue == Int(pid),
          target["bundle_identifier"] as? String == bundleID,
          (target["process_generation"] as? NSNumber)?.int64Value == generation,
          prior["set_window_identity"] as? String == setWindowIdentity,
          let priorWindow = prior["window"] as? [String: Any],
          priorWindow["identity"] as? String == identity,
          NSDictionary(dictionary: priorWindow).isEqual(to: currentWindow) else { return false }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let observed = formatter.date(from: prior["observed_at_utc"] as? String ?? "") else { return false }
    let age = now.timeIntervalSince(observed)
    return age >= -2 && age <= 30
}

func focusWindowPreflightReport(observedAt: String, snapshotObservedAt: String,
                                snapshotLaunchDate: String, pid: pid_t,
                                bundleID: String, generation: Int64, setURL: String,
                                setWindowIdentity: String,
                                window: [String: Any]) -> [String: Any] {
    [
        "format": "codex-devtools-live-focus-window-preflight-v1",
        "status": "preflight_ready",
        "read_only": true,
        "observed_at_utc": observedAt,
        "snapshot_observed_at_utc": snapshotObservedAt,
        "snapshot_launch_date": snapshotLaunchDate,
        "target": ["pid": pid, "bundle_identifier": bundleID, "process_generation": generation],
        "set_url": setURL,
        "set_window_identity": setWindowIdentity,
        "window": window,
        "actions": [:],
    ]
}

func successfulHelperStatus(_ status: String?) -> Bool {
    ["complete", "observed", "preflight_ready"].contains(status ?? "")
}

func focusWindowAction(_ app: AXUIElement, process: NSRunningApplication, url: URL) throws -> [String: Any] {
    phase = "focus-window-preflight"
    let gen = try processGeneration(process)
    try require(options.expectedGeneration == gen, "Live process generation differs from the caller's inspection")
    let setWindow = try exactFocusSetWindow(app, url: url)
    let setWindowIdentity = String(CFHash(setWindow))
    var prior: [String: Any]?
    if options.execute {
        let receiptURL = URL(fileURLWithPath: options.preflightReceipt)
        var info = stat()
        guard options.preflightReceipt.withCString({ lstat($0, &info) }) == 0,
              (info.st_mode & S_IFMT) == S_IFREG,
              let data = try? Data(contentsOf: receiptURL),
              SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == options.preflightHash,
              let parsed = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw TransportFailure(phase: phase, message: "preflight receipt is missing, changed, or not valid JSON")
        }
        prior = parsed
        guard let selected = (parsed["window"] as? [String: Any])?["identity"] as? String,
              selected.allSatisfy({ $0.isNumber }) else {
            throw TransportFailure(phase: phase, message: "preflight receipt has no valid selected AX identity")
        }
        options.windowIdentity = selected
    }
    let matches = try focusWindows(app).filter { String(CFHash($0)) == options.windowIdentity }
    try require(matches.count == 1, "selected window identity is missing or ambiguous")
    let window = matches[0]
    let record = try focusWindowRecord(window, app: app, pid: options.pid, generation: gen)
    let timestamp = ISO8601DateFormatter()
    timestamp.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if !options.execute {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let snapshotDate = formatter.date(from: options.snapshotObservedAtUTC),
              let snapshotLaunch = formatter.date(from: options.snapshotLaunchDate) else {
            throw TransportFailure(phase: phase, message: "AX snapshot observation or launch date is invalid")
        }
        let snapshotAge = Date().timeIntervalSince(snapshotDate)
        let snapshotGeneration = Int64((snapshotLaunch.timeIntervalSince1970 * 1_000).rounded(.towardZero))
        guard let processLaunch = process.launchDate else {
            throw TransportFailure(phase: phase, message: "Live launch date is unavailable")
        }
        let processGenerationAtSnapshotPrecision = Int64((processLaunch.timeIntervalSince1970 * 1_000).rounded(.towardZero))
        try require(snapshotAge >= -2 && snapshotAge <= 30, "AX snapshot is stale or from the future")
        try require(snapshotGeneration == processGenerationAtSnapshotPrecision && processGenerationAtSnapshotPrecision == gen / 1_000,
                    "AX snapshot belongs to a different Live process generation")
        return focusWindowPreflightReport(
            observedAt: timestamp.string(from: Date()),
            snapshotObservedAt: options.snapshotObservedAtUTC,
            snapshotLaunchDate: options.snapshotLaunchDate,
            pid: options.pid,
            bundleID: options.bundleID,
            generation: gen,
            setURL: url.absoluteString,
            setWindowIdentity: setWindowIdentity,
            window: record
        )
    }
    guard let prior,
          focusReceiptIsCurrent(prior, pid: options.pid, bundleID: options.bundleID,
                                generation: gen, setURL: url.absoluteString,
                                setWindowIdentity: setWindowIdentity,
                                identity: options.windowIdentity, currentWindow: record, now: Date()) else {
        throw TransportFailure(phase: phase, message: "preflight is stale or target window identity, bounds or metadata changed")
    }
    try require(currentProcessMatches(options.pid, bundleID: options.bundleID), "Live process changed before focus")
    try require(processGeneration(process) == gen, "Live process generation changed before focus")
    let pinnedRecord = record
    try raiseExactFocusWindow(app, process: process, pid: options.pid, bundleID: options.bundleID,
                              generation: gen, url: url, setWindowIdentity: setWindowIdentity,
                              identity: options.windowIdentity, pinnedRecord: pinnedRecord)
    try require(currentProcessMatches(options.pid, bundleID: options.bundleID) && processGeneration(process) == gen,
                "Live process changed during focus")
    let freshSetWindow = try exactFocusSetWindow(app, url: url)
    try require(CFHash(freshSetWindow) == CFHash(setWindow), "exact current Set window identity changed during focus")
    let afterWindow = try selectedFocusWindow(app, identity: options.windowIdentity)
    let after = try focusWindowRecord(afterWindow, app: app, pid: options.pid, generation: gen)
    try require(NSDictionary(dictionary: record).isEqual(to: after) && frontmost(options.pid) && focusedWindowIs(app, window),
                "exact target window changed or did not remain focused")
    return ["format": "codex-devtools-live-focus-window-result-v1", "status": "complete",
            "read_only": false, "target": ["pid": options.pid, "bundle_identifier": options.bundleID, "process_generation": gen], "set_url": url.absoluteString,
            "set_window_identity": setWindowIdentity,
            "preflight_sha256": options.preflightHash, "window": after,
            "actions": ["activate": "known", "raise": "known"]]
}

func positionWindowAction(_ app: AXUIElement, process: NSRunningApplication, url: URL) throws -> [String: Any] {
    phase = "position-window-preflight"
    let generation = try processGeneration(process)
    try require(options.expectedGeneration == generation, "Live process generation differs from the caller's inspection")
    let setWindow = try exactFocusSetWindow(app, url: url)
    let setWindowIdentity = String(CFHash(setWindow))
    var prior: [String: Any]?
    if options.execute {
        let receiptURL = URL(fileURLWithPath: options.preflightReceipt)
        var info = stat()
        guard options.preflightReceipt.withCString({ lstat($0, &info) }) == 0,
              (info.st_mode & S_IFMT) == S_IFREG,
              let data = try? Data(contentsOf: receiptURL),
              positionReceiptHashMatches(data, expected: options.preflightHash),
              let parsed = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let selected = (parsed["window"] as? [String: Any])?["identity"] as? String,
              !selected.isEmpty, selected.allSatisfy({ $0.isNumber }),
              let pinned = parsed["requested_position"] as? [String: NSNumber],
              let x = pinned["x"], let y = pinned["y"], x.doubleValue.isFinite, y.doubleValue.isFinite else {
            throw TransportFailure(phase: phase, message: "position preflight receipt is missing, changed, or invalid")
        }
        options.positionX = x.doubleValue
        options.positionY = y.doubleValue
        options.windowIdentity = selected
        prior = parsed
    }
    guard let requestedX = options.positionX, let requestedY = options.positionY,
          requestedX.isFinite, requestedY.isFinite else {
        throw TransportFailure(phase: phase, message: "finite pinned destination X/Y are required")
    }
    let requested = ["x": requestedX, "y": requestedY]
    let matches = try focusWindows(app).filter { String(CFHash($0)) == options.windowIdentity }
    try require(matches.count == 1, "selected window identity is missing or ambiguous")
    let window = matches[0]
    let identity = String(CFHash(window))
    let record = try focusWindowRecord(window, app: app, pid: options.pid, generation: generation)
    _ = try positionIsSettable(window)
    guard let currentGeometry = record["geometry"] as? [String: Double] else {
        throw TransportFailure(phase: phase, message: "selected window geometry is invalid")
    }
    let destination = CGPoint(x: requestedX, y: requestedY)
    let size = CGSize(width: currentGeometry["width"] ?? 0, height: currentGeometry["height"] ?? 0)
    try require(try currentActiveDisplayContains(destination, size: size), "requested window rectangle is outside current displays")
    let timestamp = ISO8601DateFormatter()
    timestamp.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if !options.execute {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let snapshotDate = formatter.date(from: options.snapshotObservedAtUTC),
              let snapshotLaunch = formatter.date(from: options.snapshotLaunchDate),
              let processLaunch = process.launchDate else {
            throw TransportFailure(phase: phase, message: "AX snapshot or Live launch date is invalid")
        }
        let snapshotAge = Date().timeIntervalSince(snapshotDate)
        let snapshotGeneration = Int64((snapshotLaunch.timeIntervalSince1970 * 1_000).rounded(.towardZero))
        let processGenerationAtSnapshotPrecision = Int64((processLaunch.timeIntervalSince1970 * 1_000).rounded(.towardZero))
        try require(snapshotAge >= -2 && snapshotAge <= 30, "AX snapshot is stale or from the future")
        try require(snapshotGeneration == processGenerationAtSnapshotPrecision && processGenerationAtSnapshotPrecision == generation / 1_000,
                    "AX snapshot belongs to a different Live process generation")
        return positionWindowReport(
            observedAt: timestamp.string(from: Date()), snapshotObservedAt: options.snapshotObservedAtUTC,
            snapshotLaunchDate: options.snapshotLaunchDate, pid: options.pid, bundleID: options.bundleID,
            generation: generation, setURL: url.absoluteString, setWindowIdentity: setWindowIdentity,
            window: record, position: requested
        )
    }
    guard let prior,
          positionWindowIsCurrent(prior, pid: options.pid, bundleID: options.bundleID,
                                  generation: generation, setURL: url.absoluteString,
                                  setWindowIdentity: setWindowIdentity, identity: identity,
                                  currentWindow: record, position: requested, now: Date()) else {
        throw TransportFailure(phase: phase, message: "position preflight is stale or Live, Set, window or geometry changed")
    }
    try require(currentProcessMatches(options.pid, bundleID: options.bundleID) && processGeneration(process) == generation,
                "Live process changed before window positioning")
    try require(CFHash(try exactFocusSetWindow(app, url: url)) == CFHash(setWindow), "exact current Set window changed before positioning")
    _ = try positionIsSettable(window)
    try require(try currentActiveDisplayContains(destination, size: size), "requested window rectangle left current displays")
    let latest = try focusWindowRecord(window, app: app, pid: options.pid, generation: generation)
    try require(NSDictionary(dictionary: record).isEqual(to: latest), "selected window changed immediately before positioning")
    try require(currentProcessMatches(options.pid, bundleID: options.bundleID) &&
                processGeneration(process) == generation &&
                CFHash(try exactFocusSetWindow(app, url: url)) == CFHash(setWindow),
                "Live process or exact Set changed immediately before positioning")
    _ = try positionIsSettable(window)

    phase = "position-window-write"
    var point = destination
    guard let value = AXValueCreate(.cgPoint, &point) else {
        throw TransportFailure(phase: phase, message: "AXPosition value could not be created")
    }
    setAction("position", outcome: "unknown")
    AXUIElementSetMessagingTimeout(window, 2.0)
    let writeResult = AXUIElementSetAttributeValue(window, kAXPositionAttribute as CFString, value)
    guard writeResult == .success else {
        throw TransportFailure(phase: phase, message: "AXPosition setter outcome is uncertain (\(writeResult.rawValue)); inspect manually")
    }

    phase = "position-window-readback"
    try require(currentProcessMatches(options.pid, bundleID: options.bundleID) && processGeneration(process) == generation,
                "Live process changed during window positioning")
    try require(CFHash(try exactFocusSetWindow(app, url: url)) == CFHash(setWindow), "exact current Set window changed during positioning")
    let afterWindow = try selectedFocusWindow(app, identity: identity)
    let after = try focusWindowRecord(afterWindow, app: app, pid: options.pid, generation: generation)
    guard let afterGeometry = after["geometry"] as? [String: Double] else {
        throw TransportFailure(phase: phase, message: "positioned window geometry is unavailable")
    }
    try require(positionReadbackMatches(afterGeometry, requested: requested, originalSize: size),
                "window position or size did not match the requested readback")
    try require(after["role"] as? String == record["role"] as? String &&
                after["subrole"] as? String == record["subrole"] as? String &&
                after["title"] as? String == record["title"] as? String &&
                after["identifier"] as? String == record["identifier"] as? String &&
                after["document"] as? String == record["document"] as? String,
                "selected window identity or metadata changed during positioning")
    let finalPosition = CGPoint(x: afterGeometry["x"] ?? .nan, y: afterGeometry["y"] ?? .nan)
    let finalSize = CGSize(width: afterGeometry["width"] ?? .nan, height: afterGeometry["height"] ?? .nan)
    try require(try currentActiveDisplayContains(finalPosition, size: finalSize), "positioned window is outside current displays")
    setAction("position", outcome: "known")
    return positionWindowResultReport(
        pid: options.pid, bundleID: options.bundleID, generation: generation,
        setURL: url.absoluteString, setWindowIdentity: setWindowIdentity,
        preflightHash: options.preflightHash, before: record, after: after, position: requested
    )
}

func positionWindowIsCurrent(_ prior: [String: Any], pid: pid_t, bundleID: String,
                             generation: Int64, setURL: String, setWindowIdentity: String,
                             identity: String, currentWindow: [String: Any],
                             position: [String: Double], now: Date) -> Bool {
    guard prior["format"] as? String == "codex-devtools-live-position-window-preflight-v1",
          prior["status"] as? String == "preflight_ready",
          prior["read_only"] as? Bool == true,
          prior["set_url"] as? String == setURL,
          (prior["requested_position"] as? NSDictionary)?.isEqual(to: position) == true,
          prior["position_settable"] as? Bool == true,
          let target = prior["target"] as? [String: Any],
          (target["pid"] as? NSNumber)?.intValue == Int(pid),
          target["bundle_identifier"] as? String == bundleID,
          (target["process_generation"] as? NSNumber)?.int64Value == generation,
          prior["set_window_identity"] as? String == setWindowIdentity,
          let priorWindow = prior["window"] as? [String: Any],
          priorWindow["identity"] as? String == identity,
          NSDictionary(dictionary: priorWindow).isEqual(to: currentWindow) else { return false }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let observed = formatter.date(from: prior["observed_at_utc"] as? String ?? "") else { return false }
    let age = now.timeIntervalSince(observed)
    return age >= -2 && age <= 30
}

func positionReceiptHashMatches(_ data: Data, expected: String) -> Bool {
    guard expected.count == 64 else { return false }
    return SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == expected
}

func positionWindowReport(observedAt: String, snapshotObservedAt: String,
                          snapshotLaunchDate: String, pid: pid_t, bundleID: String,
                          generation: Int64, setURL: String, setWindowIdentity: String,
                          window: [String: Any], position: [String: Double]) -> [String: Any] {
    [
        "format": "codex-devtools-live-position-window-preflight-v1",
        "status": "preflight_ready", "read_only": true,
        "observed_at_utc": observedAt,
        "snapshot_observed_at_utc": snapshotObservedAt,
        "snapshot_launch_date": snapshotLaunchDate,
        "target": ["pid": pid, "bundle_identifier": bundleID, "process_generation": generation],
        "set_url": setURL, "set_window_identity": setWindowIdentity,
        "window": window, "requested_position": position,
        "position_settable": true, "actions": [:],
        "manual_inspection_required": false,
    ]
}

func positionWindowResultReport(pid: pid_t, bundleID: String, generation: Int64,
                                setURL: String, setWindowIdentity: String,
                                preflightHash: String, before: [String: Any],
                                after: [String: Any], position: [String: Double]) -> [String: Any] {
    [
        "format": "codex-devtools-live-position-window-result-v1", "status": "complete",
        "read_only": false,
        "target": ["pid": pid, "bundle_identifier": bundleID, "process_generation": generation],
        "set_url": setURL, "set_window_identity": setWindowIdentity,
        "preflight_sha256": preflightHash, "window": before, "window_after": after,
        "requested_position": position, "position_settable": true,
        "actions": ["position": "known"], "manual_inspection_required": false,
    ]
}

func positionReadbackMatches(_ actual: [String: Double], requested: [String: Double],
                             originalSize: CGSize) -> Bool {
    guard let x = actual["x"], let y = actual["y"],
          let width = actual["width"], let height = actual["height"],
          let targetX = requested["x"], let targetY = requested["y"],
          [x, y, width, height, targetX, targetY,
           originalSize.width, originalSize.height].allSatisfy(\.isFinite) else { return false }
    return abs(x - targetX) < 0.5 && abs(y - targetY) < 0.5 &&
        abs(width - originalSize.width) < 0.001 &&
        abs(height - originalSize.height) < 0.001
}

func positionFitsDisplay(_ position: CGPoint, size: CGSize, displays: [CGRect]) -> Bool {
    guard position.x.isFinite, position.y.isFinite,
          size.width.isFinite, size.height.isFinite,
          size.width > 0, size.height > 0,
          !displays.isEmpty, displays.count <= 16 else { return false }
    let target = CGRect(origin: position, size: size)
    guard target.minX.isFinite, target.minY.isFinite, target.maxX.isFinite, target.maxY.isFinite else { return false }
    return displays.contains { display in
        display.minX.isFinite && display.minY.isFinite && display.maxX.isFinite && display.maxY.isFinite &&
        display.width > 0 && display.height > 0 && display.contains(target)
    }
}

func currentActiveDisplayContains(_ position: CGPoint, size: CGSize) throws -> Bool {
    var count: UInt32 = 0
    guard CGGetActiveDisplayList(0, nil, &count) == .success, count > 0, count <= 16 else {
        throw TransportFailure(phase: phase, message: "active display inventory is unavailable or outside its bound")
    }
    var identifiers = [CGDirectDisplayID](repeating: 0, count: Int(count))
    guard CGGetActiveDisplayList(count, &identifiers, &count) == .success, count > 0 else {
        throw TransportFailure(phase: phase, message: "active display inventory changed or could not be read")
    }
    return positionFitsDisplay(position, size: size, displays: identifiers.prefix(Int(count)).map(CGDisplayBounds))
}

func positionIsSettable(_ window: AXUIElement) throws -> Bool {
    var settable = DarwinBoolean(false)
    AXUIElementSetMessagingTimeout(window, 2.0)
    let error = AXUIElementIsAttributeSettable(window, kAXPositionAttribute as CFString, &settable)
    return try positionSettableResult(error: error, settable: settable.boolValue)
}

func positionSettableResult(error: AXError, settable: Bool) throws -> Bool {
    try require(error == .success, "AXPosition settable status is unavailable (\(error.rawValue))")
    try require(settable, "selected window AXPosition is not settable")
    return true
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
    if options.action == "focus-window" {
        return try focusWindowAction(app, process: process, url: pathURL)
    }
    if options.action == "position-window" {
        return try positionWindowAction(app, process: process, url: pathURL)
    }
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
            let ackDeadline = acknowledgementDeadline(
                maxSeconds: 2.0,
                cappedBy: DispatchTime.now().uptimeNanoseconds + 2_000_000_000
            )
            let stopAcknowledged = try waitForTransportValue(
                0,
                element: after["Play"]!.element,
                controlName: "Play",
                app: app,
                pid: options.pid,
                bundleID: options.bundleID,
                window: freshWindow,
                url: pathURL,
                title: setTitle,
                deadline: ackDeadline
            )
            receipt["stop_play_readback"] = stopAcknowledged ? "verified" : "not_observed_before_bound"
            try require(stopAcknowledged, "Stop did not read back Play off within the acknowledgement bound")
        }
        let finalState = controlsState(try exactControls(try exactSetWindow(app, url: pathURL)))
        if actions["stop"]?["attempted"] as? Bool == true {
            try require(finalState["play"] as? Int == 0, "Play changed after Stop acknowledgement")
            setAction("stop", outcome: "known")
        }
        var result = transportReport(
            status: "complete", phase: "complete", action: "stop", readOnly: false,
            pid: options.pid, bundleID: options.bundleID, processName: process.localizedName ?? "",
            setURL: pathURL, title: setTitle, transport: finalState
        )
        if let readback = receipt["stop_play_readback"] {
            result["stop_play_readback"] = readback
        }
        return result
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
        let playAckDeadline = acknowledgementDeadline(maxSeconds: 2.0, cappedBy: deadline)
        let playAcknowledged = try waitForTransportValue(
            1,
            element: playControl,
            controlName: "Play",
            app: app,
            pid: options.pid,
            bundleID: options.bundleID,
            window: setWindow,
            url: pathURL,
            title: setTitle,
            deadline: playAckDeadline
        )
        receipt["play_readback"] = playAcknowledged ? "verified" : "not_observed_before_bound"
        guard playAcknowledged else {
            setAction("play", outcome: "unknown", detail: "Play did not read back on within the acknowledgement bound")
            throw TransportFailure(phase: phase, message: "Play readback did not confirm on before the acknowledgement bound")
        }
        setAction("play", outcome: "known")
    } catch {
        // Once Play is attempted, the independent deadline Stop still runs even
        // if the AX call or immediate readback has an uncertain outcome.
        receipt["play_error"] = String(describing: error)
        if actions["play"]?["attempted"] as? Bool == true,
           receipt["play_readback"] == nil {
            receipt["play_readback"] = "unknown"
        }
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
        pumpRunLoop(until: deadline)
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
        let stopAckDeadline = acknowledgementDeadline(maxSeconds: 2.0, cappedBy: DispatchTime.now().uptimeNanoseconds + 2_000_000_000)
        let stopAcknowledged = try waitForTransportValue(
            0,
            element: beforeStop["Play"]!.element,
            controlName: "Play",
            app: app,
            pid: options.pid,
            bundleID: options.bundleID,
            window: setWindow,
            url: pathURL,
            title: setTitle,
            deadline: stopAckDeadline
        )
        receipt["stop_play_readback"] = stopAcknowledged ? "verified" : "not_observed_before_bound"
        try require(stopAcknowledged, "timed Stop did not read back Play off within the acknowledgement bound")
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
            if receipt["stop_play_readback"] == nil {
                receipt["stop_play_readback"] = "unknown"
            }
        } else {
            setAction("stop", outcome: "not_attempted", detail: String(describing: error))
            manualInspectionRequired = true
        }
        receipt["status"] = "stopped"
        receipt["phase"] = phase
        let stopError = String(describing: error)
        receipt["stop_error"] = stopError
        if let playError = receipt["play_error"] as? String {
            receipt["error"] = "Play attempt: \(playError); Stop cleanup: \(stopError)"
        } else {
            receipt["error"] = stopError
        }
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
    if options.action == "position-window" && options.execute {
        actions = ["position": ["attempted": false, "outcome": "not_attempted"]]
    } else if options.execute {
        actions = [
            "settings_close": ["attempted": false, "outcome": "not_attempted"],
            "play": ["attempted": false, "outcome": "not_attempted"],
            "stop": ["attempted": false, "outcome": "not_attempted"],
        ]
    }
    let result = try run()
    let success = successfulHelperStatus(result["status"] as? String)
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
    if actions["stop"]?["attempted"] as? Bool == true,
       result["stop_play_readback"] == nil {
        result["stop_play_readback"] = "unknown"
    }
    result["manual_inspection_required"] = manualInspectionRequired
    result["error"] = String(describing: error)
    if options.action == "play" { result["play_for_seconds"] = options.playForSeconds }
    if !attempted { result["manual_inspection_required"] = false }
    emit(result, code: 1)
}
