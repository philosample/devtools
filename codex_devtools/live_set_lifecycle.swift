// Native Accessibility Save As/Quit/LaunchServices reopen for one exact Live Set.
import ApplicationServices
import AppKit
import Darwin
import Foundation
import CryptoKit

struct Options {
    var pid: pid_t = 0
    var bundleID = ""
    var sourceSet = ""
    var destinationSet: String?
    var appPath: String?
    var execute = false
    var stopBeforeReopen = false
    var resumeReceipt: String?
    var receiptSHA256: String?
    var openExisting = false
    var reconcilePendingOpen = false
    var cancelSelectedFileMenu = false
    var observeOriginalPendingOpen = false
    var originalOpenReceipt: String?
    var originalOpenReceiptSHA256: String?
    var preflightReceipt: String?
    var preflightReceiptSHA256: String?
    var attemptMarker: String?
    var expectedGeneration: Int64?
    var sourceSHA256: String?
    var destinationSHA256: String?
    var saveOwnedSource = false
    var backupReceipt: String?
    var saveCopyOnly = false
    var expectedFinalSet: String?
    var projectRequiredDialog = false
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
var sourceSaveVerified = false
var projectPromptAttempted = false
var projectPromptVerified = false
var saveCopyDeadline: TimeInterval?
var fileMenuCancelAttempted = false
var attemptMarkerCreated = false
var cancelFocusVerified = false
var cancelOriginalReceiptSHA256 = ""
var cancelSourcePin: [String: Any]?
var cancelDestinationPin: [String: Any]?
var cancelSourceURL: URL?
var cancelDestinationURL: URL?
var cancelAttemptMarkerURL: URL?

func checkSaveCopyDeadline() throws {
    if let deadline = saveCopyDeadline { try require(ProcessInfo.processInfo.systemUptime < deadline, "same-process Save As deadline expired; no retry") }
}

let menuReadinessTimeout: TimeInterval = 2

// Keep the Save As exception identical to the closed signature used by the
// transport inspector. This file is compiled as a standalone helper, so the
// small pure predicate is intentionally local rather than a shared build API.
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

struct SaveCopyWindowAssessment {
    let role: String
    let subrole: String?
    let identifier: String?
    let modal: Bool?
    let sharingOverlay: WindowSharingOverlaySignature?
}

struct FileMenuCancelPreflightEvidence {
    let exactProcess: Bool
    let expectedGeneration: Bool
    let currentDesktop: Bool
    let exactSourceWindowFocused: Bool
    let sourcePinMatches: Bool
    let destinationPinMatches: Bool
    let transportStopped: Bool
    let noBlockingWindows: Bool
    let uniqueFileMenuCount: Int
    let selectedMenuCount: Int
    let fileMenuSelected: Bool
    let fileMenuEnabled: Bool
    let cancelActionAvailable: Bool
}

func fileMenuCancelPreflightAllows(_ evidence: FileMenuCancelPreflightEvidence) -> Bool {
    evidence.exactProcess && evidence.expectedGeneration && evidence.currentDesktop &&
        evidence.exactSourceWindowFocused && evidence.sourcePinMatches &&
        evidence.destinationPinMatches && evidence.transportStopped &&
        evidence.noBlockingWindows && evidence.uniqueFileMenuCount == 1 &&
        evidence.selectedMenuCount == 1 && evidence.fileMenuSelected &&
        evidence.fileMenuEnabled && evidence.cancelActionAvailable
}

struct PendingOpenWaitEvidence {
    let exactProcess: Bool
    let expectedGeneration: Bool
    let noBlockingWindows: Bool
    let onlyOwnedSourceWindow: Bool
    let sourcePinMatches: Bool
    let sourceTransportStopped: Bool
    let fileMenuClosed: Bool
}

func pendingOpenWaitAllows(_ evidence: PendingOpenWaitEvidence) -> Bool {
    evidence.exactProcess && evidence.expectedGeneration &&
        evidence.noBlockingWindows && evidence.onlyOwnedSourceWindow &&
        evidence.sourcePinMatches && evidence.sourceTransportStopped &&
        evidence.fileMenuClosed
}

struct PendingOpenSuccessEvidence {
    let withinDeadline: Bool
    let exactProcess: Bool
    let expectedGeneration: Bool
    let exactDestinationFocused: Bool
    let destinationPinStable: Bool
    let sourcePinUnchanged: Bool
    let sourceAndDestinationTransportStopped: Bool
    let noBlockingWindows: Bool
    let fileMenuClosed: Bool
}

func pendingOpenSuccessAllows(_ evidence: PendingOpenSuccessEvidence) -> Bool {
    evidence.withinDeadline && evidence.exactProcess && evidence.expectedGeneration &&
        evidence.exactDestinationFocused && evidence.destinationPinStable &&
        evidence.sourcePinUnchanged && evidence.sourceAndDestinationTransportStopped &&
        evidence.noBlockingWindows && evidence.fileMenuClosed
}

enum PendingOpenPostCancelReadState: Equatable {
    case originalMenuStillSelected
    case closed
    case transientUnavailable
    case changed
}

struct PendingOpenPostCancelWaitEvidence {
    let withinDeadline: Bool
    let exactProcess: Bool
    let expectedGeneration: Bool
    let noObservedBlockers: Bool
    let readState: PendingOpenPostCancelReadState
}

struct PendingOpenBlockerScanFailureEvidence {
    let transientUnavailable: Bool
    let blockerAlreadyObserved: Bool
}

func pendingOpenBlockerScanAllowsWait(_ evidence: PendingOpenBlockerScanFailureEvidence) -> Bool {
    evidence.transientUnavailable && !evidence.blockerAlreadyObserved
}

func pendingOpenRoleIsBlocking(_ role: String) -> Bool {
    ["AXDialog", "AXSheet"].contains(role)
}

func pendingOpenPostCancelWaitAllows(_ evidence: PendingOpenPostCancelWaitEvidence) -> Bool {
    evidence.withinDeadline && evidence.exactProcess && evidence.expectedGeneration &&
        evidence.noObservedBlockers &&
        (evidence.readState == .originalMenuStillSelected || evidence.readState == .transientUnavailable)
}

func saveCopyWindowBlocks(_ window: SaveCopyWindowAssessment) -> Bool {
    if window.modal == true { return true }
    if let sharingOverlay = window.sharingOverlay,
       isObservedWindowSharingOverlay(sharingOverlay) {
        return false
    }
    return ["AXDialog", "AXSheet", "AXAlert"].contains(window.role) ||
        ["AXDialog", "AXSheet"].contains(window.subrole ?? "") ||
        window.identifier == "open-panel"
}

func saveCopyWindowsHaveBlocker(_ windows: [SaveCopyWindowAssessment]) -> Bool {
    windows.contains { saveCopyWindowBlocks($0) }
}

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

func isBlockingWindowStrict(_ element: AXUIElement) throws -> Bool {
    let elementRole = try role(element)
    if pendingOpenRoleIsBlocking(elementRole) { return true }
    let elementIdentifier = try optionalAXThrowing(element, "AXIdentifier") as? String
    if elementRole == (kAXWindowRole as String) && elementIdentifier == "open-panel" { return true }
    let modalValue = try optionalAXThrowing(element, "AXModal")
    let modal: Bool
    if let number = modalValue as? NSNumber {
        modal = number.boolValue
    } else if modalValue == nil {
        modal = false
    } else {
        throw LifecycleFailure(phase: phase, message: "AXModal state is not boolean")
    }
    return modal
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
        if key == "--stop-before-reopen" { result.stopBeforeReopen = true; index += 1; continue }
        if key == "--save-copy-only" { result.saveCopyOnly = true; index += 1; continue }
        if key == "--project-required-dialog" { result.projectRequiredDialog = true; index += 1; continue }
        if key == "--open-existing" { result.openExisting = true; index += 1; continue }
        if key == "--reconcile-pending-open" { result.reconcilePendingOpen = true; index += 1; continue }
        if key == "--cancel-selected-file-menu" { result.cancelSelectedFileMenu = true; index += 1; continue }
        if key == "--observe-original-pending-open" { result.observeOriginalPendingOpen = true; index += 1; continue }
        if key == "--save-owned-source" { result.saveOwnedSource = true; index += 1; continue }
        guard index + 1 < args.count else { fail("missing value for \(key)") }
        let value = args[index + 1]
        switch key {
        case "--resume-receipt": result.resumeReceipt = value
        case "--receipt-sha256": result.receiptSHA256 = value
        case "--original-open-receipt": result.originalOpenReceipt = value
        case "--original-open-receipt-sha256": result.originalOpenReceiptSHA256 = value
        case "--preflight-receipt": result.preflightReceipt = value
        case "--preflight-receipt-sha256": result.preflightReceiptSHA256 = value
        case "--attempt-marker": result.attemptMarker = value
        case "--pid":
            guard let parsed = Int32(value), parsed > 0 else { fail("PID must be positive") }
            result.pid = pid_t(parsed)
        case "--bundle-id": result.bundleID = value
        case "--source-set": result.sourceSet = value
        case "--destination-set": result.destinationSet = value
        case "--app": result.appPath = value
        case "--source-sha256": result.sourceSHA256 = value
        case "--destination-sha256": result.destinationSHA256 = value
        case "--expected-generation":
            guard let parsed = Int64(value), parsed > 0 else { fail("expected generation must be positive") }
            result.expectedGeneration = parsed
        case "--backup-receipt": result.backupReceipt = value
        case "--expected-final-set": result.expectedFinalSet = value
        default: fail("unknown option: \(key)")
        }
        index += 2
    }
    if result.resumeReceipt != nil {
        guard result.receiptSHA256 != nil && result.pid == 0 && result.bundleID.isEmpty && result.sourceSet.isEmpty &&
              result.destinationSet == nil && result.appPath == nil && !result.stopBeforeReopen && !result.openExisting &&
              !result.saveCopyOnly && !result.saveOwnedSource && result.sourceSHA256 == nil && result.destinationSHA256 == nil &&
              result.backupReceipt == nil && result.expectedFinalSet == nil && !result.projectRequiredDialog &&
              !result.reconcilePendingOpen && !result.cancelSelectedFileMenu &&
              !result.observeOriginalPendingOpen && result.expectedGeneration == nil &&
              result.originalOpenReceipt == nil && result.originalOpenReceiptSHA256 == nil &&
              result.preflightReceipt == nil && result.preflightReceiptSHA256 == nil &&
              result.attemptMarker == nil else {
            fail("resume requires only a receipt path, SHA-256 and optional --execute")
        }
        return result
    }
    if result.receiptSHA256 != nil || (result.stopBeforeReopen && (result.openExisting || result.saveCopyOnly)) {
        fail("incompatible lifecycle mode options")
    }
    guard result.pid > 0, !result.bundleID.isEmpty, !result.sourceSet.isEmpty else {
        fail("--pid, --bundle-id and --source-set are required")
    }
    if result.execute && (result.destinationSet == nil || result.appPath == nil) {
        fail("--execute requires --destination-set and --app")
    }
    if result.openExisting && (result.destinationSet == nil || result.appPath == nil || result.sourceSHA256 == nil || result.destinationSHA256 == nil) {
        fail("--open-existing requires exact source/destination hashes, destination and app")
    }
    if result.cancelSelectedFileMenu || result.observeOriginalPendingOpen {
        guard result.openExisting && result.cancelSelectedFileMenu && result.observeOriginalPendingOpen &&
              !result.reconcilePendingOpen && !result.saveOwnedSource && result.backupReceipt == nil &&
              result.expectedGeneration != nil && result.originalOpenReceipt != nil &&
              result.originalOpenReceiptSHA256 != nil && result.attemptMarker != nil else {
            fail("File-menu cancellation requires its paired observe mode, exact original receipt/hash, generation and marker; Save and reconcile modes are forbidden")
        }
        if result.execute {
            guard result.preflightReceipt != nil && result.preflightReceiptSHA256 != nil else {
                fail("File-menu cancellation execute requires the reviewed preflight receipt/hash")
            }
        } else if result.preflightReceipt != nil || result.preflightReceiptSHA256 != nil {
            fail("File-menu cancellation preflight receipt/hash are execute-only")
        }
    } else if result.originalOpenReceipt != nil || result.originalOpenReceiptSHA256 != nil ||
                result.preflightReceipt != nil || result.preflightReceiptSHA256 != nil || result.attemptMarker != nil {
        fail("File-menu cancellation receipts/marker require both explicit cancel/observe mode flags")
    }
    if result.reconcilePendingOpen {
        guard result.openExisting && result.saveOwnedSource && result.expectedGeneration != nil else {
            fail("--reconcile-pending-open requires --open-existing, --save-owned-source and --expected-generation")
        }
    } else if !result.cancelSelectedFileMenu && result.expectedGeneration != nil {
        fail("--expected-generation requires --reconcile-pending-open")
    }
    if !result.openExisting && !result.saveCopyOnly && (result.sourceSHA256 != nil || result.destinationSHA256 != nil || result.saveOwnedSource || result.backupReceipt != nil) {
        fail("existing-Set open options require --open-existing")
    }
    if result.openExisting && result.execute && result.saveOwnedSource && result.backupReceipt == nil {
        fail("saving the owned source requires its already-written backup receipt")
    }
    if result.saveCopyOnly {
        guard !result.openExisting && !result.saveOwnedSource && result.destinationSHA256 == nil && result.sourceSHA256 != nil &&
              result.destinationSet != nil && result.expectedFinalSet != nil && result.appPath != nil &&
              (!result.execute || result.backupReceipt != nil) else { fail("save-copy-only requires exact source hash, selected/final paths, app and execute backup; incompatible mode flags") }
    } else if result.expectedFinalSet != nil || result.projectRequiredDialog { fail("Save As copy options require --save-copy-only") }
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

func optionalAXThrowing(_ element: AXUIElement, _ name: String) throws -> CFTypeRef? {
    var value: CFTypeRef?
    AXUIElementSetMessagingTimeout(element, 2)
    let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    if error == .noValue { return nil }
    guard error == .success, let value else {
        throw LifecycleFailure(phase: phase, message: "AX read \(name) failed (\(error.rawValue))")
    }
    return value
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
    try checkSaveCopyDeadline()
    try require((optionalAX(element, kAXEnabledAttribute as String) as? NSNumber)?.boolValue == true, "target AX element is not enabled")
    let actions = try actionNames(element)
    try require(actions.contains(kAXPressAction as String), "target AX element does not support AXPress")
    AXUIElementSetMessagingTimeout(element, 2)
    let menuItem = (try role(element)) == (kAXMenuBarItemRole as String)
    try checkSaveCopyDeadline()
    uiActionAttempted = true
    if menuItem { menuNavigationAttempted = true }
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
                              destination: URL, seconds: TimeInterval = 5, additionalGuard: (() throws -> Void)? = nil) throws -> AXUIElement {
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
        try additionalGuard?()
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
    try checkSaveCopyDeadline()
    let fieldRole = try role(element)
    try require(identifier(element) == expectedID && fieldRole == (kAXTextFieldRole as String), "unexpected AX text-field identity")
    var settable = DarwinBoolean(false)
    let error = AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable)
    try require(error == .success && settable.boolValue, "AX text field is not settable")
    try checkSaveCopyDeadline()
    let setError = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, value as CFTypeRef)
    try require(setError == .success, "AXSetValue failed (\(setError.rawValue))")
    try require(stringValue(element) == value, "AX text-field readback mismatch")
}

func focusSaveNameField(_ element: AXUIElement, app: AXUIElement) throws {
    try checkSaveCopyDeadline()
    let fieldRole = try role(element)
    try require(fieldRole == (kAXTextFieldRole as String) && identifier(element) == "saveAsNameTextField", "unexpected Save As filename field identity")
    var settable = DarwinBoolean(false)
    let checkError = AXUIElementIsAttributeSettable(element, kAXFocusedAttribute as CFString, &settable)
    try require(checkError == .success && settable.boolValue, "Save As filename field is not focus-settable")
    try checkSaveCopyDeadline()
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

func focusedWindowIsThrowing(_ app: AXUIElement, _ expected: AXUIElement) throws -> Bool {
    guard let value = try optionalAXThrowing(app, kAXFocusedWindowAttribute as String) else { return false }
    guard CFGetTypeID(value) == AXUIElementGetTypeID() else {
        throw LifecycleFailure(phase: phase, message: "focused window is not an AX element")
    }
    return CFEqual(value as! AXUIElement, expected)
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
    try checkSaveCopyDeadline()
    try waitForFrontmost(pid: pid)
    if !focusedWindowIs(app, window) {
        let actions = try actionNames(window)
        try require(actions.contains(kAXRaiseAction as String), "exact AX window does not support AXRaise")
        try checkSaveCopyDeadline()
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
    try checkSaveCopyDeadline()
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
    try checkSaveCopyDeadline()
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

// Save-copy-only uses the existing Save As sequence, with a separate exact
// prompt policy and same-process receipt. No other mode adopts this prompt.
let projectRequiredText = "The Live Set you are about to save is located outside of a Project folder. Live must save the Set into a Project folder. Please choose a Project folder to save the Set into."

func projectRequiredSignature(_ rows: [[String: Any]]) -> Bool {
    guard rows.count == 6 else { return false }
    let byRole = Dictionary(grouping: rows) { $0["role"] as? String ?? "" }
    guard byRole.keys.count == 4, byRole["AXWindow"]?.count == 1, byRole["AXGroup"]?.count == 1,
          byRole["AXStaticText"]?.count == 2, byRole["AXButton"]?.count == 2,
          let window = byRole["AXWindow"]?.first, window["subrole"] as? String == "AXDialog", window["title"] as? String == "" else { return false }
    let texts = byRole["AXStaticText"]!
    guard texts.filter({ $0["identifier"] as? String == "DialogText" && $0["value"] as? String == projectRequiredText }).count == 1,
          texts.filter({ $0["value"] as? String == "" }).count == 1 else { return false }
    let buttons = byRole["AXButton"]!
    for (id, description) in [("Cancel", "Cancel"), ("SaveAs...", "Save As...")] {
        guard buttons.filter({ $0["identifier"] as? String == id && $0["description"] as? String == description &&
            $0["enabled"] as? Bool == true && $0["press"] as? Bool == true }).count == 1 else { return false }
    }
    return true
}

func saveCopyWindowAssessment(_ window: AXUIElement) throws -> SaveCopyWindowAssessment {
    let kind = try role(window)
    let subrole = optionalAX(window, kAXSubroleAttribute as String) as? String
    let windowTitle = title(window)
    let windowIdentifier = identifier(window)
    var sharingOverlay: WindowSharingOverlaySignature?
    if kind == (kAXWindowRole as String), subrole == "AXDialog",
       windowTitle == "Window", windowIdentifier == "_NS:8" {
        let childElements = try children(window)
        let childSignatures = try childElements.map { child -> WindowSharingOverlayChildSignature in
            guard let childEnabled = attributeBool(child, kAXEnabledAttribute as String) else {
                throw LifecycleFailure(phase: phase, message: "sharing overlay child enabled state is unavailable")
            }
            return WindowSharingOverlayChildSignature(
                role: try role(child),
                title: title(child),
                identifier: identifier(child),
                enabled: childEnabled
            )
        }
        sharingOverlay = WindowSharingOverlaySignature(
            role: kind,
            subrole: subrole,
            title: windowTitle,
            identifier: windowIdentifier,
            children: childSignatures
        )
    }
    return SaveCopyWindowAssessment(
        role: kind,
        subrole: subrole,
        identifier: windowIdentifier,
        modal: attributeBool(window, "AXModal"),
        sharingOverlay: sharingOverlay
    )
}

func saveCopyWindows(_ app: AXUIElement, allowing allowed: AXUIElement? = nil) throws {
    let assessments = try windows(app).compactMap { window -> SaveCopyWindowAssessment? in
        if let allowed, CFEqual(window, allowed) { return nil }
        return try saveCopyWindowAssessment(window)
    }
    try require(!saveCopyWindowsHaveBlocker(assessments), "unexpected dialog during same-process Save As; no prompt accepted")
}

func projectRequiredButton(_ app: AXUIElement) throws -> (AXUIElement, AXUIElement) {
    let matches = try windows(app).filter { (try? role($0)) == "AXWindow" && optionalAX($0, kAXSubroleAttribute as String) as? String == "AXDialog" }
    try require(matches.count == 1, "exact Project-required dialog is absent or ambiguous")
    let dialog = matches[0], elements = try allElements([dialog], maxDepth: 6, maxElements: 16)
    let rows: [[String: Any]] = try elements.map { element in
        ["role": try role(element), "subrole": optionalAX(element, kAXSubroleAttribute as String) as Any? ?? NSNull(),
         "title": title(element) as Any? ?? NSNull(), "identifier": identifier(element) as Any? ?? NSNull(),
         "description": optionalAX(element, kAXDescriptionAttribute as String) as Any? ?? NSNull(),
         "value": stringValue(element) as Any? ?? NSNull(), "enabled": attributeBool(element, kAXEnabledAttribute as String) as Any? ?? NSNull(),
         "press": ((try? actionNames(element)) ?? []).contains(kAXPressAction as String)]
    }
    try require(projectRequiredSignature(rows), "Project-required dialog signature differs; no prompt accepted")
    try saveCopyWindows(app, allowing: dialog)
    let buttons = elements.filter { identifier($0) == "SaveAs..." && (try? role($0)) == "AXButton" }
    try require(buttons.count == 1, "Project Save As button ambiguous")
    return (dialog, buttons[0])
}

func saveCopyTransport(_ sourceWindow: AXUIElement) throws -> [String: Int] {
    let elements = try allElements([sourceWindow])
    var values: [String: Int] = [:]
    for (key, name, id) in [("play", "Play", "Transport.Play"), ("link", "Link", "Transport.AbletonLink"),
                             ("arrangement_record", "Arrangement Record", "Transport.GlobalRecord"), ("session_record", "Session Record", "Transport.SessionRecord")] {
        let matches = elements.filter { identifier($0) == id }
        try require(matches.count == 1 && (try? role(matches[0])) == "AXCheckBox" &&
                    optionalAX(matches[0], kAXDescriptionAttribute as String) as? String == name, "exact source transport checkbox missing/ambiguous")
        guard let value = optionalAX(matches[0], kAXValueAttribute as String) as? NSNumber else {
            throw LifecycleFailure(phase: phase, message: "source transport value unavailable")
        }
        try require(value.doubleValue == 0, "Save As requires Play, Link and both recording flags off")
        values[key] = 0
    }
    return values
}

func saveCopyTransportStrict(_ sourceWindow: AXUIElement) throws -> [String: Int] {
    let elements = try allElements([sourceWindow])
    var values: [String: Int] = [:]
    for (key, name, id) in [("play", "Play", "Transport.Play"), ("link", "Link", "Transport.AbletonLink"),
                             ("arrangement_record", "Arrangement Record", "Transport.GlobalRecord"),
                             ("session_record", "Session Record", "Transport.SessionRecord")] {
        var matches: [AXUIElement] = []
        for element in elements {
            if try optionalAXThrowing(element, "AXIdentifier") as? String == id { matches.append(element) }
        }
        try require(matches.count == 1, "exact source transport checkbox missing/ambiguous")
        let controlRole = try role(matches[0])
        let controlName = try optionalAXThrowing(matches[0], kAXDescriptionAttribute as String) as? String
        try require(controlRole == "AXCheckBox" && controlName == name,
                    "exact source transport checkbox missing/ambiguous")
        guard let value = try ax(matches[0], kAXValueAttribute as String) as? NSNumber else {
            throw LifecycleFailure(phase: phase, message: "source transport value unavailable")
        }
        try require(value.doubleValue == 0, "source transport is not stopped")
        values[key] = 0
    }
    return values
}

func saveChangesDialogSignature(_ rows: [[String: Any]], expectedText: String) -> Bool {
    guard rows.count == 7 else { return false }
    let byRole = Dictionary(grouping: rows) { $0["role"] as? String ?? "" }
    guard byRole.keys.count == 4,
          byRole["AXWindow"]?.count == 1,
          byRole["AXGroup"]?.count == 1,
          byRole["AXStaticText"]?.count == 2,
          byRole["AXButton"]?.count == 3,
          let window = byRole["AXWindow"]?.first,
          window["subrole"] as? String == "AXDialog",
          window["title"] as? String == "" else { return false }
    let texts = byRole["AXStaticText"]!
    guard texts.filter({
        $0["identifier"] as? String == "DialogText" &&
        $0["value"] as? String == expectedText
    }).count == 1,
    texts.filter({ $0["value"] as? String == "" }).count == 1 else { return false }
    let buttons = byRole["AXButton"]!
    for (id, description) in [
        ("Save", "Save"),
        ("Cancel", "Cancel"),
        ("Don’tSave", "Don’t Save")
    ] {
        guard buttons.filter({
            $0["identifier"] as? String == id &&
            $0["description"] as? String == description &&
            $0["enabled"] as? Bool == true &&
            $0["press"] as? Bool == true
        }).count == 1 else { return false }
    }
    return true
}

func canWaitForReconciliationDialog(blockerCount: Int, exactSameDialog: Bool) -> Bool {
    blockerCount == 1 && exactSameDialog
}

func isReconciliationBlockingWindow(_ window: AXUIElement) -> Bool {
    let elementRole = (try? role(window)) ?? ""
    let elementSubrole = optionalAX(window, kAXSubroleAttribute as String) as? String
    return ["AXDialog", "AXSheet", "AXAlert"].contains(elementRole) ||
        ["AXDialog", "AXSheet", "AXAlert"].contains(elementSubrole ?? "") ||
        attributeBool(window, "AXModal") == true ||
        (elementRole == (kAXWindowRole as String) && identifier(window) == "open-panel")
}

func exactSaveChangesDialog(_ app: AXUIElement, expectedText: String) throws -> (AXUIElement, AXUIElement) {
    let matches = try windows(app).filter {
        (try? role($0)) == (kAXWindowRole as String) &&
        optionalAX($0, kAXSubroleAttribute as String) as? String == "AXDialog" &&
        title($0) == ""
    }
    try require(matches.count == 1, "exact blank-title Save Changes dialog is absent or ambiguous")
    let dialog = matches[0]
    let blockers = try windows(app).filter { isReconciliationBlockingWindow($0) }
    try require(blockers.count == 1 && CFEqual(blockers[0], dialog),
                "another dialog, sheet or file panel is open")
    let elements = try allElements([dialog], maxDepth: 4, maxElements: 16)
    let rows: [[String: Any]] = try elements.map { element in
        [
            "role": try role(element),
            "subrole": optionalAX(element, kAXSubroleAttribute as String) as Any? ?? NSNull(),
            "title": title(element) as Any? ?? NSNull(),
            "identifier": identifier(element) as Any? ?? NSNull(),
            "description": optionalAX(element, kAXDescriptionAttribute as String) as Any? ?? NSNull(),
            "value": stringValue(element) as Any? ?? NSNull(),
            "enabled": attributeBool(element, kAXEnabledAttribute as String) as Any? ?? NSNull(),
            "press": ((try? actionNames(element)) ?? []).contains(kAXPressAction as String)
        ]
    }
    try require(saveChangesDialogSignature(rows, expectedText: expectedText),
                "Save Changes dialog signature/text/controls differ; no control pressed")
    let buttons = elements.filter {
        (try? role($0)) == (kAXButtonRole as String) &&
        identifier($0) == "Save" &&
        optionalAX($0, kAXDescriptionAttribute as String) as? String == "Save"
    }
    try require(buttons.count == 1, "exact Save Changes Save button is ambiguous")
    return (dialog, buttons[0])
}

func saveCopyDirectoryPin(_ url: URL, empty: Bool) throws -> [String: String] {
    let normalized = try lexicalPath(url.path)
    try require(url.path == normalized, "canonical destination directory required")
    // Foundation rewrites /private/tmp to /tmp even for a canonical POSIX path.
    // lstat each supplied component instead of treating that rewrite as evidence.
    var cursor = URL(fileURLWithPath: "/"), info = stat()
    for part in url.path.split(separator: "/") {
        cursor.appendPathComponent(String(part), isDirectory: true)
        try require(lstat(cursor.path, &info) == 0 && (info.st_mode & S_IFMT) == S_IFDIR, "destination directory has a missing/non-directory/symlink component")
        try require(!FileManager.default.fileExists(atPath: cursor.appendingPathComponent(".git").path), "Save As destination must remain outside Git")
    }
    try require(info.st_uid == getuid() && info.st_mode & 0o077 == 0, "fresh private owned destination directory required")
    if empty { let contents = try FileManager.default.contentsOfDirectory(atPath: url.path); try require(contents.isEmpty, "fresh Save As directory is no longer empty; refuse overwrite") }
    return ["path": url.path, "device": String(info.st_dev), "inode": String(info.st_ino)]
}

func checkedSaveCopyBackup(_ options: Options, source: URL, selected: URL, final: URL, directory: URL) throws -> [String: String] {
    let path = URL(fileURLWithPath: try lexicalPath(options.backupReceipt!))
    try ensureNoSymlinkChain(path, mustExist: true, outsideGit: true)
    let attrs = try fileIdentity(path)
    try require(((attrs[.size] as? NSNumber)?.intValue ?? 131073) <= 131072 &&
                ((attrs[.posixPermissions] as? NSNumber)?.intValue ?? 0o777) & 0o077 == 0 &&
                (attrs[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(), "private bounded Save As backup receipt required")
    guard let proof = try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as? [String: Any],
          let original = proof["source"] as? [String: Any], let backup = proof["backup"] as? [String: Any],
          let backupPath = backup["path"] as? String, let directoryPin = proof["destination_directory"] as? [String: String] else {
        throw LifecycleFailure(phase: phase, message: "invalid Save As source backup proof")
    }
    let currentSource = try setPin(source), currentBackup = try setPin(canonicalURL(backupPath)), currentDirectory = try saveCopyDirectoryPin(directory, empty: true)
    try require(proof["format"] as? String == "codex-devtools-live-set-save-as-backup-v1" && proof["pid"] as? Int == Int(options.pid) &&
                proof["bundle_identifier"] as? String == options.bundleID && proof["project_required_dialog"] as? Bool == options.projectRequiredDialog &&
                proof["destination_set_url"] as? String == selected.absoluteString && proof["expected_final_set_url"] as? String == final.absoluteString &&
                original["sha256"] as? String == options.sourceSHA256 && samePin(original, currentSource) &&
                samePin(backup, currentBackup) && backup["sha256"] as? String == options.sourceSHA256 &&
                directoryPin == currentDirectory, "Save As backup/source/destination/process proof changed")
    let backupAttrs = try fileIdentity(URL(fileURLWithPath: backupPath))
    try require(((backupAttrs[.posixPermissions] as? NSNumber)?.intValue ?? 0o777) & 0o077 == 0 &&
                (backupAttrs[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(), "Save As backup must be private and owned")
    return directoryPin
}

func saveCopyActions(_ options: Options, saved: Bool = false) -> [String: String] {
    ["focus": focusVerified ? "verified" : focusAttempted ? "outcome_unknown" : "not_attempted",
     "menu_navigation": menuNavigationVerified ? "verified" : menuNavigationAttempted ? "outcome_unknown" : "not_attempted",
     "save_as_menu": saveAsMenuAttempted ? (saved ? "verified_panel" : "outcome_unknown") : "not_attempted",
     "project_prompt": projectPromptVerified ? "verified_save_panel" : projectPromptAttempted ? "outcome_unknown" : "not_attempted",
     "save": saved ? "destination_observed" : saveAttempted ? "outcome_unknown" : "not_attempted", "quit": "not_attempted", "open": "not_attempted"]
}

func waitForSavedCopy(app: AXUIElement, process: NSRunningApplication, options: Options, source: URL,
                      before: [String: Any], final: URL, directory: URL, directoryPin: [String: String]) throws -> [String: Any] {
    let deadline = min(saveCopyDeadline!, ProcessInfo.processInfo.systemUptime + 30)
    var previous: [String: Any]?, stable = 0
    while ProcessInfo.processInfo.systemUptime < deadline {
        try requireSameProcess(process, options: options); try saveCopyWindows(app)
        let currentSource = try setPin(source), currentDirectory = try saveCopyDirectoryPin(directory, empty: false)
        try require(samePin(currentSource, before) && directoryPin == currentDirectory, "source or destination directory changed during Save As")
        var info = stat(); let exists = lstat(final.path, &info) == 0
        if !exists { try require(errno == ENOENT, "cannot inspect exact saved destination") }
        if exists {
            do {
                let matching = try windows(app).filter { window in
                    guard let raw = optionalAX(window, kAXDocumentAttribute as String) as? String else { return false }
                    return try sameSetURL(raw, expected: final)
                }
                try require(matching.count <= 1, "ambiguous exact saved Set window")
                guard let window = matching.first else {
                    _ = try exactDocumentWindow(app, url: source); stable = 0; previous = nil
                    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1)); continue
                }
                try require(focusedWindowIs(app, window), "saved Set lost focus")
                _ = try saveCopyTransport(window)
                let pin = try setPin(final)
                stable = previous.map { samePin($0, pin) } == true ? stable + 1 : 0; previous = pin
                if stable >= 2 { try checkSaveCopyDeadline(); return pin }
            } catch is SetObservationChanged { stable = 0; previous = nil }
        } else { _ = try exactDocumentWindow(app, url: source) }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }
    throw LifecycleFailure(phase: phase, message: "Save As outcome unknown: exact final URL/hash/metadata did not settle before deadline; no retry")
}

func stoppedSaveCopy(_ options: Options, error: String) -> [String: Any] {
    ["format": "codex-devtools-live-set-save-as-v1", "status": "stopped", "read_only": !options.execute,
     "target": ["pid": options.pid, "bundle_identifier": options.bundleID], "phase": phase, "error": error,
     "actions": saveCopyActions(options), "manual_inspection_required": uiActionAttempted || focusAttempted || saveAsMenuAttempted || projectPromptAttempted || saveAttempted,
     "backup_receipt": options.backupReceipt as Any? ?? NSNull()]
}

func run(_ options: Options) throws -> [String: Any] {
    phase = "preflight"
    if options.saveCopyOnly { saveCopyDeadline = ProcessInfo.processInfo.systemUptime + 120 }
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
    var initialProjectDialog: AXUIElement?
    if options.saveCopyOnly {
        if options.projectRequiredDialog { initialProjectDialog = try projectRequiredButton(appElement).0 }
        else { try saveCopyWindows(appElement) }
    } else { try ensureNoDialogs(appElement) }
    let exactSourceWindow = try exactDocumentWindow(appElement, url: source)
    var copySourcePin: [String: Any]?, copyDirectoryPin: [String: String]?, copyFinal: URL?
    if options.saveCopyOnly {
        copySourcePin = try setPin(source)
        try require(copySourcePin!["sha256"] as? String == options.sourceSHA256 &&
                    (originalSourceIdentity[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(), "exact owned source hash required for Save As")
        _ = try saveCopyTransport(exactSourceWindow)
        let appURL = URL(fileURLWithPath: options.appPath!)
        try require(appURL.path == options.appPath && appURL.resolvingSymlinksInPath().path == appURL.path && appURL.pathExtension == "app" &&
                    process.bundleURL?.path == appURL.path && Bundle(url: appURL)?.bundleIdentifier == options.bundleID, "exact Save As app differs from running process")
    }

    var destination: URL?
    if let rawDestination = options.destinationSet {
        let target = try canonicalURL(rawDestination)
        if options.saveCopyOnly {
            let directory = target.deletingLastPathComponent(), final = try canonicalURL(options.expectedFinalSet!)
            let project = directory.appendingPathComponent(target.deletingPathExtension().lastPathComponent + " Project").appendingPathComponent(target.lastPathComponent)
            try require(target != source && (final == target || final == project), "explicit final Set must be the exact direct or Live Project form")
            if options.execute { copyDirectoryPin = try checkedSaveCopyBackup(options, source: source, selected: target, final: final, directory: directory) }
            else { try ensureNoSymlinkChain(directory, mustExist: false, outsideGit: true) }
            copyFinal = final
        } else {
            try require(target != source && target.deletingLastPathComponent() == source.deletingLastPathComponent(), "destination must be a new sibling of the exact source Set")
            try ensureNoSymlinkChain(target, mustExist: false, outsideGit: true)
        }
        destination = target
    }
    var pausedApp: [String: Any]?
    if options.stopBeforeReopen {
        guard let path = options.appPath, destination != nil else {
            throw LifecycleFailure(phase: phase, message: "pause requires exact destination and app")
        }
        pausedApp = try lifecycleAppPin(path, bundleID: options.bundleID)
        try require(process.bundleURL?.path == path, "pause app differs from running process")
    }
    guard options.execute else {
        if options.saveCopyOnly {
            return ["format": "codex-devtools-live-set-save-as-v1", "status": "ready", "read_only": true,
                    "target": ["pid": options.pid, "bundle_identifier": options.bundleID], "app": options.appPath!,
                    "source_set_url": source.absoluteString, "destination_set_url": destination!.absoluteString,
                    "expected_final_set_url": copyFinal!.absoluteString, "source_file": copySourcePin!,
                    "project_required_dialog": options.projectRequiredDialog, "transport": try saveCopyTransport(exactSourceWindow),
                    "actions": saveCopyActions(options)]
        }
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
    try activateExactTarget(process, app: appElement, sourceWindow: initialProjectDialog ?? sourceWindow)
    if options.saveCopyOnly {
        try requireSameProcess(process, options: options)
        _ = try checkedSaveCopyBackup(options, source: source, selected: destination, final: copyFinal!, directory: destination.deletingLastPathComponent())
        _ = try saveCopyTransport(sourceWindow)
    }
    if let initialProjectDialog {
        phase = "project-required-dialog"
        let (dialog, projectButton) = try projectRequiredButton(appElement)
        try require(CFEqual(dialog, initialProjectDialog) && focusedWindowIs(appElement, dialog), "Project-required dialog identity/focus changed")
        _ = try exactDocumentWindow(appElement, url: source)
        try checkSaveCopyDeadline(); projectPromptAttempted = true
        try press(projectButton)
    } else {
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
    }
    phase = "save-dialog"
    let saveWindow = try waitForWindow(appElement, title: "Save")
    if options.saveCopyOnly {
        try require(identifier(saveWindow) == "save-panel", "exact Save panel identifier required")
        try saveCopyWindows(appElement, allowing: saveWindow)
    }
    if projectPromptAttempted { projectPromptVerified = true }
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
                                                 originalSourceIdentity: originalSourceIdentity, destination: destination,
                                                 additionalGuard: options.saveCopyOnly ? {
        try checkSaveCopyDeadline(); try requireSameProcess(process, options: options)
        try saveCopyWindows(appElement, allowing: refreshedWindow)
        _ = try saveCopyTransport(sourceWindow)
        _ = try checkedSaveCopyBackup(options, source: source, selected: destination, final: copyFinal!, directory: destination.deletingLastPathComponent())
    } : nil)

    phase = "save"
    try checkSaveCopyDeadline()
    saveAttempted = true
    try press(saveButton)
    phase = "verify-saved-set"
    if options.saveCopyOnly {
        let saved = try waitForSavedCopy(app: appElement, process: process, options: options, source: source, before: copySourcePin!,
                                         final: copyFinal!, directory: destination.deletingLastPathComponent(), directoryPin: copyDirectoryPin!)
        let finalWindow = try exactDocumentWindow(appElement, url: copyFinal!)
        let transport = try saveCopyTransport(finalWindow), afterSource = try setPin(source)
        try require(samePin(afterSource, copySourcePin!), "source changed after Save As")
        try requireSameProcess(process, options: options); try checkSaveCopyDeadline()
        return ["format": "codex-devtools-live-set-save-as-v1", "status": "saved-copy", "read_only": false, "same_process": true,
                "target": ["pid": options.pid, "bundle_identifier": options.bundleID], "app": options.appPath!,
                "source_set_url": source.absoluteString, "destination_set_url": destination.absoluteString, "expected_final_set_url": copyFinal!.absoluteString,
                "saved_set_url": copyFinal!.absoluteString, "source_file": copySourcePin!, "source_file_after_save": afterSource, "destination_file": saved,
                "destination_directory": copyDirectoryPin!, "project_required_dialog": options.projectRequiredDialog, "transport": transport,
                "stable_observations": 3, "backup_receipt": options.backupReceipt!, "actions": saveCopyActions(options, saved: true),
                "evidence_limit": "same-process AX URL and stable file bytes/metadata only; M4L state, dependency resolution, silence and recall unproven"]
    }
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

    let pausedSet = options.stopBeforeReopen ? try setPin(destination) : nil
    if let pausedApp {
        let currentApp = try lifecycleAppPin(rawAppPath, bundleID: options.bundleID)
        try require(samePin(pausedApp, currentApp), "app changed before Quit")
    }

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

    if options.stopBeforeReopen {
        let currentSet = try setPin(destination)
        let currentApp = try lifecycleAppPin(rawAppPath, bundleID: options.bundleID)
        try require(samePin(pausedSet!, currentSet) && samePin(pausedApp!, currentApp),
                    "saved Set or app changed during Quit; no resumable receipt")
        return ["format": pausedLifecycleFormat, "mode": "stop-before-reopen", "status": "saved-and-quit",
                "target": ["pid": options.pid, "bundle_identifier": options.bundleID],
                "source_set_url": source.absoluteString, "destination_set_url": destination.absoluteString,
                "saved_set": currentSet, "app": pausedApp!, "read_only": false, "actions": pausedLifecycleActions,
                "paused_at_utc": ISO8601DateFormatter().string(from: Date()),
                "evidence_limit": pausedLifecycleLimit]
    }
    return try reopenLifecycleSet(destination: destination, appURL: appURL, oldPID: options.pid, bundleID: options.bundleID,
                                  processName: process.localizedName ?? "", source: source)
}

func reopenLifecycleSet(destination: URL, appURL: URL, oldPID: pid_t, bundleID: String,
                        processName: String, source: URL) throws -> [String: Any] {
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
    try require(reopened.bundleIdentifier == bundleID && reopened.bundleURL?.path == appURL.path && reopened.processIdentifier > 0 && reopened.processIdentifier != oldPID, "reopened Live process identity is unexpected")

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
            "target": ["pid": oldPID, "bundle_identifier": bundleID,
                       "process_name": processName],
            "reopened_target": ["pid": reopened.processIdentifier, "bundle_identifier": reopened.bundleIdentifier ?? "",
                                "process_name": reopened.localizedName ?? ""],
            "source_set_url": source.absoluteString, "destination_set_url": destination.absoluteString,
            "read_only": false,
            "actions": ["focus": "verified", "menu_navigation": "verified", "save_as_menu": "verified_panel", "save": "destination_observed", "quit": "terminated", "open": "exact_set_window_observed"],
            "evidence_limit": "AX document URL and nonempty regular-file observation; host LOM path and recall state require separate verification"]
}

// Existing-Set mode reuses the lifecycle AX/menu/focus and LaunchServices helpers.
struct SetObservationChanged: Error {}
let maxPinnedSetBytes: Int64 = 128 * 1024 * 1024
var openSourcePin: [String: Any]?

func setPin(_ url: URL, maxBytes: Int64 = maxPinnedSetBytes) throws -> [String: Any] {
    try ensureNoSymlinkChain(url, mustExist: true, outsideGit: true)
    let fd = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW)
    try require(fd >= 0, "cannot open exact Set file without following symlinks")
    defer { Darwin.close(fd) }
    var before = stat(), after = stat(), current = stat()
    try require(fstat(fd, &before) == 0 && (before.st_mode & S_IFMT) == S_IFREG, "Set must be a regular file")
    try require(before.st_size <= maxBytes, "Set exceeds bounded file size")
    if before.st_size <= 0 { throw SetObservationChanged() }
    var digest = SHA256(), count: Int64 = 0, buffer = [UInt8](repeating: 0, count: 65536)
    while true {
        let size = buffer.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
        try require(size >= 0, "Set file read failed")
        if size == 0 { break }
        count += Int64(size)
        try require(count <= maxBytes, "Set exceeded file bound while reading")
        digest.update(data: Data(buffer.prefix(size)))
    }
    try require(fstat(fd, &after) == 0 && lstat(url.path, &current) == 0, "Set disappeared while reading")
    if before.st_dev != after.st_dev || before.st_ino != after.st_ino || before.st_size != count || before.st_size != after.st_size ||
        before.st_mtimespec.tv_sec != after.st_mtimespec.tv_sec || before.st_mtimespec.tv_nsec != after.st_mtimespec.tv_nsec ||
        before.st_ctimespec.tv_sec != after.st_ctimespec.tv_sec || before.st_ctimespec.tv_nsec != after.st_ctimespec.tv_nsec ||
        current.st_dev != before.st_dev || current.st_ino != before.st_ino || current.st_size != before.st_size ||
        current.st_mtimespec.tv_sec != before.st_mtimespec.tv_sec || current.st_mtimespec.tv_nsec != before.st_mtimespec.tv_nsec ||
        current.st_ctimespec.tv_sec != before.st_ctimespec.tv_sec || current.st_ctimespec.tv_nsec != before.st_ctimespec.tv_nsec { throw SetObservationChanged() }
    return ["path": url.path, "sha256": digest.finalize().map { String(format: "%02x", $0) }.joined(),
            "bytes": count, "device": String(before.st_dev), "inode": String(before.st_ino),
            "mtime_seconds": before.st_mtimespec.tv_sec, "mtime_nanoseconds": before.st_mtimespec.tv_nsec,
            "ctime_seconds": before.st_ctimespec.tv_sec, "ctime_nanoseconds": before.st_ctimespec.tv_nsec]
}

func samePin(_ left: [String: Any], _ right: [String: Any]) -> Bool {
    NSDictionary(dictionary: left).isEqual(to: right)
}

// Only the destination after the issued open may differ in ctime. Pre-open
// approval and the retained source continue to require the complete pin.
func sameOpenedDestination(_ before: [String: Any], _ after: [String: Any]) -> Bool {
    var left = before, right = after
    for key in ["ctime_seconds", "ctime_nanoseconds"] {
        left.removeValue(forKey: key); right.removeValue(forKey: key)
    }
    return samePin(left, right)
}

func sameFileMenuIdentity(_ before: [String: Any], _ after: [String: Any]) -> Bool {
    var left = before, right = after
    left.removeValue(forKey: "selected"); right.removeValue(forKey: "selected")
    return NSDictionary(dictionary: left).isEqual(to: right)
}

func isTransientPostCancelAXReadFailure(_ error: Error) -> Bool {
    guard let failure = error as? LifecycleFailure else { return false }
    if failure.message == "target has no readable AX windows" { return true }
    return failure.message.contains("failed (-25204)") ||
        failure.message.contains("unavailable (-25204)")
}

func canWaitAfterTransientPostCancelRead(_ error: Error, process: NSRunningApplication,
                                         generation: Int64, deadline: TimeInterval,
                                         noObservedBlockers: Bool) -> Bool {
    guard pendingOpenBlockerScanAllowsWait(PendingOpenBlockerScanFailureEvidence(
        transientUnavailable: isTransientPostCancelAXReadFailure(error),
        blockerAlreadyObserved: !noObservedBlockers)) else { return false }
    return pendingOpenPostCancelWaitAllows(PendingOpenPostCancelWaitEvidence(
        withinDeadline: ProcessInfo.processInfo.systemUptime < deadline,
        exactProcess: !process.isTerminated,
        expectedGeneration: processGenerationValue(process.launchDate ?? .distantPast) == generation,
        noObservedBlockers: noObservedBlockers,
        readState: .transientUnavailable))
}

func readPinnedJSONReceipt(_ path: String, expectedSHA256: String) throws -> [String: Any] {
    try require(expectedSHA256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                "reviewed receipt SHA-256 is malformed")
    let url = URL(fileURLWithPath: try lexicalPath(path))
    try ensureNoSymlinkChain(url, mustExist: true, outsideGit: true)
    let before = try fileIdentity(url)
    try require((before[.ownerAccountID] as? NSNumber)?.uint32Value == getuid() &&
                (before[.type] as? FileAttributeType) == .typeRegular &&
                ((before[.size] as? NSNumber)?.intValue ?? 65537) <= 65536,
                "reviewed receipt must be a bounded current-user regular file")
    let data = try Data(contentsOf: url, options: .mappedIfSafe)
    let after = try fileIdentity(url)
    try require(sameFileIdentity(before, after) && data.count > 0 && data.count <= 65536,
                "reviewed receipt changed or exceeded its bound")
    let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    try require(digest == expectedSHA256, "reviewed receipt SHA-256 mismatch")
    guard let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw LifecycleFailure(phase: phase, message: "reviewed receipt must be a JSON object")
    }
    return result
}

func originalUnknownOpenReceiptIsExact(_ receipt: [String: Any], pid: pid_t, bundleID: String) -> Bool {
    let expectedKeys: Set<String> = [
        "actions", "backup_receipt", "error", "format", "manual_inspection_required",
        "phase", "read_only", "source_backup", "status", "target"
    ]
    guard Set(receipt.keys) == expectedKeys,
          receipt["format"] as? String == "codex-devtools-live-set-open-v1",
          receipt["status"] as? String == "stopped",
          receipt["phase"] as? String == "verify-opened-existing-set",
          receipt["read_only"] as? Bool == false,
          receipt["manual_inspection_required"] as? Bool == true,
          let error = receipt["error"] as? String,
          !error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
          receipt["backup_receipt"] is NSNull,
          receipt["source_backup"] is NSNull,
          let target = receipt["target"] as? [String: Any],
          Set(target.keys) == ["pid", "bundle_identifier"],
          (target["pid"] as? NSNumber)?.intValue == Int(pid),
          target["bundle_identifier"] as? String == bundleID,
          let actions = receipt["actions"] as? [String: String],
          Set(actions.keys) == Set(["focus", "menu_navigation", "save_as_menu", "save", "quit", "open"]) else {
        return false
    }
    return actions["focus"] == "verified" && actions["menu_navigation"] == "not_attempted" &&
        actions["save_as_menu"] == "not_attempted" && actions["save"] == "not_attempted" &&
        actions["quit"] == "not_attempted" && actions["open"] == "outcome_unknown"
}

func fileMenuCancelPreflightReceiptIsExact(
    _ receipt: [String: Any], options: Options, source: URL, destination: URL,
    sourcePin: [String: Any], destinationPin: [String: Any], originalReceiptSHA256: String,
    marker: URL
) -> Bool {
    let expectedKeys: Set<String> = [
        "actions", "attempt_marker", "blocking_windows", "destination_file", "destination_set_url",
        "file_menu", "focused_window_matches_source", "format", "open_initiated_by_helper",
        "original_open_receipt_sha256", "pending_destination_binding_pre_action", "phase",
        "read_only", "selected_menu_count", "source_file", "source_set_url", "source_transport",
        "status", "target"
    ]
    guard Set(receipt.keys) == expectedKeys,
          receipt["format"] as? String == "codex-devtools-live-set-open-v1",
          receipt["status"] as? String == "ready",
          receipt["phase"] as? String == "file-menu-cancel-preflight",
          receipt["read_only"] as? Bool == true,
          let target = receipt["target"] as? [String: Any],
          Set(target.keys) == ["pid", "bundle_identifier", "process_generation"],
          (target["pid"] as? NSNumber)?.intValue == Int(options.pid),
          target["bundle_identifier"] as? String == options.bundleID,
          (target["process_generation"] as? NSNumber)?.int64Value == options.expectedGeneration,
          receipt["source_set_url"] as? String == source.absoluteString,
          receipt["destination_set_url"] as? String == destination.absoluteString,
          let observedSource = receipt["source_file"] as? [String: Any], samePin(observedSource, sourcePin),
          let observedDestination = receipt["destination_file"] as? [String: Any], samePin(observedDestination, destinationPin),
          receipt["original_open_receipt_sha256"] as? String == originalReceiptSHA256,
          receipt["pending_destination_binding_pre_action"] as? String == "unverified",
          receipt["open_initiated_by_helper"] as? Bool == false,
          receipt["attempt_marker"] as? String == marker.path,
          receipt["focused_window_matches_source"] as? Bool == true,
          (receipt["blocking_windows"] as? [Any])?.isEmpty == true,
          (receipt["selected_menu_count"] as? NSNumber)?.intValue == 1,
          let transport = receipt["source_transport"] as? [String: Int],
          transport == ["play": 0, "link": 0, "arrangement_record": 0, "session_record": 0],
          let menu = receipt["file_menu"] as? [String: Any],
          Set(menu.keys) == ["actions", "cancel_action", "enabled", "role", "selected", "title"],
          menu["role"] as? String == "AXMenuBarItem", menu["title"] as? String == "File",
          menu["selected"] as? Bool == true, menu["enabled"] as? Bool == true,
          menu["cancel_action"] as? Bool == true,
          menu["actions"] as? [String] == [kAXCancelAction as String],
          let actions = receipt["actions"] as? [String: String],
          actions == ["focus": "verified", "file_menu_cancel": "not_attempted", "open": "outcome_unknown"] else {
        return false
    }
    return true
}

func exactFileMenuSnapshot(_ app: AXUIElement) throws -> (AXUIElement, [String: Any], Int) {
    let menuBar = try ax(app, kAXMenuBarAttribute as String) as! AXUIElement
    let top = try children(menuBar)
    let menuItems = try top.filter { try role($0) == (kAXMenuBarItemRole as String) }
    let titles = try menuItems.map { try stringAttribute($0, kAXTitleAttribute as String) }
    let fileMenus = zip(menuItems, titles).filter { $0.1 == "File" }.map { $0.0 }
    try require(fileMenus.count == 1, "exact File AXMenuBarItem is absent or ambiguous")
    let selectionValues = try menuItems.map { item -> Bool in
        guard let number = try ax(item, kAXSelectedAttribute as String) as? NSNumber else {
            throw LifecycleFailure(phase: phase, message: "top-level menu selected state is not boolean")
        }
        return number.boolValue
    }
    let selected = menuItems.enumerated().filter { selectionValues[$0.offset] }.map { $0.element }
    let fileMenu = fileMenus[0]
    guard let fileMenuIndex = menuItems.firstIndex(where: { CFEqual($0, fileMenu) }) else {
        throw LifecycleFailure(phase: phase, message: "File menu was lost while reading its selected state")
    }
    let actions = try actionNames(fileMenu)
    let selectedValue = selectionValues[fileMenuIndex]
    guard let enabledValue = try ax(fileMenu, kAXEnabledAttribute as String) as? NSNumber else {
        throw LifecycleFailure(phase: phase, message: "File menu enabled state is not boolean")
    }
    let snapshot: [String: Any] = [
        "role": try role(fileMenu), "title": titles[fileMenuIndex],
        "selected": selectedValue,
        "enabled": enabledValue.boolValue,
        "actions": actions.filter { $0 == (kAXCancelAction as String) },
        "cancel_action": actions.contains(kAXCancelAction as String)
    ]
    return (fileMenu, snapshot, selected.count)
}

func createFileMenuCancelAttemptMarker(_ marker: URL, options: Options, receiptSHA256: String,
                                       preflightSHA256: String, sourcePin: [String: Any],
                                       destinationPin: [String: Any]) throws {
    try ensureNoSymlinkChain(marker, mustExist: false, outsideGit: true)
    try requireAbsentLeaf(marker.path, "one-use File-menu cancellation marker already exists; never retry")
    let directory = try fileIdentity(marker.deletingLastPathComponent())
    try require((directory[.type] as? FileAttributeType) == .typeDirectory &&
                (directory[.ownerAccountID] as? NSNumber)?.uint32Value == getuid() &&
                ((directory[.posixPermissions] as? NSNumber)?.intValue ?? 0o777) & 0o077 == 0,
                "one-use cancellation marker directory must be private and owned")
    let value: [String: Any] = [
        "format": "codex-devtools-live-file-menu-cancel-attempt-v1",
        "pid": options.pid, "bundle_identifier": options.bundleID,
        "process_generation": options.expectedGeneration!,
        "original_open_receipt_sha256": receiptSHA256,
        "preflight_receipt_sha256": preflightSHA256,
        "source_file": sourcePin, "destination_file": destinationPin,
        "pending_destination_binding_pre_action": "unverified",
        "native_action": "one exact File AXCancel only; no Open/Save/Quit",
        "attempt_admitted_at_utc": ISO8601DateFormatter().string(from: Date())
    ]
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .prettyPrinted])
    let fd = Darwin.open(marker.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, S_IRUSR | S_IWUSR)
    if fd >= 0 { attemptMarkerCreated = true }
    try require(fd >= 0, "could not exclusively create the one-use cancellation marker")
    defer { Darwin.close(fd) }
    var written = 0
    while written < data.count {
        let amount = data.withUnsafeBytes { bytes in
            Darwin.write(fd, bytes.baseAddress!.advanced(by: written), data.count - written)
        }
        try require(amount > 0, "could not write complete one-use cancellation marker")
        written += amount
    }
    try require(fsync(fd) == 0, "could not fsync one-use cancellation marker")
}

func fileMenuCancelReceiptBase(_ options: Options) -> [String: Any] {
    ["format": "codex-devtools-live-set-open-v1",
     "target": ["pid": options.pid, "bundle_identifier": options.bundleID,
                "process_generation": options.expectedGeneration as Any? ?? NSNull()],
     "source_set_url": cancelSourceURL?.absoluteString ?? "",
     "destination_set_url": cancelDestinationURL?.absoluteString ?? "",
     "source_file": cancelSourcePin as Any? ?? NSNull(),
     "destination_file": cancelDestinationPin as Any? ?? NSNull(),
     "original_open_receipt_sha256": cancelOriginalReceiptSHA256,
     "pending_destination_binding_pre_action": "unverified",
     "open_initiated_by_helper": false,
     "attempt_marker": cancelAttemptMarkerURL?.path ?? ""]
}

func stoppedFileMenuCancel(_ options: Options, error: String) -> [String: Any] {
    var report = fileMenuCancelReceiptBase(options)
    report["status"] = "stopped"
    report["phase"] = phase
    report["error"] = error
    report["read_only"] = !options.execute
    report["actions"] = [
        "focus": cancelFocusVerified ? "verified" : (focusAttempted ? "outcome_unknown" : "not_attempted"),
        "file_menu_cancel": fileMenuCancelAttempted ? "outcome_unknown" : "not_attempted",
        "open": "outcome_unknown"
    ]
    report["manual_inspection_required"] = attemptMarkerCreated
    return report
}

func runFileMenuCancelObserve(_ options: Options) throws -> [String: Any] {
    phase = "file-menu-cancel-preflight"
    initialPID = Int(options.pid); initialBundleID = options.bundleID
    guard let generation = options.expectedGeneration,
          let originalPath = options.originalOpenReceipt,
          let originalSHA = options.originalOpenReceiptSHA256,
          let markerPath = options.attemptMarker else {
        throw LifecycleFailure(phase: phase, message: "exact generation, original receipt and one-use marker required")
    }
    cancelOriginalReceiptSHA256 = originalSHA
    let source = try canonicalURL(options.sourceSet)
    let destination = try canonicalURL(options.destinationSet!)
    let marker = URL(fileURLWithPath: try lexicalPath(markerPath))
    cancelSourceURL = source; cancelDestinationURL = destination; cancelAttemptMarkerURL = marker
    guard let appPath = options.appPath else {
        throw LifecycleFailure(phase: phase, message: "exact Live app path required")
    }
    let appURL = URL(fileURLWithPath: try lexicalPath(appPath))
    try require(appURL.pathExtension == "app" && appURL.path == appPath &&
                appURL.resolvingSymlinksInPath().path == appURL.path,
                "exact canonical Live app path required")
    let originalReceiptURL = URL(fileURLWithPath: try lexicalPath(originalPath))
    let originalReceipt = try readPinnedJSONReceipt(originalReceiptURL.path, expectedSHA256: originalSHA)
    try require(originalUnknownOpenReceiptIsExact(originalReceipt, pid: options.pid, bundleID: options.bundleID),
                "receipt does not bind the exact original unknown Open attempt")
    try require(AXIsProcessTrusted(), "Accessibility permission is not already trusted")
    guard let process = NSRunningApplication(processIdentifier: options.pid) else {
        throw LifecycleFailure(phase: phase, message: "exact target Live PID is unavailable")
    }
    try require(process.bundleIdentifier == options.bundleID, "exact PID bundle identifier mismatch")
    try requireExpectedGeneration(process, options: options)
    let running = NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == options.bundleID }
    try require(running.count == 1 && running[0].processIdentifier == options.pid,
                "ambiguous running Live applications")
    try require(process.bundleURL?.path == appURL.path &&
                Bundle(url: appURL)?.bundleIdentifier == options.bundleID,
                "running Live app identity differs from the exact app bundle")
    let sourcePin = try setPin(source), destinationPin = try setPin(destination)
    cancelSourcePin = sourcePin; cancelDestinationPin = destinationPin
    try require(source != destination &&
                !(sourcePin["device"] as? String == destinationPin["device"] as? String &&
                  sourcePin["inode"] as? String == destinationPin["inode"] as? String),
                "source/destination aliases are forbidden")
    try require(sourcePin["sha256"] as? String == options.sourceSHA256 &&
                destinationPin["sha256"] as? String == options.destinationSHA256,
                "reviewed source/destination SHA-256 mismatch")
    try ensureNoSymlinkChain(marker, mustExist: false, outsideGit: true)
    try requireAbsentLeaf(marker.path, "one-use File-menu cancellation marker already exists; never retry")
    let app = AXUIElementCreateApplication(options.pid)
    try ensureNoDialogs(app)
    let sourceWindow = try exactDocumentWindow(app, url: source)
    try require(focusedWindowIs(app, sourceWindow), "exact source Set is not the focused window")
    cancelFocusVerified = true
    let sourceTransport = try saveCopyTransport(sourceWindow)
    try require(sourceTransport == ["play": 0, "link": 0, "arrangement_record": 0, "session_record": 0],
                "source transport is not stopped")
    let (menu, menuSignature, selectedCount) = try exactFileMenuSnapshot(app)
    try require(selectedCount == 1 && menuSignature["selected"] as? Bool == true &&
                menuSignature["enabled"] as? Bool == true &&
                menuSignature["cancel_action"] as? Bool == true &&
                menuSignature["actions"] as? [String] == [kAXCancelAction as String],
                "unique selected enabled File menu with AXCancel is not observed")
    let evidence = FileMenuCancelPreflightEvidence(
        exactProcess: !process.isTerminated && process.bundleIdentifier == options.bundleID,
        expectedGeneration: processGenerationValue(process.launchDate ?? .distantPast) == generation,
        currentDesktop: NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid,
        exactSourceWindowFocused: focusedWindowIs(app, sourceWindow),
        sourcePinMatches: samePin(try setPin(source), sourcePin),
        destinationPinMatches: samePin(try setPin(destination), destinationPin),
        transportStopped: sourceTransport.values.allSatisfy { $0 == 0 },
        noBlockingWindows: try windows(app).allSatisfy { !isBlockingWindow($0) },
        uniqueFileMenuCount: 1, selectedMenuCount: selectedCount,
        fileMenuSelected: menuSignature["selected"] as? Bool == true,
        fileMenuEnabled: menuSignature["enabled"] as? Bool == true,
        cancelActionAvailable: menuSignature["cancel_action"] as? Bool == true)
    try require(fileMenuCancelPreflightAllows(evidence), "File-menu cancellation preflight changed")

    if !options.execute {
        var report = fileMenuCancelReceiptBase(options)
        report["status"] = "ready"
        report["phase"] = "file-menu-cancel-preflight"
        report["read_only"] = true
        report["source_transport"] = sourceTransport
        report["focused_window_matches_source"] = true
        report["blocking_windows"] = []
        report["selected_menu_count"] = selectedCount
        report["file_menu"] = menuSignature
        report["actions"] = ["focus": "verified", "file_menu_cancel": "not_attempted", "open": "outcome_unknown"]
        return report
    }

    phase = "file-menu-cancel-review"
    guard let preflightPath = options.preflightReceipt,
          let preflightSHA = options.preflightReceiptSHA256 else {
        throw LifecycleFailure(phase: phase, message: "execute requires reviewed preflight receipt and SHA-256")
    }
    let preflightURL = URL(fileURLWithPath: try lexicalPath(preflightPath))
    let reviewedPreflight = try readPinnedJSONReceipt(preflightURL.path, expectedSHA256: preflightSHA)
    try require(fileMenuCancelPreflightReceiptIsExact(
        reviewedPreflight, options: options, source: source, destination: destination,
        sourcePin: sourcePin, destinationPin: destinationPin, originalReceiptSHA256: originalSHA,
        marker: marker), "reviewed File-menu preflight receipt differs from current evidence")
    try requireSameProcess(process, options: options)
    try ensureNoDialogs(app)
    let finalSource = try exactDocumentWindow(app, url: source)
    try require(focusedWindowIs(app, finalSource), "source focus changed before cancellation")
    let sourceBeforeCancel = try setPin(source), destinationBeforeCancel = try setPin(destination)
    try require(samePin(sourceBeforeCancel, sourcePin) && samePin(destinationBeforeCancel, destinationPin),
                "source/destination pin changed before cancellation")
    let finalTransport = try saveCopyTransport(finalSource)
    try require(finalTransport == sourceTransport, "source transport changed before cancellation")
    let (finalMenu, finalMenuSignature, finalSelectedCount) = try exactFileMenuSnapshot(app)
    try require(finalSelectedCount == 1 && NSDictionary(dictionary: finalMenuSignature).isEqual(to: menuSignature) &&
                CFEqual(finalMenu, menu), "selected File menu changed before cancellation")
    let sourceAtAction = try setPin(source), destinationAtAction = try setPin(destination)
    let windowsAtAction = try windows(app)
    let actionEvidence = FileMenuCancelPreflightEvidence(
        exactProcess: !process.isTerminated, expectedGeneration: processGenerationValue(process.launchDate ?? .distantPast) == generation,
        currentDesktop: NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid,
        exactSourceWindowFocused: focusedWindowIs(app, finalSource),
        sourcePinMatches: samePin(sourceAtAction, sourcePin),
        destinationPinMatches: samePin(destinationAtAction, destinationPin),
        transportStopped: finalTransport.values.allSatisfy { $0 == 0 },
        noBlockingWindows: windowsAtAction.allSatisfy { !isBlockingWindow($0) },
        uniqueFileMenuCount: 1, selectedMenuCount: finalSelectedCount,
        fileMenuSelected: finalMenuSignature["selected"] as? Bool == true,
        fileMenuEnabled: finalMenuSignature["enabled"] as? Bool == true,
        cancelActionAvailable: finalMenuSignature["cancel_action"] as? Bool == true)
    try require(fileMenuCancelPreflightAllows(actionEvidence),
        "File-menu cancellation conditions changed at the action boundary")

    try createFileMenuCancelAttemptMarker(marker, options: options, receiptSHA256: originalSHA,
                                           preflightSHA256: preflightSHA, sourcePin: sourcePin,
                                           destinationPin: destinationPin)
    phase = "file-menu-cancel-post-marker-guard"
    try requireSameProcess(process, options: options)
    try requireExpectedGeneration(process, options: options)
    try ensureNoDialogs(app)
    let postMarkerSource = try exactDocumentWindow(app, url: source)
    try require(CFEqual(postMarkerSource, sourceWindow) && focusedWindowIs(app, postMarkerSource),
                "source focus/window changed after marker creation")
    let postMarkerSourcePin = try setPin(source), postMarkerDestinationPin = try setPin(destination)
    try require(samePin(postMarkerSourcePin, sourcePin) && samePin(postMarkerDestinationPin, destinationPin),
                "source/destination pin changed after marker creation")
    let postMarkerTransport = try saveCopyTransportStrict(postMarkerSource)
    try require(postMarkerTransport == sourceTransport, "source transport changed after marker creation")
    let (postMarkerMenu, postMarkerMenuSignature, postMarkerSelectedCount) = try exactFileMenuSnapshot(app)
    let postMarkerWindows = try windows(app)
    var postMarkerBlockers: [AXUIElement] = []
    for window in postMarkerWindows where try isBlockingWindowStrict(window) {
        postMarkerBlockers.append(window)
    }
    let postMarkerEvidence = FileMenuCancelPreflightEvidence(
        exactProcess: !process.isTerminated && process.bundleIdentifier == options.bundleID,
        expectedGeneration: processGenerationValue(process.launchDate ?? .distantPast) == generation,
        currentDesktop: NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid,
        exactSourceWindowFocused: focusedWindowIs(app, postMarkerSource),
        sourcePinMatches: samePin(postMarkerSourcePin, sourcePin),
        destinationPinMatches: samePin(postMarkerDestinationPin, destinationPin),
        transportStopped: postMarkerTransport.values.allSatisfy { $0 == 0 },
        noBlockingWindows: postMarkerBlockers.isEmpty,
        uniqueFileMenuCount: CFEqual(postMarkerMenu, menu) ? 1 : 0,
        selectedMenuCount: postMarkerSelectedCount,
        fileMenuSelected: postMarkerMenuSignature["selected"] as? Bool == true,
        fileMenuEnabled: postMarkerMenuSignature["enabled"] as? Bool == true,
        cancelActionAvailable: postMarkerMenuSignature["cancel_action"] as? Bool == true)
    try require(fileMenuCancelPreflightAllows(postMarkerEvidence) &&
                NSDictionary(dictionary: postMarkerMenuSignature).isEqual(to: menuSignature),
                "File-menu cancel guards changed after one-use marker creation")
    let (lastMenu, lastMenuSignature, lastSelectedCount) = try exactFileMenuSnapshot(app)
    try require(CFEqual(lastMenu, menu) && lastSelectedCount == 1 &&
                NSDictionary(dictionary: lastMenuSignature).isEqual(to: menuSignature) &&
                focusedWindowIs(app, postMarkerSource),
                "exact source/menu action boundary changed after marker creation")
    try requireSameProcess(process, options: options)
    try requireExpectedGeneration(process, options: options)
    phase = "cancel-selected-file-menu"
    fileMenuCancelAttempted = true
    uiActionAttempted = true
    AXUIElementSetMessagingTimeout(lastMenu, 2)
    let cancelError = AXUIElementPerformAction(lastMenu, kAXCancelAction as CFString)
    try require(cancelError == .success, "AXCancel returned failure; outcome unknown and never retry")
    phase = "observe-original-pending-open"
    let deadline = ProcessInfo.processInfo.systemUptime + 90
    var stable = 0
    while ProcessInfo.processInfo.systemUptime < deadline {
        try requireSameProcess(process, options: options)
        try requireExpectedGeneration(process, options: options)
        let currentWindows: [AXUIElement]
        do {
            currentWindows = try windows(app)
        } catch {
            guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                       deadline: deadline, noObservedBlockers: true) else { throw error }
            stable = 0
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
            continue
        }
        var observedBlockers: [AXUIElement] = []
        let blockers: [AXUIElement]
        do {
            for window in currentWindows where try isBlockingWindowStrict(window) { observedBlockers.append(window) }
            blockers = observedBlockers
        } catch {
            guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                       deadline: deadline, noObservedBlockers: observedBlockers.isEmpty) else { throw error }
            stable = 0
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
            continue
        }
        try require(blockers.isEmpty, "blocking window appeared while observing original Open")
        let (currentMenu, currentMenuSignature, selectedMenus): (AXUIElement, [String: Any], Int)
        do {
            (currentMenu, currentMenuSignature, selectedMenus) = try exactFileMenuSnapshot(app)
        } catch {
            guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                       deadline: deadline, noObservedBlockers: blockers.isEmpty) else { throw error }
            stable = 0
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
            continue
        }
        try require(CFEqual(currentMenu, lastMenu) && sameFileMenuIdentity(lastMenuSignature, currentMenuSignature),
                    "File menu identity/signature changed after AXCancel")
        if selectedMenus == 1 && NSDictionary(dictionary: currentMenuSignature).isEqual(to: menuSignature) {
            stable = 0
            try require(pendingOpenPostCancelWaitAllows(PendingOpenPostCancelWaitEvidence(
                withinDeadline: ProcessInfo.processInfo.systemUptime < deadline,
                exactProcess: !process.isTerminated,
                expectedGeneration: processGenerationValue(process.launchDate ?? .distantPast) == generation,
                noObservedBlockers: blockers.isEmpty, readState: .originalMenuStillSelected)),
                "unchanged selected File menu exceeded the safe wait boundary")
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
            continue
        }
        try require(selectedMenus == 0 && currentMenuSignature["selected"] as? Bool == false,
                    "a changed or additional menu remained selected after AXCancel")
        var destinationMatches: [AXUIElement] = []
        do {
            for window in currentWindows {
                guard try role(window) == (kAXWindowRole as String) else { continue }
                guard let raw = try optionalAXThrowing(window, kAXDocumentAttribute as String) as? String else { continue }
                if try sameSetURL(raw, expected: destination) { destinationMatches.append(window) }
            }
        } catch {
            guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                       deadline: deadline, noObservedBlockers: blockers.isEmpty) else { throw error }
            stable = 0
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
            continue
        }
        try require(destinationMatches.count <= 1, "destination URL became ambiguous")
        if let observed = destinationMatches.first {
            let destinationFocused: Bool
            do {
                destinationFocused = try focusedWindowIsThrowing(app, observed)
            } catch {
                guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                           deadline: deadline, noObservedBlockers: blockers.isEmpty) else { throw error }
                stable = 0
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
                continue
            }
            try require(destinationFocused, "exact destination Set is not focused")
            let currentDestination = try setPin(destination)
            let currentSource = try setPin(source)
            try require(samePin(currentSource, sourcePin) && sameOpenedDestination(destinationPin, currentDestination),
                        "source or destination file pin changed unexpectedly")
            let destinationTransport: [String: Int]
            do {
                destinationTransport = try saveCopyTransportStrict(observed)
            } catch {
                guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                           deadline: deadline, noObservedBlockers: blockers.isEmpty) else { throw error }
                stable = 0
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
                continue
            }
            try require(destinationTransport.values.allSatisfy { $0 == 0 }, "destination transport is not stopped")
            let successEvidence = PendingOpenSuccessEvidence(
                withinDeadline: ProcessInfo.processInfo.systemUptime < deadline,
                exactProcess: !process.isTerminated && process.bundleIdentifier == options.bundleID,
                expectedGeneration: processGenerationValue(process.launchDate ?? .distantPast) == generation,
                exactDestinationFocused: destinationFocused,
                destinationPinStable: sameOpenedDestination(destinationPin, currentDestination),
                sourcePinUnchanged: samePin(sourcePin, currentSource),
                sourceAndDestinationTransportStopped: sourceTransport.values.allSatisfy { $0 == 0 } &&
                    destinationTransport.values.allSatisfy { $0 == 0 },
                noBlockingWindows: blockers.isEmpty,
                fileMenuClosed: selectedMenus == 0)
            try require(pendingOpenSuccessAllows(successEvidence),
                        "original Open destination acceptance evidence is incomplete")
            try requireSameProcess(process, options: options)
            let destinationStillFocused: Bool
            do {
                destinationStillFocused = try focusedWindowIsThrowing(app, observed)
            } catch {
                guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                           deadline: deadline, noObservedBlockers: blockers.isEmpty) else { throw error }
                stable = 0
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
                continue
            }
            try require(destinationStillFocused, "destination focus changed during verification")
            let finalSource = try setPin(source), finalDestination = try setPin(destination)
            try require(samePin(finalSource, sourcePin) && sameOpenedDestination(destinationPin, finalDestination),
                        "Set bytes/identity changed during stable destination observation")
            stable += 1
            if stable >= 3 {
                var finalBlockerObserved = false
                do {
                    try require(ProcessInfo.processInfo.systemUptime < deadline,
                                "original Open observation deadline expired before final readback")
                    try requireSameProcess(process, options: options)
                    try requireExpectedGeneration(process, options: options)
                    let finalWindows = try windows(app)
                    var finalBlockers: [AXUIElement] = []
                    for window in finalWindows {
                        if try isBlockingWindowStrict(window) {
                            finalBlockerObserved = true
                            finalBlockers.append(window)
                        }
                    }
                    try require(finalBlockers.isEmpty, "blocking window appeared before final destination acceptance")
                    let (finalMenu, finalMenuSignature, finalSelectedMenus) = try exactFileMenuSnapshot(app)
                    try require(CFEqual(finalMenu, lastMenu) && finalSelectedMenus == 0 &&
                                finalMenuSignature["selected"] as? Bool == false &&
                                sameFileMenuIdentity(lastMenuSignature, finalMenuSignature),
                                "File menu was not closed at final destination acceptance")
                    var finalDestinationWindows: [AXUIElement] = []
                    for window in finalWindows {
                        guard try role(window) == (kAXWindowRole as String) else { continue }
                        guard let raw = try optionalAXThrowing(window, kAXDocumentAttribute as String) as? String else { continue }
                        if try sameSetURL(raw, expected: destination) { finalDestinationWindows.append(window) }
                    }
                    try require(finalDestinationWindows.count == 1,
                                "exact destination uniqueness changed before final acceptance")
                    let finalDestinationFocused = try focusedWindowIsThrowing(app, finalDestinationWindows[0])
                    try require(finalDestinationFocused,
                                "exact destination focus changed before final acceptance")
                    let finalSourcePin = try setPin(source), finalDestinationPin = try setPin(destination)
                    try require(samePin(finalSourcePin, sourcePin) &&
                                sameOpenedDestination(destinationPin, finalDestinationPin),
                                "source/destination pin changed before final acceptance")
                    let finalTransport = try saveCopyTransportStrict(finalDestinationWindows[0])
                    try require(finalTransport.values.allSatisfy { $0 == 0 },
                                "stopped transport readback failed before final acceptance")
                    let finalEvidence = PendingOpenSuccessEvidence(
                        withinDeadline: ProcessInfo.processInfo.systemUptime < deadline,
                        exactProcess: !process.isTerminated && process.bundleIdentifier == options.bundleID,
                        expectedGeneration: processGenerationValue(process.launchDate ?? .distantPast) == generation,
                        exactDestinationFocused: finalDestinationFocused,
                        destinationPinStable: sameOpenedDestination(destinationPin, finalDestinationPin),
                        sourcePinUnchanged: samePin(sourcePin, finalSourcePin),
                        sourceAndDestinationTransportStopped: sourceTransport.values.allSatisfy { $0 == 0 } &&
                            finalTransport.values.allSatisfy { $0 == 0 },
                        noBlockingWindows: finalBlockers.isEmpty,
                        fileMenuClosed: finalSelectedMenus == 0)
                    try require(pendingOpenSuccessAllows(finalEvidence),
                                "final original Open acceptance evidence is incomplete")
                    try requireSameProcess(process, options: options)
                    try requireExpectedGeneration(process, options: options)
                    try require(finalTransport.values.allSatisfy { $0 == 0 } &&
                                ProcessInfo.processInfo.systemUptime < deadline,
                                "deadline or stopped readback expired before success receipt")
                    var report = fileMenuCancelReceiptBase(options)
                    report["status"] = "original_open_observed"
                    report["phase"] = "observe-original-pending-open"
                    report["read_only"] = false
                    report["same_process"] = true
                    report["source_file_after"] = finalSourcePin
                    report["destination_file_before"] = destinationPin
                    report["destination_file_after"] = finalDestinationPin
                    report["destination_metadata_delta"] = samePin(destinationPin, finalDestinationPin) ? [] : ["ctime"]
                    report["transport_before"] = sourceTransport
                    report["transport_after"] = finalTransport
                    report["stable_destination_observations"] = stable
                    report["file_menu_closed"] = true
                    report["actions"] = ["focus": "verified", "file_menu_cancel": "AXCancel_succeeded", "open": "exact_destination_observed"]
                    report["manual_inspection_required"] = false
                    return report
                } catch {
                    guard pendingOpenBlockerScanAllowsWait(PendingOpenBlockerScanFailureEvidence(
                        transientUnavailable: isTransientPostCancelAXReadFailure(error),
                        blockerAlreadyObserved: finalBlockerObserved)) else { throw error }
                    stable = 0
                    try require(pendingOpenPostCancelWaitAllows(PendingOpenPostCancelWaitEvidence(
                        withinDeadline: ProcessInfo.processInfo.systemUptime < deadline,
                        exactProcess: !process.isTerminated,
                        expectedGeneration: processGenerationValue(process.launchDate ?? .distantPast) == generation,
                        noObservedBlockers: true, readState: .transientUnavailable)),
                        "transient final AX read exceeded the safe wait boundary")
                    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
                    continue
                }
            }
        } else {
            stable = 0
            var sourceMatches: [AXUIElement] = []
            do {
                for window in currentWindows {
                    guard try role(window) == (kAXWindowRole as String) else { continue }
                    guard let raw = try optionalAXThrowing(window, kAXDocumentAttribute as String) as? String else { continue }
                    if try sameSetURL(raw, expected: source) { sourceMatches.append(window) }
                }
            } catch {
                guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                           deadline: deadline, noObservedBlockers: blockers.isEmpty) else { throw error }
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
                continue
            }
            try require(sourceMatches.count == 1, "source window lost while pending Open remains unresolved")
            let currentSourceWindow = sourceMatches[0]
            let sourceFocused: Bool
            do {
                sourceFocused = try focusedWindowIsThrowing(app, currentSourceWindow)
            } catch {
                guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                           deadline: deadline, noObservedBlockers: blockers.isEmpty) else { throw error }
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
                continue
            }
            try require(sourceFocused, "source window lost focus while pending Open remains unresolved")
            let sourceTransportNow: [String: Int]
            do {
                sourceTransportNow = try saveCopyTransportStrict(currentSourceWindow)
            } catch {
                guard canWaitAfterTransientPostCancelRead(error, process: process, generation: generation,
                                                           deadline: deadline, noObservedBlockers: blockers.isEmpty) else { throw error }
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
                continue
            }
            let waitEvidence = PendingOpenWaitEvidence(
                exactProcess: !process.isTerminated, expectedGeneration: processGenerationValue(process.launchDate ?? .distantPast) == generation,
                noBlockingWindows: blockers.isEmpty, onlyOwnedSourceWindow: currentWindows.count == 1 && CFEqual(currentWindows[0], currentSourceWindow),
                sourcePinMatches: samePin(try setPin(source), sourcePin),
                sourceTransportStopped: sourceTransportNow.values.allSatisfy { $0 == 0 }, fileMenuClosed: selectedMenus == 0)
            try require(pendingOpenWaitAllows(waitEvidence), "unsafe state while waiting for original Open")
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }
    throw LifecycleFailure(phase: phase, message: "original pending Open outcome unknown; exact destination was not stable before timeout; inspect and never retry")
}

func requireSameProcess(_ process: NSRunningApplication, options: Options) throws {
    try require(!process.isTerminated, "original Live process terminated; same-process open stopped")
    let running = NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == options.bundleID }
    try require(running.count == 1 && running[0].processIdentifier == options.pid &&
                running[0].bundleURL == process.bundleURL, "Live process/bundle changed or became ambiguous")
    try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == options.pid, "exact Live PID lost foreground")
}

func processGenerationValue(_ launchDate: Date) -> Int64? {
    let value = launchDate.timeIntervalSince1970 * 1_000_000
    guard value.isFinite, value > 0, value < Double(Int64.max) else { return nil }
    return Int64(value.rounded(.towardZero))
}

func requireExpectedGeneration(_ process: NSRunningApplication, options: Options) throws {
    guard let launchDate = process.launchDate,
          let generation = processGenerationValue(launchDate) else {
        throw LifecycleFailure(phase: phase, message: "Live process generation is unavailable")
    }
    try require(generation == options.expectedGeneration,
                "Live process generation differs from the reviewed inspection")
}

func runReconcilePendingOpen(_ options: Options) throws -> [String: Any] {
    phase = "reconcile-preflight"
    initialPID = Int(options.pid); initialBundleID = options.bundleID
    let deadline = ProcessInfo.processInfo.systemUptime + 180
    try require(AXIsProcessTrusted(), "Accessibility permission is not already trusted")
    guard let process = NSRunningApplication(processIdentifier: options.pid) else {
        throw LifecycleFailure(phase: phase, message: "exact target Live PID is unavailable")
    }
    try require(process.bundleIdentifier == options.bundleID, "exact PID bundle identifier mismatch")
    try requireExpectedGeneration(process, options: options)
    let running = NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == options.bundleID }
    try require(running.count == 1 && running[0].processIdentifier == options.pid,
                "ambiguous running Live applications")
    let source = try canonicalURL(options.sourceSet)
    let destination = try canonicalURL(options.destinationSet!)
    let sourcePin = try setPin(source), destinationPin = try setPin(destination)
    try require(source != destination &&
                !(sourcePin["device"] as? String == destinationPin["device"] as? String &&
                  sourcePin["inode"] as? String == destinationPin["inode"] as? String),
                "source/destination aliases are forbidden")
    try require(sourcePin["sha256"] as? String == options.sourceSHA256 &&
                destinationPin["sha256"] as? String == options.destinationSHA256,
                "reviewed source/destination SHA-256 mismatch")
    let appURL = URL(fileURLWithPath: options.appPath!)
    try require(appURL.path == options.appPath && appURL.resolvingSymlinksInPath().path == appURL.path &&
                appURL.pathExtension == "app" && process.bundleURL?.path == appURL.path &&
                Bundle(url: appURL)?.bundleIdentifier == options.bundleID,
                "exact Live app differs from running target")
    let app = AXUIElementCreateApplication(options.pid)
    let sourceWindow = try exactDocumentWindow(app, url: source)
    let expectedText = "Save changes to \"\(source.deletingPathExtension().lastPathComponent)\" before closing?"
    let (dialog, _) = try exactSaveChangesDialog(app, expectedText: expectedText)
    try require(focusedWindowIs(app, dialog), "exact Save Changes dialog is not the focused Live window")
    try requireSameProcess(process, options: options)
    let transport = try saveCopyTransport(sourceWindow)
    if !options.execute {
        return [
            "format": "codex-devtools-live-set-open-v1", "status": "ready", "read_only": true,
            "target": ["pid": options.pid, "bundle_identifier": options.bundleID],
            "source_set_url": source.absoluteString, "destination_set_url": destination.absoluteString,
            "source_file": sourcePin, "destination_file": destinationPin,
            "save_owned_source": true, "reconcile_pending_open": true,
            "expected_generation": options.expectedGeneration!,
            "dialog_text": expectedText, "transport": transport,
            "pending_destination_binding_pre_action": "unverified",
            "open_initiated_by_helper": false,
            "actions": openActions(readOnly: true)
        ]
    }

    phase = "reconcile-backup"
    let backupPath = try checkedSourceBackup(options.backupReceipt!, source: source,
                                             destination: destination, options: options)
    let sourceAttributes = try fileIdentity(source)
    try require((sourceAttributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
                "explicit source Save requires current-user ownership")
    let beforeSaveSource = try setPin(source), beforeSaveDestination = try setPin(destination)
    try require(samePin(beforeSaveSource, sourcePin) && samePin(beforeSaveDestination, destinationPin),
                "source/destination changed while checking backup")

    phase = "reconcile-before-save"
    try requireExpectedGeneration(process, options: options)
    try requireSameProcess(process, options: options)
    let freshSourceWindow = try exactDocumentWindow(app, url: source)
    let freshTransport = try saveCopyTransport(freshSourceWindow)
    try require(freshTransport == transport, "source transport state changed before Save")
    let (freshDialog, saveButton) = try exactSaveChangesDialog(app, expectedText: expectedText)
    try require(CFEqual(dialog, freshDialog) && focusedWindowIs(app, freshDialog),
                "exact Save Changes window changed before Save")
    let finalSource = try setPin(source), finalDestination = try setPin(destination)
    try require(samePin(finalSource, sourcePin) && samePin(finalDestination, destinationPin),
                "source/destination changed immediately before Save")
    try require(ProcessInfo.processInfo.systemUptime < deadline, "reconciliation deadline expired before Save")
    try requireExpectedGeneration(process, options: options)
    try requireSameProcess(process, options: options)

    phase = "save-owned-source-dialog"
    focusVerified = true
    saveAttempted = true
    try press(saveButton)
    openAttempted = true // Records the original pending Open outcome, not a helper-issued Open.

    phase = "observe-original-pending-open"
    let verifyDeadline = min(deadline, ProcessInfo.processInfo.systemUptime + 90)
    var previousSource: [String: Any]?
    var previousDestination: [String: Any]?
    var stable = 0
    while ProcessInfo.processInfo.systemUptime < verifyDeadline {
        try requireExpectedGeneration(process, options: options)
        try requireSameProcess(process, options: options)
        let currentWindows = try windows(app)
        let blockers = currentWindows.filter { isReconciliationBlockingWindow($0) }
        if !blockers.isEmpty {
            try require(canWaitForReconciliationDialog(
                blockerCount: blockers.count,
                exactSameDialog: blockers.count == 1 && CFEqual(blockers[0], dialog)
            ),
                        "unexpected dialog after Save; original pending Open outcome is unknown")
            let (stillOpenDialog, _) = try exactSaveChangesDialog(app, expectedText: expectedText)
            try require(CFEqual(stillOpenDialog, dialog) && focusedWindowIs(app, stillOpenDialog),
                        "Save Changes dialog changed after Save; outcome is unknown")
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
            continue
        }
        let matches = try currentWindows.filter { window in
            guard let raw = optionalAX(window, kAXDocumentAttribute as String) as? String else { return false }
            return try sameSetURL(raw, expected: destination)
        }
        try require(matches.count <= 1, "ambiguous destination Set URL after Save")
        if let destinationWindow = matches.first,
           focusedWindowIs(app, destinationWindow) {
            let savedSource = try setPin(source)
            let openedDestination = try setPin(destination)
            try require(!samePin(savedSource, sourcePin),
                        "Save Changes closed without a positive source-file transition")
            try require(sameOpenedDestination(destinationPin, openedDestination),
                        "original pending Open reached a changed destination file")
            let finalWindow = try exactDocumentWindow(app, url: destination)
            try require(focusedWindowIs(app, finalWindow),
                        "pinned destination lost focus during reconciliation")
            _ = try saveCopyTransport(finalWindow)
            if let priorSource = previousSource, let priorDestination = previousDestination,
               samePin(priorSource, savedSource), samePin(priorDestination, openedDestination) {
                stable += 1
            } else {
                stable = 1
            }
            previousSource = savedSource
            previousDestination = openedDestination
            if stable >= 3 {
                sourceSaveVerified = true
                openSourcePin = savedSource
                return [
                    "format": "codex-devtools-live-set-open-v1", "status": "reconciled", "read_only": false,
                    "target": ["pid": options.pid, "bundle_identifier": options.bundleID],
                    "same_process": true, "source_set_url": source.absoluteString,
                    "destination_set_url": destination.absoluteString,
                    "source_file": savedSource, "source_file_before_save": sourcePin,
                    "destination_file": destinationPin, "destination_file_after_open": openedDestination,
                    "destination_metadata_delta": samePin(destinationPin, openedDestination) ? [] : ["ctime"],
                    "save_owned_source": true, "reconcile_pending_open": true,
                    "reconciled_pending_open": true, "expected_generation": options.expectedGeneration!,
                    "dialog_text": expectedText,
                    "pending_destination_binding_pre_action": "unverified",
                    "open_initiated_by_helper": false,
                    "backup_receipt": options.backupReceipt!,
                    "backup_file": backupPath,
                    "transport_before_save": transport,
                    "transport_after_open": try saveCopyTransport(finalWindow),
                    "actions": openActions(readOnly: false),
                    "evidence_limit": "exact dialog Save, source file transition and stable same-process destination AX URL/file pins; the dialog does not expose the pending destination before Save, so the pending request is not pre-action-bound by this helper"
                ]
            }
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }
    throw LifecycleFailure(phase: phase,
                           message: "original pending Open did not resolve to the exact pinned destination within the deadline; Save outcome is unknown and must not be retried")
}

func checkedSourceBackup(_ receiptPath: String, source: URL, destination: URL, options: Options) throws -> String {
    let path = URL(fileURLWithPath: try lexicalPath(receiptPath))
    try ensureNoSymlinkChain(path, mustExist: true, outsideGit: true)
    let attributes = try fileIdentity(path)
    try require((attributes[.size] as? NSNumber)?.intValue ?? 65537 <= 65536, "backup receipt exceeds bounds")
    try require(((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0o777) & 0o077 == 0 &&
                (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(), "backup receipt must be private and owned")
    guard let receipt = try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as? [String: Any],
          let original = receipt["source"] as? [String: Any], let backup = receipt["backup"] as? [String: Any],
          let backupPath = backup["path"] as? String else {
        throw LifecycleFailure(phase: phase, message: "invalid already-written source backup receipt")
    }
    try require(receipt["format"] as? String == "codex-devtools-live-set-backup-v1" &&
                original["path"] as? String == source.path && original["sha256"] as? String == options.sourceSHA256 &&
                receipt["destination_set_url"] as? String == destination.absoluteString &&
                receipt["destination_sha256"] as? String == options.destinationSHA256 &&
                receipt["pid"] as? Int == Int(options.pid) && receipt["bundle_identifier"] as? String == options.bundleID,
                "source backup receipt does not match exact source/destination/process approval")
    let backupURL = try canonicalURL(backupPath), pin = try setPin(backupURL)
    let backupAttributes = try fileIdentity(backupURL)
    try require(pin["sha256"] as? String == options.sourceSHA256 && backup["sha256"] as? String == options.sourceSHA256 &&
                samePin(pin, backup) &&
                ((backupAttributes[.posixPermissions] as? NSNumber)?.intValue ?? 0o777) & 0o077 == 0 &&
                (backupAttributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(), "source backup bytes/hash or permissions mismatch")
    return backupPath
}

func waitForOwnedSourceSaved(app: AXUIElement, process: NSRunningApplication, options: Options,
                              source: URL, before: [String: Any], deadline: TimeInterval) throws -> [String: Any] {
    var previous: [String: Any]?, stable = 0
    while ProcessInfo.processInfo.systemUptime < deadline {
        try requireSameProcess(process, options: options)
        try ensureNoDialogs(app)
        let window = try exactDocumentWindow(app, url: source)
        try require(focusedWindowIs(app, window), "owned source window lost focus during Save")
        do {
            let pin = try setPin(source)
            if !samePin(pin, before) {
                stable = previous.map { samePin($0, pin) } == true ? stable + 1 : 0
                previous = pin
                if stable >= 2 && ProcessInfo.processInfo.systemUptime < deadline { return pin }
            } else { previous = nil; stable = 0 }
        } catch is SetObservationChanged { previous = nil; stable = 0 }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }
    throw LifecycleFailure(phase: phase, message: "Save outcome unknown: no positive file transition followed by stable bytes/identity/URL; unchanged/no-op Save is not inferred complete")
}

func runOpenExisting(_ options: Options) throws -> [String: Any] {
    phase = "preflight"
    initialPID = Int(options.pid); initialBundleID = options.bundleID
    let deadline = ProcessInfo.processInfo.systemUptime + 180
    try require(AXIsProcessTrusted(), "Accessibility permission is not already trusted")
    guard let process = NSRunningApplication(processIdentifier: options.pid) else {
        throw LifecycleFailure(phase: phase, message: "exact target Live PID is unavailable")
    }
    try require(process.bundleIdentifier == options.bundleID, "exact PID bundle identifier mismatch")
    let running = NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == options.bundleID }
    try require(running.count == 1 && running[0].processIdentifier == options.pid, "ambiguous running Live applications")
    let source = try canonicalURL(options.sourceSet), destination = try canonicalURL(options.destinationSet!)
    let sourcePin = try setPin(source), destinationPin = try setPin(destination)
    try require(source != destination && !(sourcePin["device"] as? String == destinationPin["device"] as? String && sourcePin["inode"] as? String == destinationPin["inode"] as? String), "source/destination aliases are forbidden")
    try require(sourcePin["sha256"] as? String == options.sourceSHA256 && destinationPin["sha256"] as? String == options.destinationSHA256,
                "reviewed source/destination SHA-256 mismatch")
    let appURL = URL(fileURLWithPath: options.appPath!)
    try require(appURL.path == options.appPath && appURL.resolvingSymlinksInPath().path == appURL.path && appURL.pathExtension == "app" &&
                process.bundleURL?.path == appURL.path && Bundle(url: appURL)?.bundleIdentifier == options.bundleID, "exact Live app differs from running target")
    let app = AXUIElementCreateApplication(options.pid)
    try ensureNoDialogs(app)
    let sourceWindow = try exactDocumentWindow(app, url: source)
    if !options.execute {
        return ["format": "codex-devtools-live-set-open-v1", "status": "ready", "read_only": true,
                "target": ["pid": options.pid, "bundle_identifier": options.bundleID],
                "source_set_url": source.absoluteString, "destination_set_url": destination.absoluteString,
                "source_file": sourcePin, "destination_file": destinationPin, "save_owned_source": options.saveOwnedSource,
                "actions": openActions(readOnly: true)]
    }
    var expectedSource = sourcePin
    var backupPath: String?
    if options.saveOwnedSource {
        backupPath = try checkedSourceBackup(options.backupReceipt!, source: source, destination: destination, options: options)
    }
    try activateExactTarget(process, app: app, sourceWindow: sourceWindow)
    try requireSameProcess(process, options: options)
    if options.saveOwnedSource {
        let ownedSourceAttributes = try fileIdentity(source)
        try require((ownedSourceAttributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(), "explicit source Save requires current-user ownership")
        phase = "source-save-menu"
        let item = try findMenuItem(app: app, pid: options.pid, sourceWindow: sourceWindow, menuTitle: "File", itemTitle: "Save Live Set")
        try requireFreshMenuFocus(app: app, pid: options.pid, sourceWindow: sourceWindow, menu: activeFileMenu, item: item)
        let beforeSaveSource = try setPin(source), beforeSaveDestination = try setPin(destination)
        try require(samePin(beforeSaveSource, sourcePin) && samePin(beforeSaveDestination, destinationPin), "source/destination changed before Save")
        _ = try checkedSourceBackup(options.backupReceipt!, source: source, destination: destination, options: options)
        let finalSaveSource = try setPin(source), finalSaveDestination = try setPin(destination)
        try require(samePin(finalSaveSource, sourcePin) && samePin(finalSaveDestination, destinationPin), "Set identity/hash changed while verifying backup")
        try require(ProcessInfo.processInfo.systemUptime < deadline, "same-process operation deadline exceeded before Save")
        try requireSameProcess(process, options: options)
        try requireFreshMenuFocus(app: app, pid: options.pid, sourceWindow: sourceWindow, menu: activeFileMenu, item: item)
        _ = try exactDocumentWindow(app, url: source)
        phase = "save-owned-source"; saveAttempted = true
        try press(item); menuDiagnostics = nil
        phase = "verify-owned-source-save"
        expectedSource = try waitForOwnedSourceSaved(app: app, process: process, options: options, source: source,
                                                    before: sourcePin, deadline: min(deadline, ProcessInfo.processInfo.systemUptime + 30))
        sourceSaveVerified = true; openSourcePin = expectedSource
    }
    phase = "open-existing-preflight"
    try requireSameProcess(process, options: options)
    try ensureNoDialogs(app)
    let currentSource = try exactDocumentWindow(app, url: source)
    try require(focusedWindowIs(app, currentSource), "owned source window lost focus before open")
    let beforeOpenSource = try setPin(source), beforeOpenDestination = try setPin(destination)
    try require(samePin(beforeOpenSource, expectedSource) && samePin(beforeOpenDestination, destinationPin), "source/destination identity/hash changed before open")
    try requireSameProcess(process, options: options)
    try ensureNoDialogs(app)
    let finalSourceWindow = try exactDocumentWindow(app, url: source)
    try require(focusedWindowIs(app, finalSourceWindow), "owned source focus changed during file verification")
    try require(ProcessInfo.processInfo.systemUptime < deadline, "same-process operation deadline exceeded before open")
    phase = "open-existing"
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.createsNewApplicationInstance = false
    var opened: NSRunningApplication?, openError: Error?
    let semaphore = DispatchSemaphore(value: 0)
    openAttempted = true
    NSWorkspace.shared.open([destination], withApplicationAt: appURL, configuration: configuration) { application, error in
        opened = application; openError = error; semaphore.signal()
    }
    if semaphore.wait(timeout: .now() + min(45, max(0, deadline - ProcessInfo.processInfo.systemUptime))) == .timedOut {
        throw LifecycleFailure(phase: phase, message: "existing-Set open outcome unknown: callback timed out")
    }
    if let openError { throw LifecycleFailure(phase: phase, message: "existing-Set open failed: \(openError.localizedDescription)") }
    try require(opened?.processIdentifier == options.pid && opened?.bundleIdentifier == options.bundleID && !process.isTerminated,
                "existing-Set open returned a different process; stopped without cleanup")
    phase = "verify-opened-existing-set"
    let verifyDeadline = min(deadline, ProcessInfo.processInfo.systemUptime + 75)
    while ProcessInfo.processInfo.systemUptime < verifyDeadline {
        try requireSameProcess(process, options: options)
        if let currentWindows = try? windows(app) {
            try require(!currentWindows.contains { isBlockingWindow($0) }, "unexpected prompt/dialog during Set switch; no interaction attempted")
            let matches = try currentWindows.filter { window in
                guard let raw = optionalAX(window, kAXDocumentAttribute as String) as? String else { return false }
                return try sameSetURL(raw, expected: destination)
            }
            try require(matches.count <= 1, "ambiguous destination Set URL")
            if let window = matches.first {
                try require(focusedWindowIs(app, window), "exact destination Set is not focused")
                let openedDestination = try setPin(destination), retainedSource = try setPin(source)
                try require(sameOpenedDestination(destinationPin, openedDestination) && samePin(retainedSource, expectedSource), "Set bytes/identity changed during open")
                try requireSameProcess(process, options: options)
                try ensureNoDialogs(app)
                let finalDestinationWindow = try exactDocumentWindow(app, url: destination)
                try require(focusedWindowIs(app, finalDestinationWindow), "destination focus changed during file verification")
                try require(ProcessInfo.processInfo.systemUptime < verifyDeadline, "Set verification deadline expired")
                return ["format": "codex-devtools-live-set-open-v1", "status": "opened", "read_only": false,
                        "target": ["pid": options.pid, "bundle_identifier": options.bundleID], "same_process": true,
                        "source_set_url": source.absoluteString, "destination_set_url": destination.absoluteString,
                        "source_file": expectedSource, "destination_file": destinationPin,
                        "destination_file_after_open": openedDestination,
                        "destination_metadata_delta": samePin(destinationPin, openedDestination) ? [] : ["ctime"],
                        "save_owned_source": options.saveOwnedSource,
                        "backup_receipt": options.backupReceipt as Any? ?? NSNull(), "backup_file": backupPath as Any? ?? NSNull(),
                        "actions": openActions(readOnly: false),
                        "evidence_limit": "same-process AX document URL plus exact file bytes/identity; SDK/LOM handles must be invalidated, and M4L path/state/silence require independent proof"]
            }
        }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }
    throw LifecycleFailure(phase: phase, message: "existing-Set open outcome unknown: exact destination AX URL was not observed")
}

func openActions(readOnly: Bool) -> [String: String] {
    if readOnly {
        return ["focus": "not_attempted", "menu_navigation": "not_attempted", "save_as_menu": "not_attempted", "save": "not_attempted", "quit": "not_attempted", "open": "not_attempted"]
    }
    return ["focus": focusVerified ? "verified" : focusAttempted ? "outcome_unknown" : "not_attempted",
            "menu_navigation": menuNavigationVerified ? "verified" : menuNavigationAttempted ? "outcome_unknown" : "not_attempted",
            "save_as_menu": "not_attempted", "save": sourceSaveVerified ? "source_observed" : saveAttempted ? "outcome_unknown" : "not_attempted",
            "quit": "not_attempted", "open": "exact_set_window_observed"]
}

func stoppedOpen(_ options: Options, error: String) -> [String: Any] {
    var actions = openActions(readOnly: false)
    actions["open"] = openAttempted ? "outcome_unknown" : "not_attempted"
    var report: [String: Any] = ["format": "codex-devtools-live-set-open-v1", "status": "stopped", "read_only": !options.execute,
        "target": ["pid": options.pid, "bundle_identifier": options.bundleID], "phase": phase, "error": error,
        "actions": actions, "manual_inspection_required": uiActionAttempted || focusAttempted || saveAttempted || openAttempted,
        "backup_receipt": options.backupReceipt as Any? ?? NSNull()]
    if options.reconcilePendingOpen {
        report["reconcile_pending_open"] = true
        report["expected_generation"] = options.expectedGeneration as Any? ?? NSNull()
        report["pending_destination_binding_pre_action"] = "unverified"
        report["open_initiated_by_helper"] = false
    }
    if let openSourcePin { report["saved_source_file"] = openSourcePin }
    if let menuDiagnostics { report["diagnostics"] = menuDiagnostics }
    return report
}

let pausedLifecycleFormat = "codex-devtools-live-set-paused-v1"
let resumedLifecycleFormat = "codex-devtools-live-set-resume-v1"
let pausedLifecycleLimit = "saved file bytes and exact PID exit only; MIDI settings, M4L recall, wire and hardware require independent verification"
let lifecycleNoActions = ["focus": "not_attempted", "menu_navigation": "not_attempted", "save_as_menu": "not_attempted", "save": "not_attempted", "quit": "not_attempted", "open": "not_attempted"]
let pausedLifecycleActions = ["focus": "verified", "menu_navigation": "verified", "save_as_menu": "verified_panel", "save": "destination_observed", "quit": "terminated", "open": "not_attempted"]

func lifecycleAppPin(_ path: String, bundleID: String) throws -> [String: Any] {
    let appURL = URL(fileURLWithPath: try lexicalPath(path))
    let infoPin = try setPin(appURL.appendingPathComponent("Contents/Info.plist"), maxBytes: 1024 * 1024)
    guard appURL.pathExtension == "app", let bundle = Bundle(url: appURL),
          bundle.bundleIdentifier == bundleID,
          let name = bundle.object(forInfoDictionaryKey: "CFBundleExecutable") as? String,
          !name.isEmpty, name != ".", name != "..", !name.contains("/"), !name.contains("\\") else {
        throw LifecycleFailure(phase: phase, message: "exact app bundle identity unavailable")
    }
    // Derive the canonical executable from the pinned metadata. Bundle.executableURL
    // may introduce macOS /var aliases even for a caller-selected /private/var path.
    let executable = appURL.appendingPathComponent("Contents/MacOS").appendingPathComponent(name)
    let executablePin = try setPin(executable, maxBytes: 512 * 1024 * 1024)
    let finalInfo = try setPin(appURL.appendingPathComponent("Contents/Info.plist"), maxBytes: 1024 * 1024)
    try require(samePin(infoPin, finalInfo), "app metadata changed while pinning executable")
    return ["path": appURL.path, "bundle_identifier": bundleID,
            "info_file": infoPin, "executable_file": executablePin]
}

func validLifecycleFilePin(_ pin: [String: Any]) -> Bool {
    let numbers = ["bytes", "mtime_seconds", "mtime_nanoseconds", "ctime_seconds", "ctime_nanoseconds"]
    guard Set(pin.keys) == Set(numbers + ["path", "sha256", "device", "inode"]),
          pin["path"] is String, let digest = pin["sha256"] as? String,
          digest.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { return false }
    for key in numbers {
        guard let value = pin[key] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
              value.doubleValue == Double(value.int64Value) else { return false }
    }
    guard (pin["bytes"] as! NSNumber).int64Value > 0 else { return false }
    for key in ["mtime_nanoseconds", "ctime_nanoseconds"] {
        let value = (pin[key] as! NSNumber).int64Value
        if value < 0 || value >= 1_000_000_000 { return false }
    }
    for key in ["device", "inode"] {
        guard let value = pin[key] as? String, value.range(of: "^[0-9]+$", options: .regularExpression) != nil else { return false }
    }
    return true
}

func boundedLifecycleReceiptData(_ url: URL) throws -> Data {
    let fd = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW)
    try require(fd >= 0, "cannot read paused receipt without following symlinks")
    defer { Darwin.close(fd) }
    var data = Data(), buffer = [UInt8](repeating: 0, count: 4096)
    while true {
        let size = buffer.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
        try require(size >= 0, "paused receipt read failed")
        if size == 0 { return data }
        try require(data.count + size <= 65536, "paused receipt exceeded read bound")
        data.append(contentsOf: buffer.prefix(size))
    }
}

func lifecycleReceipt(_ options: Options) throws -> [String: Any] {
    guard let path = options.resumeReceipt, let expected = options.receiptSHA256,
          expected.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else {
        throw LifecycleFailure(phase: phase, message: "exact paused receipt and SHA-256 required")
    }
    let url = URL(fileURLWithPath: try lexicalPath(path))
    let pin = try setPin(url, maxBytes: 65536)
    try require(pin["sha256"] as? String == expected, "paused receipt SHA-256 mismatch")
    let data = try boundedLifecycleReceiptData(url)
    let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    let afterRead = try setPin(url, maxBytes: 65536)
    try require(data.count <= 65536 && digest == expected && samePin(pin, afterRead), "paused receipt changed while reading")
    guard let receipt = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          Set(receipt.keys) == Set(["format", "mode", "status", "target", "source_set_url", "destination_set_url", "saved_set", "app", "read_only", "actions", "evidence_limit", "paused_at_utc"]),
          receipt["format"] as? String == pausedLifecycleFormat, receipt["mode"] as? String == "stop-before-reopen",
          receipt["status"] as? String == "saved-and-quit", let readOnly = receipt["read_only"] as? NSNumber,
          CFGetTypeID(readOnly) == CFBooleanGetTypeID(), readOnly.boolValue == false,
          let pausedAtRaw = receipt["paused_at_utc"] as? String, pausedAtRaw.count <= 40,
          ISO8601DateFormatter().date(from: pausedAtRaw) != nil,
          receipt["evidence_limit"] as? String == pausedLifecycleLimit,
          let actions = receipt["actions"] as? [String: String], actions == pausedLifecycleActions,
          let target = receipt["target"] as? [String: Any], Set(target.keys) == Set(["pid", "bundle_identifier"]),
          let pidNumber = target["pid"] as? NSNumber, CFGetTypeID(pidNumber) != CFBooleanGetTypeID(),
          pidNumber.doubleValue == Double(pidNumber.int32Value), pidNumber.int32Value > 0,
          let bundleID = target["bundle_identifier"] as? String, !bundleID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
          let saved = receipt["saved_set"] as? [String: Any], validLifecycleFilePin(saved), let savedPath = saved["path"] as? String,
          let app = receipt["app"] as? [String: Any], Set(app.keys) == Set(["path", "bundle_identifier", "info_file", "executable_file"]),
          let infoPin = app["info_file"] as? [String: Any], validLifecycleFilePin(infoPin),
          let executablePin = app["executable_file"] as? [String: Any], validLifecycleFilePin(executablePin),
          let appPath = app["path"] as? String,
          let sourceRaw = receipt["source_set_url"] as? String, let source = URL(string: sourceRaw), source.isFileURL,
          let destinationRaw = receipt["destination_set_url"] as? String else {
        throw LifecycleFailure(phase: phase, message: "invalid closed saved-and-quit receipt")
    }
    let destination = try canonicalURL(savedPath), canonicalSource = try canonicalURL(source.path)
    try require(sourceRaw == canonicalSource.absoluteString && destinationRaw == destination.absoluteString &&
                destination != canonicalSource && destination.deletingLastPathComponent() == canonicalSource.deletingLastPathComponent(),
                "paused receipt Set URLs are not exact siblings")
    let currentSet = try setPin(destination), currentApp = try lifecycleAppPin(appPath, bundleID: bundleID)
    try require(samePin(saved, currentSet) && samePin(app, currentApp),
                "paused Set or app bytes/identity changed")
    return receipt
}

func requirePausedProcessAbsent(pid: pid_t, bundleID: String) throws {
    errno = 0
    let result = kill(pid, 0)
    try require(result == -1 && errno == ESRCH && NSRunningApplication(processIdentifier: pid) == nil,
                "old paused PID is present or its absence is uncertain")
    try require(NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == bundleID }.isEmpty,
                "a Live process is already running; resume stopped")
}

func runResumeLifecycle(_ options: Options) throws -> [String: Any] {
    phase = "resume-preflight"
    let receipt = try lifecycleReceipt(options)
    let target = receipt["target"] as! [String: Any], oldPID = pid_t((target["pid"] as! NSNumber).int32Value)
    let bundleID = target["bundle_identifier"] as! String
    initialPID = Int(oldPID); initialBundleID = bundleID
    try require(AXIsProcessTrusted(), "Accessibility permission is not already trusted")
    try requirePausedProcessAbsent(pid: oldPID, bundleID: bundleID)
    var report: [String: Any] = ["format": resumedLifecycleFormat, "mode": "resume", "status": "ready", "read_only": true,
        "target": target, "resumed_from": ["path": options.resumeReceipt!, "sha256": options.receiptSHA256!],
        "saved_set": receipt["saved_set"]!, "app": receipt["app"]!, "actions": lifecycleNoActions,
        "evidence_limit": pausedLifecycleLimit]
    if !options.execute { return report }
    phase = "resume-before-open"
    // Re-read the caller-pinned receipt, file and app immediately before the only effect.
    let current = try lifecycleReceipt(options)
    try require(samePin(receipt, current), "paused receipt changed before open")
    try requirePausedProcessAbsent(pid: oldPID, bundleID: bundleID)
    let destination = try canonicalURL((receipt["saved_set"] as! [String: Any])["path"] as! String)
    let appURL = URL(fileURLWithPath: (receipt["app"] as! [String: Any])["path"] as! String)
    let source = URL(string: receipt["source_set_url"] as! String)!
    let reopened = try reopenLifecycleSet(destination: destination, appURL: appURL, oldPID: oldPID,
                                         bundleID: bundleID, processName: "", source: source)
    let after = try setPin(destination), saved = receipt["saved_set"] as! [String: Any]
    try require(sameOpenedDestination(saved, after), "saved Set bytes/identity changed during resume")
    let afterApp = try lifecycleAppPin(appURL.path, bundleID: bundleID)
    try require(samePin(receipt["app"] as! [String: Any], afterApp), "app changed during resume")
    report["status"] = "reopened"; report["read_only"] = false
    var actions = lifecycleNoActions; actions["open"] = "exact_set_window_observed"
    report["actions"] = actions; report["reopened_target"] = reopened["reopened_target"]!
    return report
}

func stoppedResumeLifecycle(_ options: Options, error: String) -> [String: Any] {
    var actions = lifecycleNoActions; actions["open"] = openAttempted ? "outcome_unknown" : "not_attempted"
    return ["format": resumedLifecycleFormat, "mode": "resume", "status": "stopped", "read_only": !options.execute,
            "target": ["pid": initialPID ?? 0, "bundle_identifier": initialBundleID ?? ""],
            "resumed_from": ["path": options.resumeReceipt!, "sha256": options.receiptSHA256!],
            "phase": phase, "error": error, "actions": actions, "manual_inspection_required": openAttempted]
}

let options = parseOptions()
if options.resumeReceipt != nil {
    do {
        let report = try runResumeLifecycle(options)
        let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
        print(String(data: data, encoding: .utf8) ?? "{}")
    } catch {
        let message = (error as? LifecycleFailure)?.message ?? String(describing: error)
        let report = stoppedResumeLifecycle(options, error: message)
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) { print(String(data: data, encoding: .utf8) ?? "{}") }
        exit(1)
    }
    exit(0)
}
if options.openExisting {
    do {
        let report = try options.reconcilePendingOpen
            ? runReconcilePendingOpen(options)
            : (options.cancelSelectedFileMenu ? try runFileMenuCancelObserve(options) : try runOpenExisting(options))
        let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
        print(String(data: data, encoding: .utf8) ?? "{}")
    } catch let error as LifecycleFailure {
        let report = options.cancelSelectedFileMenu
            ? stoppedFileMenuCancel(options, error: error.message)
            : stoppedOpen(options, error: error.message)
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) { print(String(data: data, encoding: .utf8) ?? "{}") }
        exit(1)
    } catch {
        let message = "existing-Set operation unavailable or uncertain: " + String(describing: error)
        let report = options.cancelSelectedFileMenu
            ? stoppedFileMenuCancel(options, error: message)
            : stoppedOpen(options, error: message)
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) { print(String(data: data, encoding: .utf8) ?? "{}") }
        exit(1)
    }
    exit(0)
}
do {
    let report = try run(options)
    let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
    print(String(data: data, encoding: .utf8) ?? "{}")
} catch let error as LifecycleFailure {
    if options.saveCopyOnly {
        let report = stoppedSaveCopy(options, error: error.message)
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) { print(String(data: data, encoding: .utf8) ?? "{}") }
        exit(1)
    }
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
    if options.saveCopyOnly {
        let report = stoppedSaveCopy(options, error: String(describing: error))
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) { print(String(data: data, encoding: .utf8) ?? "{}") }
        exit(1)
    }
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
