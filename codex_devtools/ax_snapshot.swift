// Read-only macOS Accessibility snapshot for one caller-selected process.
import ApplicationServices
import AppKit
import Foundation

let maximumWindows = 64

struct Options {
    var pid: pid_t = 0
    var maxElements = 500
    var maxDepth = 10
    var windowTitle: String?
}

func fail(_ message: String, code: Int32 = 2) -> Never {
    fputs("\(message)\n", stderr)
    exit(code)
}

func parseOptions() -> Options {
    var options = Options()
    var index = 1
    let args = CommandLine.arguments
    while index < args.count {
        let key = args[index]
        if key == "--help" {
            print("Usage: ax_snapshot.swift --pid PID [--window-title EXACT] [--max-elements N] [--max-depth N]")
            exit(0)
        }
        guard index + 1 < args.count else { fail("missing value for \(key)") }
        let value = args[index + 1]
        switch key {
        case "--pid":
            guard let parsed = Int32(value), parsed > 0 else { fail("PID must be a positive 32-bit integer") }
            options.pid = pid_t(parsed)
        case "--max-elements":
            guard let parsed = Int(value), (1...2048).contains(parsed) else { fail("max elements must be between 1 and 2048") }
            options.maxElements = parsed
        case "--max-depth":
            guard let parsed = Int(value), (0...32).contains(parsed) else { fail("max depth must be between 0 and 32") }
            options.maxDepth = parsed
        case "--window-title":
            guard !value.isEmpty, value.utf8.count <= 256 else { fail("window title must contain 1 to 256 UTF-8 bytes") }
            options.windowTitle = value
        default:
            fail("unknown option: \(key)")
        }
        index += 2
    }
    guard options.pid > 0 else { fail("--pid is required") }
    return options
}

func axValue(_ element: AXUIElement, _ attribute: String) -> (Any?, Int32?) {
    var value: CFTypeRef?
    // Bound each out-of-process accessibility message without showing a prompt.
    AXUIElementSetMessagingTimeout(element, 2.0)
    let result = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard result == .success else { return (nil, result.rawValue) }
    return (value, nil)
}

func boundedString(_ value: String, limit: Int = 512) -> (String, Bool) {
    guard value.utf8.count > limit else { return (value, false) }
    var result = ""
    for character in value {
        let next = result + String(character)
        if next.utf8.count > limit { break }
        result = next
    }
    return (result, true)
}

func scalar(_ value: Any?) -> Any? {
    guard let value else { return nil }
    if let string = value as? String { return boundedString(string).0 }
    if let number = value as? NSNumber {
        if CFGetTypeID(number) == CFBooleanGetTypeID() { return number.boolValue }
        return number
    }
    return "<non-scalar>"
}

func attributeText(_ element: AXUIElement, _ attribute: String) -> String? {
    let (value, _) = axValue(element, attribute)
    guard let value else { return nil }
    if let string = value as? String { return boundedString(string).0 }
    return nil
}

func elementRecord(_ element: AXUIElement, index: Int, parent: Int?, depth: Int) -> [String: Any] {
    let attributes: [(String, String)] = [
        ("role", kAXRoleAttribute as String),
        ("subrole", kAXSubroleAttribute as String),
        ("title", kAXTitleAttribute as String),
        ("description", kAXDescriptionAttribute as String),
        ("identifier", "AXIdentifier"),
        ("help", kAXHelpAttribute as String),
        ("value", kAXValueAttribute as String),
        ("enabled", kAXEnabledAttribute as String)
    ]
    var record: [String: Any] = ["index": index, "depth": depth, "identity": String(CFHash(element))]
    if let parent { record["parent_index"] = parent }
    var errors: [String: Int32] = [:]
    var truncatedAttributes: [String] = []
    for (name, attribute) in attributes {
        let (value, error) = axValue(element, attribute)
        if let error { errors[name] = error; continue }
        guard let value else { continue }
        if name == "enabled" {
            if let number = value as? NSNumber { record[name] = number.boolValue }
            else { errors[name] = -1 }
        } else if let string = value as? String {
            let (bounded, wasTruncated) = boundedString(string)
            record[name] = bounded
            if wasTruncated { truncatedAttributes.append(name) }
        } else if name == "value" {
            record[name] = scalar(value)
        }
    }
    if !errors.isEmpty { record["attribute_errors"] = errors }
    if !truncatedAttributes.isEmpty { record["truncated_attributes"] = truncatedAttributes }
    return record
}

func children(_ element: AXUIElement) -> ([AXUIElement], Int32?) {
    let (value, error) = axValue(element, kAXChildrenAttribute as String)
    guard let value, let array = value as? [AXUIElement] else { return ([], error) }
    return (array, nil)
}

func childrenErrorIndicatesLeaf(_ error: Int32?) -> Bool {
    error == AXError.noValue.rawValue
}

func main() {
    let options = parseOptions()
    guard AXIsProcessTrusted() else {
        fail("Accessibility is not trusted for this process; no permission prompt was requested")
    }
    guard let app = NSRunningApplication(processIdentifier: options.pid) else {
        fail("no running application found for PID \(options.pid)")
    }

    let appElement = AXUIElementCreateApplication(options.pid)
    let (windowValue, windowError) = axValue(appElement, kAXWindowsAttribute as String)
    guard windowError == nil, let windowValue, let windows = windowValue as? [AXUIElement] else {
        fail("could not read target application windows (AX error \(windowError ?? -1))")
    }
    let windowRecords = windows.prefix(maximumWindows).enumerated().map { index, window in
        ["index": index, "title": attributeText(window, kAXTitleAttribute as String) ?? "",
         "identity": String(CFHash(window))] as [String: Any]
    }
    var selected = Array(windows.enumerated())
    if let exactTitle = options.windowTitle {
        selected = selected.filter { attributeText($0.element, kAXTitleAttribute as String) == exactTitle }
        guard selected.count == 1 else {
            fail("exact window title matched \(selected.count) windows; expected one")
        }
    } else if selected.count > maximumWindows {
        selected = Array(selected.prefix(maximumWindows))
    }

    var total = 0
    var anyTruncated = windows.count > maximumWindows
    var trees: [[String: Any]] = []
    for (windowIndex, window) in selected {
        var records: [[String: Any]] = []
        var seen = Set<CFHashCode>()
        var queue: [(AXUIElement, Int?, Int)] = [(window, nil, 0)]
        while !queue.isEmpty && total < options.maxElements {
            let (element, parent, depth) = queue.removeFirst()
            let identity = CFHash(element)
            if !seen.insert(identity).inserted { continue }
            let index = total
            records.append(elementRecord(element, index: index, parent: parent, depth: depth))
            total += 1
            if depth < options.maxDepth {
                let (childElements, childrenError) = children(element)
                if let childrenError { records[records.count - 1]["children_error"] = childrenError }
                for child in childElements {
                    if total + queue.count >= options.maxElements { anyTruncated = true; break }
                    queue.append((child, index, depth + 1))
                }
                if childrenError != nil && !childrenErrorIndicatesLeaf(childrenError) { anyTruncated = true }
            } else {
                let (childElements, childrenError) = children(element)
                if let childrenError { records[records.count - 1]["children_error"] = childrenError }
                if !childElements.isEmpty || (childrenError != nil && !childrenErrorIndicatesLeaf(childrenError)) {
                    anyTruncated = true
                }
            }
        }
        if !queue.isEmpty { anyTruncated = true }
        trees.append(["window_index": windowIndex, "elements": records])
        if total >= options.maxElements && selected.last?.offset != windowIndex { anyTruncated = true; break }
    }

    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let windowTitleFilter: Any = options.windowTitle.map { $0 as Any } ?? (NSNull() as Any)
    let result: [String: Any] = [
        "format": "codex-devtools-ax-snapshot-v1",
        "status": "observed",
        "observed_at_utc": formatter.string(from: Date()),
        "read_only": true,
        "permission": "Accessibility already trusted; no prompt requested",
        "target": ["pid": options.pid, "process_name": app.localizedName ?? "", "bundle_identifier": app.bundleIdentifier ?? "",
                   "launch_date": app.launchDate.map { formatter.string(from: $0) } ?? ""],
        "bounds": ["max_elements": options.maxElements, "max_depth": options.maxDepth, "max_windows": maximumWindows, "windows_observed": windowRecords.count, "window_count": windows.count, "windows_truncated": windows.count > maximumWindows, "elements_observed": total, "truncated": anyTruncated, "window_title_filter": windowTitleFilter],
        "windows": windowRecords,
        "trees": trees
    ]
    guard JSONSerialization.isValidJSONObject(result),
          let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]),
          let text = String(data: data, encoding: .utf8) else { fail("could not serialize AX snapshot") }
    print(text)
}

main()
