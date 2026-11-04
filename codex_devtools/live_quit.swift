// Bounded normal termination for one exact, pre-saved Ableton Live Set.
import AppKit
import ApplicationServices
import CryptoKit
import Darwin
import Foundation

let receiptFormat = "codex-devtools-live-quit-v1"
let maxSetBytes: Int64 = 128 * 1024 * 1024
let nanosecondsPerSecond: Double = 1_000_000_000
let nanosecondsPerSecondInteger: UInt64 = 1_000_000_000
let maxWindows = 64
let maxElements = 1500
let maxDepth = 12

struct Options {
    var pid: pid_t = 0
    var bundleID = ""
    var appPath = ""
    var generation = ""
    var setPath = ""
    var sha256 = ""
    var device = ""
    var inode = ""
    var deadlineMonotonicNanoseconds: UInt64 = 0
    var execute = false
}

struct QuitFailure: Error {
    let phase: String
    let message: String
}

struct AXReadFailure: Error, CustomStringConvertible {
    let attribute: String
    let code: AXError

    var description: String {
        "AX read \(attribute) failed (\(code.rawValue))"
    }
}

struct TransportControls {
    let play: Int
    let link: Int
    let arrangementRecord: Int
    let sessionRecord: Int

    var receipt: [String: Int] {
        [
            "play": play,
            "link": link,
            "arrangement_record": arrangementRecord,
            "session_record": sessionRecord,
        ]
    }
}

var options = Options()
var phase = "arguments"
var quitRequested = false
var quitAccepted: Bool?
var didTerminate = false
var processGeneration: String?
var sourcePin: [String: Any]?
var transportState: TransportControls?

func require(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {
    if try !condition() { throw QuitFailure(phase: phase, message: message) }
}

func monotonicNow() throws -> UInt64 {
    // Keep the cross-process deadline in Python's CLOCK_MONOTONIC coordinate.
    var value = timespec()
    guard clock_gettime(CLOCK_MONOTONIC, &value) == 0,
          value.tv_sec >= 0, value.tv_nsec >= 0 else {
        throw QuitFailure(phase: phase, message: "system monotonic clock is unavailable")
    }
    return UInt64(value.tv_sec) * nanosecondsPerSecondInteger + UInt64(value.tv_nsec)
}

func requireBeforeDeadline(_ deadline: UInt64, _ message: String) throws -> UInt64 {
    let now = try monotonicNow()
    try require(now < deadline, message)
    return now
}

func parseOptions() throws -> Options {
    var result = Options()
    var index = 1
    let args = CommandLine.arguments
    while index < args.count {
        let key = args[index]
        if key == "--help" {
            print("Usage: live_quit.swift --pid PID --bundle-id com.ableton.live --app /Applications/Live.app --generation TOKEN --set /path/Source.als --sha256 HASH --device N --inode N --deadline-monotonic-ns N [--execute]")
            exit(0)
        }
        if key == "--execute" {
            result.execute = true
            index += 1
            continue
        }
        guard index + 1 < args.count else {
            throw QuitFailure(phase: "arguments", message: "missing value for \(key)")
        }
        let value = args[index + 1]
        switch key {
        case "--pid":
            guard let parsed = Int32(value), parsed > 0 else {
                throw QuitFailure(phase: "arguments", message: "PID must be positive")
            }
            result.pid = pid_t(parsed)
        case "--bundle-id": result.bundleID = value
        case "--app": result.appPath = value
        case "--generation": result.generation = value
        case "--set": result.setPath = value
        case "--sha256": result.sha256 = value
        case "--device": result.device = value
        case "--inode": result.inode = value
        case "--deadline-monotonic-ns":
            guard let parsed = UInt64(value), parsed > 0 else {
                throw QuitFailure(phase: "arguments", message: "absolute monotonic deadline must be positive")
            }
            result.deadlineMonotonicNanoseconds = parsed
        default:
            throw QuitFailure(phase: "arguments", message: "unknown option: \(key)")
        }
        index += 2
    }
    guard result.pid > 0, result.bundleID == "com.ableton.live",
          !result.appPath.isEmpty, !result.generation.isEmpty,
          !result.setPath.isEmpty, result.sha256.count == 64,
          !result.device.isEmpty, !result.inode.isEmpty,
          result.deadlineMonotonicNanoseconds > 0 else {
        throw QuitFailure(phase: "arguments", message: "exact process, app, generation and Set pins are required")
    }
    guard result.sha256.allSatisfy({ $0.isHexDigit && !$0.isUppercase }),
          Int64(result.device) != nil, Int64(result.inode) != nil else {
        throw QuitFailure(phase: "arguments", message: "invalid SHA-256, device or inode pin")
    }
    return result
}

func lexicalPath(_ path: String, suffix: String) throws -> String {
    let parts = path.split(separator: "/", omittingEmptySubsequences: false)
    guard path.hasPrefix("/"), !path.hasSuffix("/"), parts.count >= 2,
          parts[0].isEmpty,
          parts.dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }),
          !path.contains("\\"), !path.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains),
          path.lowercased().hasSuffix(suffix) else {
        throw QuitFailure(phase: phase, message: "normalized absolute \(suffix) path required")
    }
    return path
}

func verifyRealDirectories(_ path: String) throws {
    var current = URL(fileURLWithPath: "/")
    let parts = path.split(separator: "/").map(String.init)
    for part in parts {
        current.appendPathComponent(part, isDirectory: true)
        var info = stat()
        guard current.path.withCString({ lstat($0, &info) }) == 0,
              (info.st_mode & S_IFMT) == S_IFDIR else {
            throw QuitFailure(phase: phase, message: "path has an unavailable or symbolic-link directory: \(current.path)")
        }
    }
}

func processGeneration(_ process: NSRunningApplication) throws -> String {
    guard let launchDate = process.launchDate else {
        throw QuitFailure(phase: phase, message: "target process launch generation is unavailable")
    }
    let value = launchDate.timeIntervalSince1970 * 1_000_000
    guard value.isFinite, value > 0, value < Double(Int64.max) else {
        throw QuitFailure(phase: phase, message: "target process launch generation is invalid")
    }
    return String(Int64(value.rounded(.towardZero)))
}

func targetProcess() throws -> NSRunningApplication {
    guard let process = NSRunningApplication(processIdentifier: options.pid),
          !process.isTerminated,
          process.bundleIdentifier == options.bundleID,
          process.bundleURL?.path == options.appPath,
          process.executableURL?.path == options.appPath + "/Contents/MacOS/Live" else {
        throw QuitFailure(phase: phase, message: "exact PID, bundle, app or executable identity changed")
    }
    let generation = try processGeneration(process)
    processGeneration = generation
    try require(generation == options.generation, "exact Live process generation changed")
    return process
}

func sameTargetProcess(_ first: NSRunningApplication, _ second: NSRunningApplication) throws -> Bool {
    try require(!first.isTerminated && !second.isTerminated,
                "exact Live process terminated during final identity check")
    let firstGeneration = try processGeneration(first)
    let secondGeneration = try processGeneration(second)
    return first.processIdentifier == second.processIdentifier &&
        first.bundleIdentifier == second.bundleIdentifier &&
        first.bundleURL?.path == second.bundleURL?.path &&
        first.executableURL?.path == second.executableURL?.path &&
        firstGeneration == secondGeneration
}

func waitForTermination(_ process: NSRunningApplication, deadline: UInt64) throws -> Bool {
    while true {
        let beforeProbe = try monotonicNow()
        guard beforeProbe < deadline else { return false }
        let terminated = process.isTerminated
        let now = try monotonicNow()
        guard now < deadline else { return false }
        if terminated { return true }
        let remaining = deadline - now
        let sleepSeconds = min(0.1, Double(remaining) / nanosecondsPerSecond)
        guard sleepSeconds > 0 else { return false }
        RunLoop.current.run(until: Date().addingTimeInterval(sleepSeconds))
    }
}

func verifyAppBundle() throws {
    let path = try lexicalPath(options.appPath, suffix: ".app")
    try verifyRealDirectories(path)
    let appURL = URL(fileURLWithPath: path, isDirectory: true)
    try require(Bundle(url: appURL)?.bundleIdentifier == options.bundleID,
                "selected app bundle does not have the expected Live bundle identifier")
    let executable = appURL.appendingPathComponent("Contents/MacOS/Live").path
    var info = stat()
    try require(executable.withCString({ lstat($0, &info) }) == 0 && (info.st_mode & S_IFMT) == S_IFREG,
                "selected Live executable is not a regular file")
}

func setPin() throws -> [String: Any] {
    let path = try lexicalPath(options.setPath, suffix: ".als")
    let url = URL(fileURLWithPath: path)
    try verifyRealDirectories(url.deletingLastPathComponent().path)
    let fd = Darwin.open(path, O_RDONLY | O_NOFOLLOW)
    try require(fd >= 0, "cannot open exact Set without following symlinks")
    defer { Darwin.close(fd) }
    var before = stat(), after = stat(), current = stat()
    try require(fstat(fd, &before) == 0 && (before.st_mode & S_IFMT) == S_IFREG,
                "selected Set is not a regular file")
    try require(before.st_size > 0 && before.st_size <= maxSetBytes,
                "selected Set must contain 1 byte to 128 MiB")
    var digest = SHA256()
    var count: Int64 = 0
    var buffer = [UInt8](repeating: 0, count: 65536)
    while true {
        _ = try requireBeforeDeadline(options.deadlineMonotonicNanoseconds,
                                      "normal quit monotonic deadline expired during Set read")
        let amount = buffer.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
        try require(amount >= 0, "selected Set read failed")
        try require(monotonicNow() < options.deadlineMonotonicNanoseconds,
                    "normal quit monotonic deadline expired during Set read")
        if amount == 0 { break }
        count += Int64(amount)
        try require(count <= maxSetBytes, "selected Set exceeded the bounded read size")
        digest.update(data: Data(buffer.prefix(amount)))
    }
    try require(fstat(fd, &after) == 0 && lstat(path, &current) == 0,
                "selected Set disappeared while being pinned")
    let expectedHash = digest.finalize().map { String(format: "%02x", $0) }.joined()
    try require(before.st_dev == after.st_dev && before.st_ino == after.st_ino &&
                before.st_size == after.st_size && before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec &&
                before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec &&
                before.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec &&
                before.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec &&
                current.st_dev == before.st_dev && current.st_ino == before.st_ino &&
                current.st_size == before.st_size && current.st_mtimespec.tv_sec == before.st_mtimespec.tv_sec &&
                current.st_mtimespec.tv_nsec == before.st_mtimespec.tv_nsec &&
                current.st_ctimespec.tv_sec == before.st_ctimespec.tv_sec &&
                current.st_ctimespec.tv_nsec == before.st_ctimespec.tv_nsec,
                "selected Set changed while its stable pin was read")
    try require(expectedHash == options.sha256 && String(before.st_dev) == options.device &&
                String(before.st_ino) == options.inode,
                "selected Set SHA-256/device/inode differs from the approved pin")
    return [
        "path": path,
        "sha256": expectedHash,
        "bytes": count,
        "device": String(before.st_dev),
        "inode": String(before.st_ino),
    ]
}

func axValue(_ element: AXUIElement, _ attribute: String) throws -> CFTypeRef? {
    let now = try requireBeforeDeadline(
        options.deadlineMonotonicNanoseconds,
        "normal quit monotonic deadline expired during AX inspection"
    )
    let remaining = options.deadlineMonotonicNanoseconds - now
    let timeout = Float(min(1.0, Double(remaining) / nanosecondsPerSecond))
    AXUIElementSetMessagingTimeout(element, timeout)
    var value: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    try require(try monotonicNow() < options.deadlineMonotonicNanoseconds,
                "normal quit monotonic deadline expired during AX read")
    if error == .success { return value }
    if error == .noValue { return nil }
    throw AXReadFailure(attribute: attribute, code: error)
}

func stringValue(_ element: AXUIElement, _ attribute: String) throws -> String? {
    guard let value = try axValue(element, attribute) else { return nil }
    if let string = value as? String { return string }
    if let url = value as? URL { return url.absoluteString }
    return nil
}

func optionalDescription(_ element: AXUIElement) throws -> String? {
    do {
        return try stringValue(element, kAXDescriptionAttribute as String)
    } catch let failure as AXReadFailure
        where failure.attribute == (kAXDescriptionAttribute as String) &&
              failure.code == .attributeUnsupported {
        return nil
    }
}

func role(_ element: AXUIElement) throws -> String {
    guard let value = try stringValue(element, kAXRoleAttribute as String) else {
        throw QuitFailure(phase: phase, message: "AX role is unavailable")
    }
    return value
}

func appWindows(_ app: AXUIElement) throws -> [AXUIElement] {
    guard let raw = try axValue(app, kAXWindowsAttribute as String),
          let windows = raw as? [AXUIElement], !windows.isEmpty,
          windows.count <= maxWindows else {
        throw QuitFailure(phase: phase, message: "Live AX window inventory is unavailable or out of bounds")
    }
    for window in windows {
        let windowRole = try role(window)
        let subrole = try stringValue(window, kAXSubroleAttribute as String) ?? ""
        let modal = (try axValue(window, kAXModalAttribute as String) as? NSNumber)?.boolValue ?? false
        try require(!modal && windowRole != "AXDialog" &&
                    windowRole != "AXSheet" && windowRole != "AXPopover" &&
                    !["AXDialog", "AXSystemDialog", "AXFloatingWindow"].contains(subrole),
                    "Live has a dialog, sheet, popover or modal window; no quit was attempted")
    }
    return windows
}

func exactSetWindow(_ app: AXUIElement, setURL: URL) throws -> AXUIElement {
    let expected = setURL.absoluteString
    let matches = try appWindows(app).filter { window in
        guard try role(window) == (kAXWindowRole as String),
              let document = try stringValue(window, kAXDocumentAttribute as String) else { return false }
        return document == expected
    }
    try require(matches.count == 1, "exact source Set URL must identify one Live AX window")
    return matches[0]
}

func tree(_ root: AXUIElement) throws -> [AXUIElement] {
    var queue: [(AXUIElement, Int)] = [(root, 0)]
    var seen = Set<CFHashCode>()
    var result: [AXUIElement] = []
    while !queue.isEmpty {
        let (element, depth) = queue.removeFirst()
        guard seen.insert(CFHash(element)).inserted else { continue }
        result.append(element)
        try require(result.count <= maxElements, "Live AX Set window exceeds element bound")
        if let raw = try axValue(element, kAXChildrenAttribute as String) {
            guard let children = raw as? [AXUIElement] else {
                throw QuitFailure(phase: phase, message: "AXChildren is not an element array")
            }
            if depth < maxDepth {
                try require(result.count + queue.count + children.count <= maxElements,
                            "Live AX Set window exceeds element bound")
                queue.append(contentsOf: children.map { ($0, depth + 1) })
            } else {
                try require(children.isEmpty, "Live AX Set window exceeds depth bound")
            }
        }
    }
    return result
}

func checkboxValue(_ element: AXUIElement, description: String, identifier: String) throws -> Int {
    try require(try role(element) == (kAXCheckBoxRole as String), "transport \(description) role changed")
    try require(try stringValue(element, kAXDescriptionAttribute as String) == description &&
                stringValue(element, "AXIdentifier") == identifier,
                "transport \(description) identity changed")
    guard let raw = try axValue(element, kAXValueAttribute as String),
          let number = raw as? NSNumber else {
        throw QuitFailure(phase: phase, message: "transport \(description) value is unavailable")
    }
    let value = number.intValue
    try require((value == 0 || value == 1) && number.doubleValue == Double(value),
                "transport \(description) value is not binary")
    return value
}

func stoppedTransport(_ window: AXUIElement) throws -> TransportControls {
    let definitions: [(String, String)] = [
        ("Play", "Transport.Play"),
        ("Link", "Transport.AbletonLink"),
        ("Arrangement Record", "Transport.GlobalRecord"),
        ("Session Record", "Transport.SessionRecord"),
    ]
    var found: [String: AXUIElement] = [:]
    for element in try tree(window) {
        let description = try optionalDescription(element) ?? ""
        guard let definition = definitions.first(where: { $0.0 == description }) else { continue }
        try require(found[description] == nil, "duplicate exact transport control: \(description)")
        try require(try stringValue(element, "AXIdentifier") == definition.1,
                    "transport \(description) identifier changed")
        found[description] = element
    }
    try require(found.count == definitions.count, "exact Live transport controls are incomplete")
    let play = try checkboxValue(found["Play"]!, description: "Play", identifier: "Transport.Play")
    let link = try checkboxValue(found["Link"]!, description: "Link", identifier: "Transport.AbletonLink")
    let arrangement = try checkboxValue(found["Arrangement Record"]!, description: "Arrangement Record", identifier: "Transport.GlobalRecord")
    let session = try checkboxValue(found["Session Record"]!, description: "Session Record", identifier: "Transport.SessionRecord")
    try require(play == 0 && link == 0 && arrangement == 0 && session == 0,
                "Live transport must be stopped with Link and both record controls off")
    return TransportControls(play: play, link: link, arrangementRecord: arrangement, sessionRecord: session)
}

func verifyPreflight(deadline: UInt64) throws -> NSRunningApplication {
    _ = try requireBeforeDeadline(deadline, "quit preflight deadline expired")
    _ = try targetProcess()
    try verifyAppBundle()
    let pin = try setPin()
    sourcePin = pin
    let setURL = URL(fileURLWithPath: options.setPath)
    let app = AXUIElementCreateApplication(options.pid)
    try require(AXIsProcessTrusted(), "Accessibility is not already trusted; no permission prompt was requested")
    let window = try exactSetWindow(app, setURL: setURL)
    let controls = try stoppedTransport(window)
    transportState = controls
    _ = try requireBeforeDeadline(deadline, "quit preflight deadline expired")
    return try targetProcess()
}

func receipt(status: String, error: String? = nil) -> [String: Any] {
    var result: [String: Any] = [
        "format": receiptFormat,
        "status": status,
        "phase": phase,
        "read_only": !options.execute,
        "target": [
            "pid": Int(options.pid),
            "bundle_identifier": options.bundleID,
            "app_path": options.appPath,
            "executable_path": options.appPath + "/Contents/MacOS/Live",
            "process_generation": processGeneration ?? options.generation,
        ],
        "source_set_url": URL(fileURLWithPath: options.setPath).absoluteString,
        "source_file": sourcePin ?? NSNull(),
        "transport": transportState?.receipt ?? NSNull(),
        "quit": [
            "requested": quitRequested,
            "accepted": quitAccepted as Any? ?? NSNull(),
            "terminated": didTerminate,
            "outcome": didTerminate ? "terminated" : quitRequested ? "unknown" : "not_attempted",
            "force_quit": false,
        ],
        "manual_inspection_required": quitRequested && !didTerminate,
        "evidence_limit": "disk Set pin and AX transport state do not prove unsaved in-memory content matches disk",
    ]
    if let error { result["error"] = error }
    return result
}

func run() throws -> [String: Any] {
    let deadline = options.deadlineMonotonicNanoseconds
    phase = "preflight"
    let process = try verifyPreflight(deadline: deadline)
    if !options.execute {
        phase = "complete"
        return receipt(status: "ready")
    }

    phase = "final-guard"
    let current = try verifyPreflight(deadline: deadline)
    _ = try requireBeforeDeadline(deadline, "quit deadline expired before normal termination request")
    try require(try sameTargetProcess(process, current),
                "exact Live process changed immediately before normal termination")
    _ = try requireBeforeDeadline(deadline, "quit deadline expired immediately before normal termination")

    phase = "normal-quit-request"
    quitRequested = true
    quitAccepted = current.terminate()
    if try waitForTermination(current, deadline: deadline) {
        didTerminate = true
        phase = "complete"
        return receipt(status: "terminated")
    }
    phase = "quit-deadline"
    return receipt(status: "stopped", error: "normal quit did not terminate the exact PID before the monotonic deadline")
}

do {
    options = try parseOptions()
    let report = try run()
    let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
    if report["status"] as? String == "stopped" { exit(1) }
} catch let error as QuitFailure {
    phase = error.phase
    let report = receipt(status: "stopped", error: error.message)
    if let data = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) {
        print(text)
    } else {
        fputs("native Live quit helper could not encode its stopped receipt\n", stderr)
    }
    exit(1)
} catch {
    let report = receipt(status: "stopped", error: String(describing: error))
    if let data = try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) {
        print(text)
    } else {
        fputs("native Live quit helper could not encode its stopped receipt\n", stderr)
    }
    exit(1)
}
