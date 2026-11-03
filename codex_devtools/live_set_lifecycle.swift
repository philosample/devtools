// Native Accessibility Save As/Quit/LaunchServices reopen for one exact Live Set.
import ApplicationServices
import AppKit
import Darwin
import Foundation

struct Options {
    var pid: pid_t = 0
    var bundleID = ""
    var sourceSet = ""
    var destinationSet: String?
    var appPath: String?
    var execute = false
}

struct LifecycleFailure: Error {
    let phase: String
    let message: String
}

var phase = "arguments"
var saveAttempted = false
var saveAsMenuAttempted = false
var quitAttempted = false
var openAttempted = false
var menuNavigationAttempted = false
var menuNavigationVerified = false
var focusAttempted = false
var focusVerified = false
var uiActionAttempted = false
var initialPID: Int?
var initialBundleID: String?
var menuDiagnostics: [String: Any]?
var activeFileMenu: AXUIElement?

let menuReadinessTimeout: TimeInterval = 2

func bounded(_ value: String?, limit: Int = 256) -> Any {
    guard let value else { return NSNull() }
    return String(value.prefix(limit))
}

func attributeBool(_ element: AXUIElement?, _ name: String) -> Bool? {
    guard let element, let value = optionalAX(element, name) as? NSNumber else { return nil }
    return value.boolValue
}

func isBlockingWindow(_ element: AXUIElement, except allowedTitle: String? = nil) -> Bool {
    let elementRole = (try? role(element)) ?? ""
    let elementTitle = title(element)
    let elementIdentifier = identifier(element)
    if elementRole == (kAXWindowRole as String), elementTitle == allowedTitle, allowedTitle != nil {
        return false
    }
    if ["AXDialog", "AXSheet"].contains(elementRole) {
        return true
    }
    if attributeBool(element, "AXModal") == true {
        return true
    }
    return elementRole == (kAXWindowRole as String) &&
        elementIdentifier == "open-panel"
}

func elementSummary(_ element: AXUIElement?, blocking: Bool = false) -> [String: Any]? {
    guard let element else { return nil }
    let actionList = (try? actionNames(element)) ?? []
    let focusedValue = optionalAX(element, kAXDocumentAttribute as String) as? String
    return [
        "role": bounded(try? role(element)),
        "title": bounded(title(element)),
        "identifier": bounded(identifier(element)),
        "document_url": bounded(focusedValue, limit: 512),
        "modal": attributeBool(element, "AXModal") as Any? ?? NSNull(),
        "enabled": attributeBool(element, kAXEnabledAttribute as String) as Any? ?? NSNull(),
        "selected": attributeBool(element, kAXSelectedAttribute as String) as Any? ?? NSNull(),
        "expanded": attributeBool(element, kAXExpandedAttribute as String) as Any? ?? NSNull(),
        "press_action": actionList.contains(kAXPressAction as String),
        "actions": actionList.prefix(16).map { String($0.prefix(128)) },
        "blocking": blocking,
    ]
}

func captureMenuDiagnostics(app: AXUIElement, pid: pid_t, menu: AXUIElement?, item: AXUIElement?,
                            ownedWindow: AXUIElement? = nil) {
    let readWindows = try? windows(app)
    let appWindows = readWindows ?? []
    let blockers = appWindows.prefix(32).compactMap { window -> [String: Any]? in
        guard isBlockingWindow(window) else { return nil }
        return elementSummary(window, blocking: true)
    }
    let focusedWindow = optionalAX(app, kAXFocusedWindowAttribute as String)
    let focusedElement: AXUIElement?
    if let focusedWindow, CFGetTypeID(focusedWindow) == AXUIElementGetTypeID() {
        focusedElement = (focusedWindow as! AXUIElement)
    } else {
        focusedElement = nil
    }
    let focusedSummary: Any
    if let focusedElement {
        focusedSummary = elementSummary(focusedElement) ?? NSNull()
    } else {
        focusedSummary = NSNull()
    }
    let frontmostPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
    let timestamp = ISO8601DateFormatter().string(from: Date())
    menuDiagnostics = [
        "phase": phase,
        "observed_at_utc": timestamp,
        "frontmost_pid": frontmostPID as Any? ?? NSNull(),
        "expected_pid": Int(pid),
        "focused_window": focusedSummary,
        "owned_window": elementSummary(ownedWindow) as Any? ?? NSNull(),
        "focused_window_matches_owned": ownedWindow.map { window in
            focusedElement.map { CFEqual($0, window) } ?? false
        } as Any? ?? NSNull(),
        "blocking_windows": blockers,
        "windows_complete": readWindows != nil && appWindows.count <= 32,
        "file_menu": elementSummary(menu) as Any? ?? NSNull(),
        "save_as_item": elementSummary(item) as Any? ?? NSNull(),
    ]
}

func fail(_ message: String) -> Never {
    fputs(message + "\n", stderr)
    exit(2)
}

func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw LifecycleFailure(phase: phase, message: message) }
}

func parseOptions() -> Options {
    var result = Options()
    var index = 1
    let args = CommandLine.arguments
    while index < args.count {
        let key = args[index]
        if key == "--help" {
            print("Usage: live_set_lifecycle.swift --pid PID --bundle-id ID --source-set /path/Source.als [--destination-set /path/Copy.als --app /Applications/Live.app --execute]")
            exit(0)
        }
        if key == "--execute" {
            result.execute = true
            index += 1
            continue
        }
        guard index + 1 < args.count else { fail("missing value for \(key)") }
        let value = args[index + 1]
        switch key {
        case "--pid":
            guard let parsed = Int32(value), parsed > 0 else { fail("PID must be positive") }
            result.pid = pid_t(parsed)
        case "--bundle-id": result.bundleID = value
        case "--source-set": result.sourceSet = value
        case "--destination-set": result.destinationSet = value
        case "--app": result.appPath = value
        default: fail("unknown option: \(key)")
        }
        index += 2
    }
    guard result.pid > 0, !result.bundleID.isEmpty, !result.sourceSet.isEmpty else {
        fail("--pid, --bundle-id and --source-set are required")
    }
    if result.execute && (result.destinationSet == nil || result.appPath == nil) {
        fail("--execute requires --destination-set and --app")
    }
    return result
}

func ax(_ element: AXUIElement, _ name: String) throws -> CFTypeRef {
    var value: CFTypeRef?
    AXUIElementSetMessagingTimeout(element, 2)
    let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    guard error == .success, let value else {
        throw LifecycleFailure(phase: phase, message: "AX read \(name) failed (\(error.rawValue))")
    }
    return value
}

func optionalAX(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    try? ax(element, name)
}

func stringAttribute(_ element: AXUIElement, _ name: String) throws -> String {
    guard let value = try ax(element, name) as? String else {
        throw LifecycleFailure(phase: phase, message: "AX \(name) is not a string")
    }
    return value
}

func role(_ element: AXUIElement) throws -> String { try stringAttribute(element, kAXRoleAttribute as String) }

func title(_ element: AXUIElement) -> String? {
    optionalAX(element, kAXTitleAttribute as String) as? String
}

func identifier(_ element: AXUIElement) -> String? {
    optionalAX(element, "AXIdentifier") as? String
}

func children(_ element: AXUIElement) throws -> [AXUIElement] {
    var value: CFTypeRef?
    AXUIElementSetMessagingTimeout(element, 2)
    let error = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &value)
    if error == .noValue { return [] }
    guard error == .success, let raw = value else {
        throw LifecycleFailure(phase: phase, message: "AX children read failed (\(error.rawValue))")
    }
    guard let list = raw as? [AXUIElement] else {
        throw LifecycleFailure(phase: phase, message: "AX children is not an element array")
    }
    return list
}

func allElements(_ roots: [AXUIElement], maxDepth: Int = 24, maxElements: Int = 4096) throws -> [AXUIElement] {
    var output: [AXUIElement] = []
    var queue = roots.map { ($0, 0) }
    var seen = Set<CFHashCode>()
    while !queue.isEmpty {
        let (element, depth) = queue.removeFirst()
        let hash = CFHash(element)
        guard seen.insert(hash).inserted else { continue }
        output.append(element)
        try require(output.count <= maxElements, "AX subtree exceeded element bound")
        if depth < maxDepth {
            for child in try children(element) { queue.append((child, depth + 1)) }
        } else if !(try children(element)).isEmpty {
            throw LifecycleFailure(phase: phase, message: "AX subtree exceeded depth bound")
        }
    }
    return output
}

func windows(_ app: AXUIElement) throws -> [AXUIElement] {
    let raw = try ax(app, kAXWindowsAttribute as String)
    guard let list = raw as? [AXUIElement], !list.isEmpty else {
        throw LifecycleFailure(phase: phase, message: "target has no readable AX windows")
    }
    return list
}

func lexicalPath(_ path: String) throws -> String {
    let parts = path.split(separator: "/", omittingEmptySubsequences: false)
    guard path.hasPrefix("/"), !path.hasSuffix("/"), parts.count >= 2, parts[0].isEmpty,
          parts.dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }),
          !path.contains("\\"), !path.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else {
        throw LifecycleFailure(phase: phase, message: "normalized absolute path required")
    }
    return path
}

func canonicalURL(_ path: String) throws -> URL {
    let safe = try lexicalPath(path)
    guard safe.lowercased().hasSuffix(".als") else {
        throw LifecycleFailure(phase: phase, message: "absolute .als path required")
    }
    return URL(fileURLWithPath: safe)
}

func requireAbsentLeaf(_ path: String, _ message: String) throws {
    var info = stat()
    let result = path.withCString { lstat($0, &info) }
    if result == 0 {
        throw LifecycleFailure(phase: phase, message: message)
    }
    guard errno == ENOENT else {
        throw LifecycleFailure(phase: phase, message: "cannot verify destination absence: \(path) (errno \(errno))")
    }
}

func ensureNoSymlinkChain(_ path: URL, mustExist: Bool, outsideGit: Bool) throws {
    let file = path.path
    var cursor = URL(fileURLWithPath: "/")
    let parts = file.split(separator: "/").map(String.init)
    let directories = mustExist ? Array(parts.dropLast()) : Array(parts.dropLast())
    for part in directories {
        cursor.appendPathComponent(part, isDirectory: true)
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: cursor.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw LifecycleFailure(phase: phase, message: "path directory does not exist: \(cursor.path)")
        }
        let attributes = try FileManager.default.attributesOfItem(atPath: cursor.path)
        try require(attributes[.type] as? FileAttributeType == .typeDirectory, "directory component is not a real directory")
        let kind = try cursor.resourceValues(forKeys: [.isSymbolicLinkKey])
        try require(kind.isSymbolicLink != true, "symbolic link component is unsupported")
        if outsideGit {
            try require(!FileManager.default.fileExists(atPath: cursor.appendingPathComponent(".git").path), "destination must be outside Git")
        }
    }
    if mustExist {
        let attrs = try FileManager.default.attributesOfItem(atPath: file)
        try require(attrs[.type] as? FileAttributeType == .typeRegular, "source Set must be a regular file")
        let kind = try path.resourceValues(forKeys: [.isSymbolicLinkKey])
        try require(kind.isSymbolicLink != true, "source Set cannot be a symbolic link")
    } else {
        try requireAbsentLeaf(file, "destination already exists or is a symbolic link")
        try requireAbsentLeaf(file + ".als", "destination collision or symbolic link")
    }
}

func fileURLString(_ raw: String) throws -> String {
    guard let url = URL(string: raw), url.isFileURL, url.query == nil, url.fragment == nil else {
        throw LifecycleFailure(phase: phase, message: "AX document value is not a local file URL")
    }
    let path = try lexicalPath(url.path)
    return URL(fileURLWithPath: path).absoluteString
}

func sameSetURL(_ raw: String, expected: URL) throws -> Bool {
    let reported = try fileURLString(raw)
    if reported == expected.absoluteString { return true }
    guard let reportedURL = URL(string: reported) else { return false }
    let reportedPath = reportedURL.path
    let expectedPath = expected.path
    let privatePrefix = "/private/tmp/"
    let publicPrefix = "/tmp/"
    let aliasMatches =
        (expectedPath.hasPrefix(privatePrefix) &&
         reportedPath == publicPrefix + expectedPath.dropFirst(privatePrefix.count)) ||
        (reportedPath.hasPrefix(privatePrefix) &&
         expectedPath == publicPrefix + reportedPath.dropFirst(privatePrefix.count))
    guard aliasMatches else { return false }
    let expectedAttributes = try FileManager.default.attributesOfItem(atPath: expectedPath)
    let reportedAttributes = try FileManager.default.attributesOfItem(atPath: reportedPath)
    return expectedAttributes[.systemNumber] as? NSNumber == reportedAttributes[.systemNumber] as? NSNumber &&
        expectedAttributes[.systemFileNumber] as? NSNumber == reportedAttributes[.systemFileNumber] as? NSNumber
}

func exactDocumentWindow(_ app: AXUIElement, url: URL) throws -> AXUIElement {
    var matches: [AXUIElement] = []
    for window in try windows(app) {
        guard (try? role(window)) == (kAXWindowRole as String) else { continue }
        guard let document = optionalAX(window, kAXDocumentAttribute as String) as? String else { continue }
        if try sameSetURL(document, expected: url) { matches.append(window) }
    }
    try require(matches.count == 1, "expected exactly one AX window for the exact Set URL")
    return matches[0]
}

func ensureNoDialogs(_ app: AXUIElement, except allowedTitle: String? = nil) throws {
    let appWindows = try windows(app)
    let dialogs = appWindows.filter { isBlockingWindow($0, except: allowedTitle) }
    try require(dialogs.isEmpty, "unexpected blocking dialog, sheet or file panel")
    let allowed = appWindows.filter { (try? role($0)) == (kAXWindowRole as String) && title($0) == allowedTitle }
    try require(allowedTitle == nil || allowed.count == 1, "unexpected or ambiguous dialog window")
}

func fileIdentity(_ path: URL) throws -> [FileAttributeKey: Any] {
    try FileManager.default.attributesOfItem(atPath: path.path)
}

func sameFileIdentity(_ left: [FileAttributeKey: Any], _ right: [FileAttributeKey: Any]) -> Bool {
    left[.systemNumber] as? NSNumber == right[.systemNumber] as? NSNumber &&
    left[.systemFileNumber] as? NSNumber == right[.systemFileNumber] as? NSNumber &&
    left[.size] as? NSNumber == right[.size] as? NSNumber &&
    left[.modificationDate] as? Date == right[.modificationDate] as? Date
}

func actionNames(_ element: AXUIElement) throws -> [String] {
    var names: CFArray?
    AXUIElementSetMessagingTimeout(element, 2)
    let error = AXUIElementCopyActionNames(element, &names)
    guard error == .success, let names, let strings = names as? [String] else {
        throw LifecycleFailure(phase: phase, message: "AX action list unavailable (\(error.rawValue))")
    }
    return strings
}

func press(_ element: AXUIElement) throws {
    try require((optionalAX(element, kAXEnabledAttribute as String) as? NSNumber)?.boolValue == true, "target AX element is not enabled")
    let actions = try actionNames(element)
    try require(actions.contains(kAXPressAction as String), "target AX element does not support AXPress")
    AXUIElementSetMessagingTimeout(element, 2)
    uiActionAttempted = true
    if (try role(element)) == (kAXMenuBarItemRole as String) { menuNavigationAttempted = true }
    let error = AXUIElementPerformAction(element, kAXPressAction as CFString)
    try require(error == .success, "AXPress failed (\(error.rawValue))")
}

func uniqueElement(_ elements: [AXUIElement], role expectedRole: String, title expectedTitle: String?, identifier expectedID: String? = nil) throws -> AXUIElement {
    let matches = try elements.filter {
        try role($0) == expectedRole && (expectedTitle == nil || title($0) == expectedTitle) &&
        (expectedID == nil || identifier($0) == expectedID)
    }
    let label = expectedTitle ?? expectedID ?? "control"
    try require(matches.count == 1, "expected exactly one AX \(expectedRole) \(label)")
    try require((optionalAX(matches[0], kAXEnabledAttribute as String) as? NSNumber)?.boolValue == true, "AX \(label) is disabled")
    return matches[0]
}

func savePanelAttribute(_ element: AXUIElement, _ name: String) throws -> CFTypeRef? {
    var value: CFTypeRef?
    AXUIElementSetMessagingTimeout(element, 2)
    let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    if error == .success { return value }
    if error == .noValue || error == .invalidUIElement { return nil }
    let optionalIdentityAttribute = name == (kAXTitleAttribute as String) || name == "AXIdentifier"
    if error == .attributeUnsupported && optionalIdentityAttribute { return nil }
    throw LifecycleFailure(phase: phase, message: "Save panel AX read \(name) failed (\(error.rawValue))")
}

func savePanelElementDiagnostic(_ element: AXUIElement) -> String {
    func value(_ attribute: String) -> String {
        guard let raw = try? savePanelAttribute(element, attribute),
              let string = raw as? String else { return "unknown" }
        return String(string.prefix(128))
    }
    let roleName = value(kAXRoleAttribute as String)
    let subrole = value(kAXSubroleAttribute as String)
    let elementIdentifier = value("AXIdentifier")
    return "container role=\(roleName) subrole=\(subrole) identifier=\(elementIdentifier)"
}

func isIrrelevantSavePanelBrowserElement(_ roleName: String) -> Bool {
    ["AXScrollArea", "AXTable", "AXOutline", "AXRow", "AXColumn"].contains(roleName)
}

func savePanelElements(_ roots: [AXUIElement], maxDepth: Int = 24, maxElements: Int = 4096) throws -> [AXUIElement] {
    var output: [AXUIElement] = []
    var queue = roots.map { ($0, 0) }
    var seen = Set<CFHashCode>()
    var visitedCount = 0
    while !queue.isEmpty {
        let (element, depth) = queue.removeFirst()
        let hash = CFHash(element)
        guard seen.insert(hash).inserted else { continue }
        visitedCount += 1
        try require(visitedCount <= maxElements, "Save panel AX subtree exceeded element bound")
        guard let rawRole = try savePanelAttribute(element, kAXRoleAttribute as String),
              let elementRole = rawRole as? String else {
            continue // An unavailable role cannot match a required panel control.
        }
        if isIrrelevantSavePanelBrowserElement(elementRole) { continue }
        output.append(element)
        let rawChildren: CFTypeRef?
        do {
            rawChildren = try savePanelAttribute(element, kAXChildrenAttribute as String)
        } catch let error as LifecycleFailure {
            throw LifecycleFailure(phase: phase, message: "\(error.message); \(savePanelElementDiagnostic(element))")
        }
        guard let rawChildren else {
            continue
        }
        guard let children = rawChildren as? [AXUIElement] else {
            throw LifecycleFailure(phase: phase, message: "Save panel AX children is not an element array; \(savePanelElementDiagnostic(element))")
        }
        if depth < maxDepth {
            queue.append(contentsOf: children.map { ($0, depth + 1) })
        } else if !children.isEmpty {
            throw LifecycleFailure(phase: phase, message: "Save panel AX subtree exceeded depth bound")
        }
    }
    return output
}

func savePanelMatches(_ elements: [AXUIElement], role expectedRole: String, title expectedTitle: String?, identifier expectedID: String? = nil) throws -> [AXUIElement] {
    var matches: [AXUIElement] = []
    for element in elements {
        guard let rawRole = try savePanelAttribute(element, kAXRoleAttribute as String),
              let actualRole = rawRole as? String,
              actualRole == expectedRole else { continue }
        if let expectedTitle {
            guard let rawTitle = try savePanelAttribute(element, kAXTitleAttribute as String),
                  (rawTitle as? String) == expectedTitle else { continue }
        }
        if let expectedID {
            guard let rawIdentifier = try savePanelAttribute(element, "AXIdentifier"),
                  (rawIdentifier as? String) == expectedID else { continue }
        }
        matches.append(element)
    }
    return matches
}

func uniqueSavePanelElement(_ elements: [AXUIElement], role expectedRole: String, title expectedTitle: String?, identifier expectedID: String? = nil) throws -> AXUIElement {
    let matches = try savePanelMatches(elements, role: expectedRole, title: expectedTitle, identifier: expectedID)
    let label = expectedTitle ?? expectedID ?? "control"
    try require(matches.count == 1, "expected exactly one Save panel AX \(expectedRole) \(label)")
    try require((optionalAX(matches[0], kAXEnabledAttribute as String) as? NSNumber)?.boolValue == true, "Save panel AX \(label) is disabled")
    return matches[0]
}

func waitForGoToPathField(_ saveWindow: AXUIElement, seconds: TimeInterval = 5) throws -> (sheet: AXUIElement, pathField: AXUIElement) {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        guard let rawChildren = try savePanelAttribute(saveWindow, kAXChildrenAttribute as String),
              let directChildren = rawChildren as? [AXUIElement] else {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
            continue
        }
        let sheets = try savePanelMatches(directChildren, role: "AXSheet", title: nil, identifier: "GoToWindow")
        try require(sheets.count <= 1, "ambiguous Go To Folder AXSheet child")
        if let sheet = sheets.first {
            let sheetElements = try savePanelElements([sheet])
            let pathFields = try savePanelMatches(sheetElements, role: kAXTextFieldRole as String, title: nil, identifier: "PathTextField")
            try require(pathFields.count <= 1, "ambiguous PathTextField in Go To Folder AXSheet")
            if let pathField = pathFields.first {
                try require((optionalAX(pathField, kAXEnabledAttribute as String) as? NSNumber)?.boolValue == true, "Go To Folder PathTextField is disabled")
                return (sheet, pathField)
            }
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    throw LifecycleFailure(phase: phase, message: "timed out waiting for exact GoToWindow sheet child and PathTextField")
}

func waitForGoToSheetDismissed(_ saveWindow: AXUIElement, seconds: TimeInterval = 5) throws {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        if let rawChildren = try savePanelAttribute(saveWindow, kAXChildrenAttribute as String),
           let directChildren = rawChildren as? [AXUIElement] {
            let sheets = try savePanelMatches(directChildren, role: "AXSheet", title: nil, identifier: "GoToWindow")
            try require(sheets.count <= 1, "ambiguous Go To Folder AXSheet child after Return")
            if sheets.isEmpty { return }
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    throw LifecycleFailure(phase: phase, message: "GoToWindow sheet child remained after Return")
}

// Readiness only: never press Save, repeat menu navigation, or repair a drifted panel.
func waitForSaveButtonEnabled(app: AXUIElement, pid: pid_t, bundleID: String,
                              process: NSRunningApplication, saveWindow: AXUIElement,
                              source: URL, originalSourceIdentity: [FileAttributeKey: Any],
                              destination: URL, seconds: TimeInterval = 5) throws -> AXUIElement {
    let deadline = ProcessInfo.processInfo.systemUptime + seconds
    while ProcessInfo.processInfo.systemUptime < deadline {
        try require(!process.isTerminated, "original Live process terminated during Save readiness")
        guard let currentProcess = NSRunningApplication(processIdentifier: pid) else {
            throw LifecycleFailure(phase: phase, message: "exact Live PID disappeared during Save readiness")
        }
        let running = NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == bundleID }
        try require(currentProcess.bundleIdentifier == bundleID && running.count == 1 && running[0].processIdentifier == pid,
                    "Live PID/bundle identity changed during Save readiness")
        try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == pid, "Live lost foreground during Save readiness")
        try ensureNoDialogs(app, except: "Save")
        let panels = try windows(app).filter { (try? role($0)) == (kAXWindowRole as String) && title($0) == "Save" }
        try require(panels.count == 1 && CFEqual(panels[0], saveWindow) && identifier(panels[0]) == "save-panel",
                    "Save panel identity changed during readiness")
        let currentPanel = panels[0]
        try require(focusedWindowIs(app, currentPanel), "Save panel lost focus during readiness")
        let refreshed = try savePanelElements([currentPanel])
        try require(!refreshed.contains { ["Replace", "Allow", "Allow Anyway", "Discard", "Don't Save", "Don’t Save"].contains(title($0) ?? "") }, "unexpected overwrite/security/discard prompt")
        let pathFields = try savePanelMatches(refreshed, role: kAXTextFieldRole as String, title: nil, identifier: "PathTextField")
        let sheets = try savePanelMatches(refreshed, role: "AXSheet", title: nil, identifier: "GoToWindow")
        try require(pathFields.isEmpty && sheets.isEmpty, "Go To Folder sheet/path field remained open")
        let finalName = try uniqueSavePanelElement(refreshed, role: kAXTextFieldRole as String, title: nil, identifier: "saveAsNameTextField")
        try require(stringValue(finalName) == destination.lastPathComponent, "Save As filename changed")
        let finalWhere = try uniqueSavePanelElement(refreshed, role: kAXPopUpButtonRole as String, title: "Where:", identifier: "where popup")
        try require(stringValue(finalWhere) == destination.deletingLastPathComponent().lastPathComponent, "Save As selected folder leaf mismatch")
        let buttons = try savePanelMatches(refreshed, role: kAXButtonRole as String, title: "Save", identifier: "OKButton")
        try require(buttons.count == 1, "expected exactly one Save panel AX Save/OKButton")
        try ensureNoSymlinkChain(destination, mustExist: false, outsideGit: true)
        let sourceBeforeSaveIdentity = try fileIdentity(source)
        try require(sameFileIdentity(originalSourceIdentity, sourceBeforeSaveIdentity), "source Set metadata changed before Save")
        _ = try exactDocumentWindow(app, url: source)
        // File/AX reads above may take time. Recheck process/focus and the positive
        // enabled state before returning a button; no mutation occurs in this loop.
        try require(!process.isTerminated && NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == bundleID,
                    "Live PID changed during Save readiness readback")
        try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == pid && focusedWindowIs(app, currentPanel),
                    "Save panel focus changed during readiness readback")
        if attributeBool(buttons[0], kAXEnabledAttribute as String) == true && ProcessInfo.processInfo.systemUptime < deadline {
            return buttons[0]
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    throw LifecycleFailure(phase: phase, message: "Save panel did not expose an enabled Save/OKButton before the bounded readiness deadline")
}

func menuItemMatches(_ elements: [AXUIElement], _ itemTitle: String) -> [AXUIElement] {
    elements.filter { (try? role($0)) == (kAXMenuItemRole as String) && title($0) == itemTitle }
}

func menuIsPresented(_ menu: AXUIElement) -> Bool {
    attributeBool(menu, kAXExpandedAttribute as String) == true ||
        attributeBool(menu, kAXSelectedAttribute as String) == true
}

func requireFreshMenuFocus(app: AXUIElement, pid: pid_t, sourceWindow: AXUIElement,
                           menu: AXUIElement? = nil, item: AXUIElement? = nil) throws {
    let frontmost = NSWorkspace.shared.frontmostApplication?.processIdentifier == pid
    focusVerified = frontmost && focusedWindowIs(app, sourceWindow)
    if !focusVerified {
        captureMenuDiagnostics(app: app, pid: pid, menu: menu, item: item, ownedWindow: sourceWindow)
        throw LifecycleFailure(phase: phase, message: "exact Live PID and source Set window are not frontmost/focused before menu action")
    }
}

func findMenuItem(app: AXUIElement, pid: pid_t, sourceWindow: AXUIElement, menuTitle: String, itemTitle: String) throws -> AXUIElement {
    menuDiagnostics = nil
    menuNavigationAttempted = false
    menuNavigationVerified = false
    activeFileMenu = nil
    captureMenuDiagnostics(app: app, pid: pid, menu: nil, item: nil, ownedWindow: sourceWindow)
    try requireFreshMenuFocus(app: app, pid: pid, sourceWindow: sourceWindow)
    try ensureNoDialogs(app)
    let menuBar = try ax(app, kAXMenuBarAttribute as String) as! AXUIElement
    let top = try children(menuBar)
    let menu = try uniqueElement(top, role: kAXMenuBarItemRole as String, title: menuTitle)
    activeFileMenu = menu
    var nested = (try? allElements(try children(menu))) ?? []
    var presented = menuIsPresented(menu)
    menuNavigationVerified = presented
    captureMenuDiagnostics(app: app, pid: pid, menu: menu, item: menuItemMatches(nested, itemTitle).first,
                           ownedWindow: sourceWindow)
    if !presented {
        try requireFreshMenuFocus(app: app, pid: pid, sourceWindow: sourceWindow, menu: menu)
        menuNavigationAttempted = true
        try press(menu)
    }
    let deadline = Date().addingTimeInterval(menuReadinessTimeout)
    while Date() < deadline {
        let current = menuItemMatches(nested, itemTitle).first
        captureMenuDiagnostics(app: app, pid: pid, menu: menu, item: current, ownedWindow: sourceWindow)
        try requireFreshMenuFocus(app: app, pid: pid, sourceWindow: sourceWindow, menu: menu, item: current)
        try ensureNoDialogs(app)
        nested = (try? allElements(try children(menu))) ?? []
        presented = menuIsPresented(menu)
        let matches = menuItemMatches(nested, itemTitle)
        let item = matches.count == 1 ? matches[0] : nil
        menuNavigationVerified = presented
        captureMenuDiagnostics(app: app, pid: pid, menu: menu, item: item, ownedWindow: sourceWindow)
        if presented, let item,
           attributeBool(item, kAXEnabledAttribute as String) == true,
           ((try? actionNames(item)) ?? []).contains(kAXPressAction as String) {
            try requireFreshMenuFocus(app: app, pid: pid, sourceWindow: sourceWindow, menu: menu, item: item)
            return item
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    throw LifecycleFailure(phase: phase, message: "File menu did not expose one enabled AXPress Save As item before the bounded readiness deadline")
}

func stringValue(_ element: AXUIElement) -> String? {
    optionalAX(element, kAXValueAttribute as String) as? String
}

func setString(_ element: AXUIElement, value: String, identifier expectedID: String) throws {
    let fieldRole = try role(element)
    try require(identifier(element) == expectedID && fieldRole == (kAXTextFieldRole as String), "unexpected AX text-field identity")
    var settable = DarwinBoolean(false)
    let error = AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable)
    try require(error == .success && settable.boolValue, "AX text field is not settable")
    let setError = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, value as CFTypeRef)
    try require(setError == .success, "AXSetValue failed (\(setError.rawValue))")
    try require(stringValue(element) == value, "AX text-field readback mismatch")
}

func focusSaveNameField(_ element: AXUIElement, app: AXUIElement) throws {
    let fieldRole = try role(element)
    try require(fieldRole == (kAXTextFieldRole as String) && identifier(element) == "saveAsNameTextField", "unexpected Save As filename field identity")
    var settable = DarwinBoolean(false)
    let checkError = AXUIElementIsAttributeSettable(element, kAXFocusedAttribute as CFString, &settable)
    try require(checkError == .success && settable.boolValue, "Save As filename field is not focus-settable")
    let setError = AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
    try require(setError == .success, "setting Save As filename focus failed (\(setError.rawValue))")
    let focused = try ax(element, kAXFocusedAttribute as String) as? NSNumber
    try require(focused?.boolValue == true, "Save As filename focus readback mismatch")
    try require(focusedIs(app, element), "Save As filename is not the focused AX element")
}

func focusedIs(_ app: AXUIElement, _ expected: AXUIElement) -> Bool {
    guard let value = optionalAX(app, kAXFocusedUIElementAttribute as String),
          CFGetTypeID(value) == AXUIElementGetTypeID() else { return false }
    let focused = value as! AXUIElement
    return CFEqual(focused, expected)
}

func focusedWindowIs(_ app: AXUIElement, _ expected: AXUIElement) -> Bool {
    guard let value = optionalAX(app, kAXFocusedWindowAttribute as String),
          CFGetTypeID(value) == AXUIElementGetTypeID() else { return false }
    let focused = value as! AXUIElement
    return CFEqual(focused, expected)
}

func waitForFrontmost(pid: pid_t, seconds: TimeInterval = 5) throws {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid { return }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    throw LifecycleFailure(phase: phase, message: "exact Live PID did not become the frontmost application")
}

func focusWindow(_ app: AXUIElement, pid: pid_t, window: AXUIElement) throws {
    try waitForFrontmost(pid: pid)
    if !focusedWindowIs(app, window) {
        let actions = try actionNames(window)
        try require(actions.contains(kAXRaiseAction as String), "exact AX window does not support AXRaise")
        uiActionAttempted = true
        let error = AXUIElementPerformAction(window, kAXRaiseAction as CFString)
        try require(error == .success, "raising exact AX window failed (\(error.rawValue))")
    }
    let deadline = Date().addingTimeInterval(5)
    while Date() < deadline {
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid && focusedWindowIs(app, window) {
            return
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    throw LifecycleFailure(phase: phase, message: "exact AX window did not become focused")
}

func activateExactTarget(_ process: NSRunningApplication, app: AXUIElement, sourceWindow: AXUIElement) throws {
    phase = "activate-target"
    uiActionAttempted = true
    focusAttempted = true
    let activated = process.activate()
    try require(activated, "activation of exact Live process was refused")
    try waitForFrontmost(pid: process.processIdentifier)
    try focusWindow(app, pid: process.processIdentifier, window: sourceWindow)
    focusVerified = true
}

// Cocoa Save panels expose Go To Folder by keyboard shortcut. Use the normal
// session event stream only while the exact target PID is verified frontmost.
func makeKeyEvent(code: CGKeyCode, down: Bool, flags: CGEventFlags) throws -> CGEvent {
    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else {
        throw LifecycleFailure(phase: phase, message: "could not create session keyboard event")
    }
    event.flags = flags
    return event
}

func postKeyStroke(pid: pid_t, code: CGKeyCode, flags: CGEventFlags = []) throws {
    let keyDown = try makeKeyEvent(code: code, down: true, flags: flags)
    let keyUp = try makeKeyEvent(code: code, down: false, flags: flags)
    try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == pid, "exact Live PID lost foreground before keyboard stroke")
    defer { keyUp.post(tap: .cgSessionEventTap) }
    keyDown.post(tap: .cgSessionEventTap)
    try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == pid, "foreground changed during keyboard stroke; matching key-up was posted")
}

func postChord(pid: pid_t, key: CGKeyCode) throws {
    // Modifier flags describe modifier state at each event, including key-up.
    let modifiers: CGEventFlags = [.maskCommand, .maskShift]
    try postKeyStroke(pid: pid, code: key, flags: modifiers)
}

func waitForWindow(_ app: AXUIElement, title expectedTitle: String, seconds: TimeInterval = 5) throws -> AXUIElement {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        let matches = try windows(app).filter { title($0) == expectedTitle }
        if matches.count == 1 { return matches[0] }
        try require(matches.isEmpty, "ambiguous AX window titled \(expectedTitle)")
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    throw LifecycleFailure(phase: phase, message: "timed out waiting for AX window \(expectedTitle)")
}

func run(_ options: Options) throws -> [String: Any] {
    phase = "preflight"
    try require(AXIsProcessTrusted(), "Accessibility permission is not already trusted")
    guard let process = NSRunningApplication(processIdentifier: options.pid) else {
        throw LifecycleFailure(phase: phase, message: "exact target PID is not a running application")
    }
    initialPID = Int(options.pid)
    initialBundleID = process.bundleIdentifier
    try require(process.bundleIdentifier == options.bundleID, "exact PID bundle identifier mismatch")
    let matches = NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == options.bundleID }
    try require(matches.count == 1 && matches[0].processIdentifier == options.pid, "ambiguous running applications for expected bundle ID")
    let source = try canonicalURL(options.sourceSet)
    try ensureNoSymlinkChain(source, mustExist: true, outsideGit: false)
    let originalSourceIdentity = try fileIdentity(source)
    let appElement = AXUIElementCreateApplication(options.pid)
    try ensureNoDialogs(appElement)
    _ = try exactDocumentWindow(appElement, url: source)

    var destination: URL?
    if let rawDestination = options.destinationSet {
        let target = try canonicalURL(rawDestination)
        try require(target != source && target.deletingLastPathComponent() == source.deletingLastPathComponent(), "destination must be a new sibling of the exact source Set")
        try ensureNoSymlinkChain(target, mustExist: false, outsideGit: true)
        destination = target
    }
    guard options.execute else {
        return ["format": "codex-devtools-live-set-lifecycle-v1", "status": "ready",
                "target": ["pid": options.pid, "bundle_identifier": options.bundleID,
                           "process_name": process.localizedName ?? ""],
                "source_set_url": source.absoluteString,
                "destination_set_url": destination?.absoluteString as Any? ?? NSNull(),
                "read_only": true, "actions": ["focus": "not_attempted", "menu_navigation": "not_attempted", "save_as_menu": "not_attempted", "save": "not_attempted", "quit": "not_attempted", "open": "not_attempted"]]
    }
    guard let destination, let rawAppPath = options.appPath else {
        throw LifecycleFailure(phase: phase, message: "exact destination Set and Live.app path required")
    }
    let appURL = URL(fileURLWithPath: rawAppPath)
    try require(appURL.path == rawAppPath && appURL.pathExtension == "app" && FileManager.default.fileExists(atPath: appURL.path), "exact normalized .app bundle path required")
    guard let appBundleID = Bundle(url: appURL)?.bundleIdentifier else {
        throw LifecycleFailure(phase: phase, message: "Live.app bundle ID is unreadable")
    }
    try require(appBundleID == options.bundleID, "Live.app bundle ID differs from target bundle ID")

    let sourceWindow = try exactDocumentWindow(appElement, url: source)
    try activateExactTarget(process, app: appElement, sourceWindow: sourceWindow)
    phase = "save-as-menu"
    let saveItem = try findMenuItem(app: appElement, pid: options.pid, sourceWindow: sourceWindow,
                                    menuTitle: "File", itemTitle: "Save Live Set As...")
    try requireFreshMenuFocus(app: appElement, pid: options.pid, sourceWindow: sourceWindow,
                              menu: activeFileMenu, item: saveItem)
    captureMenuDiagnostics(app: appElement, pid: options.pid, menu: activeFileMenu, item: saveItem,
                           ownedWindow: sourceWindow)
    saveAsMenuAttempted = true
    try press(saveItem)
    menuDiagnostics = nil
    phase = "save-dialog"
    let saveWindow = try waitForWindow(appElement, title: "Save")
    try ensureNoDialogs(appElement, except: "Save")
    try focusWindow(appElement, pid: options.pid, window: saveWindow)
    _ = try exactDocumentWindow(appElement, url: source)
    let saveRows = try savePanelElements([saveWindow])
    try require(!saveRows.contains { ["Replace", "Allow", "Allow Anyway", "Discard", "Don't Save", "Don’t Save"].contains(title($0) ?? "") }, "unexpected overwrite/security/discard prompt")
    let nameField = try uniqueSavePanelElement(saveRows, role: kAXTextFieldRole as String, title: nil, identifier: "saveAsNameTextField")
    try setString(nameField, value: destination.lastPathComponent, identifier: "saveAsNameTextField")
    try focusSaveNameField(nameField, app: appElement)
    try waitForFrontmost(pid: options.pid)
    try require(focusedWindowIs(appElement, saveWindow), "Save panel is not the focused AX window before Go To Folder")
    try require(focusedIs(appElement, nameField), "Save panel filename field did not receive focus")
    phase = "set-save-folder"
    guard let focusValue = optionalAX(appElement, kAXFocusedUIElementAttribute as String) else {
        throw LifecycleFailure(phase: phase, message: "Save panel has no focused element")
    }
    let initialFocus = focusValue as! AXUIElement
    try require(saveRows.contains { CFEqual($0, initialFocus) }, "Save panel does not own the current focus")
    try postChord(pid: options.pid, key: 5) // Shift-Command-G
    try ensureNoDialogs(appElement, except: "Save")
    let (goToSheet, pathField) = try waitForGoToPathField(saveWindow)
    try require(focusedWindowIs(appElement, goToSheet), "Go To Folder AXSheet child is not the focused AX window")
    try require(focusedIs(appElement, pathField), "Go To Folder path field is not focused before setValue")
    try setString(pathField, value: destination.deletingLastPathComponent().path, identifier: "PathTextField")
    try waitForFrontmost(pid: options.pid)
    try require(focusedWindowIs(appElement, goToSheet), "Go To Folder AXSheet child is not the focused AX window before path confirmation")
    try require(focusedIs(appElement, pathField), "Go To Folder path field is not the focused element")
    try postKeyStroke(pid: options.pid, code: 36)

    phase = "verify-save-dialog"
    let refreshedWindow = try waitForWindow(appElement, title: "Save")
    try waitForGoToSheetDismissed(refreshedWindow)
    let saveButton = try waitForSaveButtonEnabled(app: appElement, pid: options.pid, bundleID: options.bundleID,
                                                 process: process, saveWindow: refreshedWindow, source: source,
                                                 originalSourceIdentity: originalSourceIdentity, destination: destination)

    phase = "save"
    saveAttempted = true
    try press(saveButton)
    phase = "verify-saved-set"
    var stableSamples = 0
    var lastSavedSize: Int = -1
    var saveVerified = false
    let saveDeadline = Date().addingTimeInterval(30)
    while Date() < saveDeadline {
        try ensureNoDialogs(appElement)
        if (try? exactDocumentWindow(appElement, url: destination)) != nil,
           let attrs = try? FileManager.default.attributesOfItem(atPath: destination.path),
           attrs[.type] as? FileAttributeType == .typeRegular,
           let size = attrs[.size] as? NSNumber, size.intValue > 0 {
            stableSamples = size.intValue == lastSavedSize ? stableSamples + 1 : 0
            lastSavedSize = size.intValue
            if stableSamples >= 1 { saveVerified = true; break }
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }
    try require(saveVerified, "Save outcome unknown: exact destination URL and stable nonempty file were not observed")
    let sourceAfterSaveIdentity = try fileIdentity(source)
    try require(sameFileIdentity(originalSourceIdentity, sourceAfterSaveIdentity), "source Set metadata changed during Save As")

    phase = "quit-menu"
    try ensureNoDialogs(appElement)
    let savedWindow = try exactDocumentWindow(appElement, url: destination)
    try focusWindow(appElement, pid: options.pid, window: savedWindow)
    let quitItem = try findMenuItem(app: appElement, pid: options.pid, sourceWindow: savedWindow,
                                    menuTitle: process.localizedName ?? "Live", itemTitle: "Quit Live")
    phase = "quit"
    quitAttempted = true
    try press(quitItem)
    menuDiagnostics = nil
    phase = "verify-exit"
    let deadline = Date().addingTimeInterval(30)
    while Date() < deadline && !process.isTerminated {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
    }
    try require(process.isTerminated && NSRunningApplication(processIdentifier: options.pid) == nil, "Quit outcome unknown: original PID remains alive")
    try require(NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == options.bundleID }.isEmpty, "another Live process remains; reopen would be ambiguous")

    phase = "launchservices-open"
    let configuration = NSWorkspace.OpenConfiguration()
    openAttempted = true
    var reopened: NSRunningApplication?
    var openError: Error?
    let semaphore = DispatchSemaphore(value: 0)
    NSWorkspace.shared.open([destination], withApplicationAt: appURL, configuration: configuration) { application, error in
        reopened = application
        openError = error
        semaphore.signal()
    }
    if semaphore.wait(timeout: .now() + 45) == .timedOut {
        throw LifecycleFailure(phase: phase, message: "LaunchServices open outcome unknown: callback timed out")
    }
    if let openError { throw LifecycleFailure(phase: phase, message: "LaunchServices open failed: \(openError.localizedDescription)") }
    guard let reopened else { throw LifecycleFailure(phase: phase, message: "LaunchServices returned no application") }
    try require(reopened.bundleIdentifier == options.bundleID && reopened.processIdentifier > 0 && reopened.processIdentifier != options.pid, "reopened Live process identity is unexpected")

    phase = "verify-reopened-set"
    let newAppElement = AXUIElementCreateApplication(reopened.processIdentifier)
    let openDeadline = Date().addingTimeInterval(75)
    var exactWindow = false
    while Date() < openDeadline {
        if reopened.isTerminated { throw LifecycleFailure(phase: phase, message: "reopened Live exited before Set verification") }
        if let currentWindows = try? windows(newAppElement) {
            let dialogs = currentWindows.filter { ["AXDialog", "AXSheet"].contains((try? role($0)) ?? "") }
            if !dialogs.isEmpty { throw LifecycleFailure(phase: phase, message: "unexpected dialog during reopen; stopped without interaction") }
            if (try? exactDocumentWindow(newAppElement, url: destination)) != nil { exactWindow = true; break }
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }
    try require(exactWindow, "reopened Live did not expose the exact destination Set URL")
    return ["format": "codex-devtools-live-set-lifecycle-v1", "status": "reopened",
            "target": ["pid": options.pid, "bundle_identifier": options.bundleID,
                       "process_name": process.localizedName ?? ""],
            "reopened_target": ["pid": reopened.processIdentifier, "bundle_identifier": reopened.bundleIdentifier ?? "",
                                "process_name": reopened.localizedName ?? ""],
            "source_set_url": source.absoluteString, "destination_set_url": destination.absoluteString,
            "read_only": false,
            "actions": ["focus": "verified", "menu_navigation": "verified", "save_as_menu": "verified_panel", "save": "destination_observed", "quit": "terminated", "open": "exact_set_window_observed"],
            "evidence_limit": "AX document URL and nonempty regular-file observation; host LOM path and recall state require separate verification"]
}

let options = parseOptions()
do {
    let report = try run(options)
    let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
    print(String(data: data, encoding: .utf8) ?? "{}")
} catch let error as LifecycleFailure {
    var result: [String: Any] = ["format": "codex-devtools-live-set-lifecycle-v1", "status": "stopped",
        "target": ["pid": initialPID ?? 0, "bundle_identifier": initialBundleID ?? ""],
        "phase": error.phase, "error": error.message,
        "actions": ["focus": focusVerified ? "verified" : focusAttempted ? "outcome_unknown" : "not_attempted",
                    "menu_navigation": menuNavigationVerified ? "verified" : menuNavigationAttempted ? "outcome_unknown" : "not_attempted",
                    "save_as_menu": saveAsMenuAttempted ? "outcome_unknown" : "not_attempted",
                    "save": saveAttempted ? "outcome_unknown" : "not_attempted",
                    "quit": quitAttempted ? "outcome_unknown" : "not_attempted",
                    "open": openAttempted ? "outcome_unknown" : "not_attempted"],
        "manual_inspection_required": uiActionAttempted || saveAsMenuAttempted || saveAttempted || quitAttempted || openAttempted]
    if let menuDiagnostics { result["diagnostics"] = menuDiagnostics }
    if let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]),
       let text = String(data: data, encoding: .utf8) { print(text) }
    exit(1)
} catch {
    var result: [String: Any] = ["format": "codex-devtools-live-set-lifecycle-v1", "status": "stopped",
        "target": ["pid": initialPID ?? 0, "bundle_identifier": initialBundleID ?? ""],
        "phase": phase, "error": String(describing: error),
        "actions": ["focus": focusVerified ? "verified" : focusAttempted ? "outcome_unknown" : "not_attempted",
                    "menu_navigation": menuNavigationVerified ? "verified" : menuNavigationAttempted ? "outcome_unknown" : "not_attempted",
                    "save_as_menu": saveAsMenuAttempted ? "outcome_unknown" : "not_attempted",
                    "save": saveAttempted ? "outcome_unknown" : "not_attempted",
                    "quit": quitAttempted ? "outcome_unknown" : "not_attempted",
                    "open": openAttempted ? "outcome_unknown" : "not_attempted"],
        "manual_inspection_required": uiActionAttempted || saveAsMenuAttempted || saveAttempted || quitAttempted || openAttempted]
    if let menuDiagnostics { result["diagnostics"] = menuDiagnostics }
    if let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]),
       let text = String(data: data, encoding: .utf8) { print(text) }
    exit(1)
}
