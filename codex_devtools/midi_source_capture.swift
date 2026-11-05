// Passive CoreMIDI MIDI 1 source listener. It has no output port or send path.
import CoreMIDI
import Dispatch
import Foundation

let MAX_PACKET_DATA_BYTES = 256

struct CapturedPacket: Codable {
    let sequence: Int
    let sourceUniqueID: Int32
    let coreMIDITimestamp: UInt64
    let observedMonotonicNanoseconds: UInt64
    let bytes: [UInt8]
}

final class CaptureState {
    let lock = NSLock()
    let done = DispatchSemaphore(value: 0)
    let expectedSourceUID: Int32
    let maxEvents: Int
    let maxBytes: Int
    var packets: [CapturedPacket] = []
    var byteCount = 0
    var error: String? = nil
    var signaled = false

    init(sourceUID: Int32, maxEvents: Int, maxBytes: Int) {
        expectedSourceUID = sourceUID
        self.maxEvents = maxEvents
        self.maxBytes = maxBytes
    }

    func append(_ bytes: [UInt8], timestamp: UInt64) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if error != nil { return false }
        if packets.count >= maxEvents || bytes.count > maxBytes - byteCount {
            error = packets.count >= maxEvents ? "event_limit_reached" : "byte_limit_reached"
            signalLocked()
            return false
        }
        packets.append(CapturedPacket(
            sequence: packets.count,
            sourceUniqueID: expectedSourceUID,
            coreMIDITimestamp: timestamp,
            observedMonotonicNanoseconds: DispatchTime.now().uptimeNanoseconds,
            bytes: bytes
        ))
        byteCount += bytes.count
        if packets.count >= maxEvents || byteCount >= maxBytes {
            error = packets.count >= maxEvents ? "event_limit_reached" : "byte_limit_reached"
            signalLocked()
            return false
        }
        return true
    }

    func fail(_ reason: String) {
        lock.lock()
        defer { lock.unlock() }
        if error == nil { error = reason }
        signalLocked()
    }

    private func signalLocked() {
        if !signaled {
            signaled = true
            done.signal()
        }
    }
}

struct SourceIdentity: Codable {
    let uniqueID: Int32
    let offline: Bool
    let name: String
    let displayName: String
}

struct CaptureResult: Codable {
    var status = "failed"
    var runID = ""
    var processID: Int32 = 0
    var requestedSourceUID: Int32 = 0
    var source: SourceIdentity? = nil
    var requestedSeconds: Double = 0
    var maxEvents = 0
    var maxBytes = 0
    var packets: [CapturedPacket] = []
    var packetCount = 0
    var byteCount = 0
    var elapsedMonotonicNanoseconds: UInt64 = 0
    var cleanup: [String: Bool] = [:]
    var error: String? = nil
}

func propertyText(_ endpoint: MIDIEndpointRef, _ key: CFString) -> String? {
    var result: Unmanaged<CFString>?
    guard MIDIObjectGetStringProperty(endpoint, key, &result) == noErr else { return nil }
    return result?.takeRetainedValue() as String?
}

func integerProperty(_ endpoint: MIDIEndpointRef, _ key: CFString) -> Int32? {
    var result: Int32 = 0
    guard MIDIObjectGetIntegerProperty(endpoint, key, &result) == noErr else { return nil }
    return result
}

func resolveSource(_ uid: Int32) -> (MIDIEndpointRef, SourceIdentity)? {
    var matches: [(MIDIEndpointRef, SourceIdentity)] = []
    for index in 0..<MIDIGetNumberOfSources() {
        let endpoint = MIDIGetSource(index)
        guard let observedUID = integerProperty(endpoint, kMIDIPropertyUniqueID) else { continue }
        if observedUID != uid { continue }
        guard let offlineValue = integerProperty(endpoint, kMIDIPropertyOffline) else { return nil }
        matches.append((endpoint, SourceIdentity(
            uniqueID: observedUID,
            offline: offlineValue != 0,
            name: propertyText(endpoint, kMIDIPropertyName) ?? "",
            displayName: propertyText(endpoint, kMIDIPropertyDisplayName) ?? ""
        )))
    }
    return matches.count == 1 ? matches[0] : nil
}

func emitReady(runID: String, sourceUID: Int32, processID: Int32) throws {
    let marker: [String: Any] = [
        "event": "READY",
        "schema": "codex-devtools.raw-midi-source-ready/v1",
        "run_id": runID,
        "source_uid": sourceUID,
        "process_id": processID,
        "connected": true,
        "observed_monotonic_nanoseconds": DispatchTime.now().uptimeNanoseconds
    ]
    var data = try JSONSerialization.data(withJSONObject: marker, options: [.sortedKeys])
    data.append(0x0A)
    try FileHandle.standardOutput.write(contentsOf: data)
}

func run(sourceUID: Int32, seconds: Double, maxEvents: Int, maxBytes: Int, runID: String) -> CaptureResult {
    var result = CaptureResult()
    let processID = ProcessInfo.processInfo.processIdentifier
    result.runID = runID
    result.processID = processID
    result.requestedSourceUID = sourceUID
    result.requestedSeconds = seconds
    result.maxEvents = maxEvents
    result.maxBytes = maxBytes
    let started = DispatchTime.now().uptimeNanoseconds
    let clientName = "Codex Passive MIDI Source Capture \(UUID().uuidString)"
    guard let (source, identity) = resolveSource(sourceUID), !identity.offline else {
        result.error = "pinned source UID did not resolve to exactly one online CoreMIDI source"
        return result
    }
    result.source = identity

    let state = CaptureState(sourceUID: sourceUID, maxEvents: maxEvents, maxBytes: maxBytes)
    var client = MIDIClientRef()
    var input = MIDIPortRef()
    var clientCreated = false
    var inputCreated = false
    var connected = false

    let clientStatus = MIDIClientCreate(clientName as CFString, nil, nil, &client)
    if clientStatus == noErr { clientCreated = true }
    else { result.error = "MIDIClientCreate OSStatus \(clientStatus)" }

    if result.error == nil {
        let callback: MIDIReadProc = { packetList, context, _ in
            guard let context else { return }
            let state = Unmanaged<CaptureState>.fromOpaque(context).takeUnretainedValue()
            let list = packetList.pointee
            var packet = UnsafeMutablePointer<MIDIPacket>(mutating: packetList.pointer(to: \.packet)!)
            for _ in 0..<list.numPackets {
                let length = Int(packet.pointee.length)
                let timestamp = packet.pointee.timeStamp
                if length < 1 || length > MAX_PACKET_DATA_BYTES {
                    state.fail("packet_length_outside_fixed_payload_storage")
                    break
                }
                let bytes = withUnsafeBytes(of: packet.pointee.data) { raw -> [UInt8]? in
                    guard length <= raw.count else { return nil }
                    return Array(raw.prefix(length))
                }
                guard let bytes else {
                    state.fail("packet_length_exceeds_addressable_payload_storage")
                    break
                }
                if !state.append(bytes, timestamp: timestamp) { break }
                packet = UnsafeMutablePointer(MIDIPacketNext(UnsafePointer(packet)))
            }
        }
        let context = Unmanaged.passUnretained(state).toOpaque()
        let portStatus = MIDIInputPortCreate(client, "Codex Passive MIDI Input" as CFString, callback, context, &input)
        if portStatus == noErr { inputCreated = true }
        else { result.error = "MIDIInputPortCreate OSStatus \(portStatus)" }
    }

    if result.error == nil {
        let connectStatus = MIDIPortConnectSource(input, source, nil)
        if connectStatus == noErr { connected = true }
        else { result.error = "MIDIPortConnectSource OSStatus \(connectStatus)" }
    }

    if result.error == nil {
        if let (currentSource, currentIdentity) = resolveSource(sourceUID),
           currentSource == source, !currentIdentity.offline {
            do {
                try emitReady(runID: runID, sourceUID: sourceUID, processID: processID)
            } catch {
                result.error = "READY marker write failed: \(error)"
            }
        } else {
            result.error = "pinned source identity changed before READY"
        }
    }

    if result.error == nil {
        let wait = state.done.wait(timeout: .now() + .milliseconds(Int(seconds * 1000)))
        if wait == .timedOut {
            // Normal bounded-window completion. Callback errors and cap hits signal early.
        }
        state.lock.lock()
        result.packets = state.packets
        result.packetCount = state.packets.count
        result.byteCount = state.byteCount
        result.error = result.error ?? state.error
        state.lock.unlock()

        if let (currentSource, currentIdentity) = resolveSource(sourceUID),
           currentSource == source, !currentIdentity.offline {
            // The exact endpoint handle is still unique, online and UID-pinned.
        } else {
            result.error = "pinned source identity changed or went offline during capture"
        }
    }

    if connected {
        result.cleanup["sourceDisconnected"] = MIDIPortDisconnectSource(input, source) == noErr
    }
    if inputCreated {
        result.cleanup["inputPortDisposed"] = MIDIPortDispose(input) == noErr
    }
    if clientCreated {
        result.cleanup["clientDisposed"] = MIDIClientDispose(client) == noErr
    }
    result.elapsedMonotonicNanoseconds = DispatchTime.now().uptimeNanoseconds - started
    if result.error == nil && result.cleanup.values.allSatisfy({ $0 }) {
        result.status = "captured"
    } else if result.error == nil {
        result.error = "CoreMIDI input cleanup did not complete successfully"
    }
    return result
}

let arguments = CommandLine.arguments
var result = CaptureResult()
if arguments.count == 6,
   let sourceUID = Int32(arguments[1]),
   let seconds = Double(arguments[2]), seconds.isFinite, seconds >= 0.05, seconds <= 30,
   let maxEvents = Int(arguments[3]), maxEvents >= 1, maxEvents <= 65536,
   let maxBytes = Int(arguments[4]), maxBytes >= 1, maxBytes <= 1048576,
   let runID = UUID(uuidString: arguments[5]), runID.uuidString.lowercased() == arguments[5] {
    result = run(sourceUID: sourceUID, seconds: seconds, maxEvents: maxEvents, maxBytes: maxBytes, runID: arguments[5])
} else {
    result.error = "expected source UID, seconds (0.05–30), max-events (1–65536), max-bytes (1–1048576), and canonical run UUID"
}
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
let json = try encoder.encode(result)
print(String(data: json, encoding: .utf8)!)
if result.status != "captured" { exit(EXIT_FAILURE) }
