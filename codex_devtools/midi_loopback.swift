// Isolated CoreMIDI MIDI 1.0 transport smoke test. Sends only to the private
// virtual destination handle created by this process; never enumerates ports.
import CoreMIDI
import Foundation
import Dispatch

struct Event: Codable {
    let bytes: [UInt8]
    let scheduledTimestamp: UInt64
    let observedMonotonicNanoseconds: UInt64
}

struct Result: Codable {
    var status = "failed"
    var endpointName = ""
    var sentEvents = 0
    var receivedEvents = 0
    var events: [Event] = []
    var callbackCount = 0
    var error: String? = nil
    var cleanup: [String: Bool] = [:]
}

let fixture: [[UInt8]] = [
    [0x90, 0x3c, 0x64], // note on, channel 1, C4
    [0x80, 0x3c, 0x00], // note off, channel 1, C4
    [0xb0, 0x07, 0x55], // CC 7, channel 1
    [0xf8]              // MIDI clock
]

func run(timeoutSeconds: Double) -> Result {
    var result = Result()
    let token = UUID().uuidString.lowercased()
    let name = "Codex Native Loopback \(token)"
    result.endpointName = name
    var client = MIDIClientRef()
    var destination = MIDIEndpointRef()
    var output = MIDIPortRef()
    var clientCreated = false
    var destinationCreated = false
    var outputCreated = false
    let lock = NSLock()
    let received = DispatchSemaphore(value: 0)
    var callbackCount = 0
    var events: [Event] = []
    var callbackError: String?

    let clientStatus = MIDIClientCreateWithBlock(name as CFString, &client, nil)
    if clientStatus == noErr { clientCreated = true }
    else { result.error = "MIDIClientCreateWithBlock OSStatus \(clientStatus)" }

    if result.error == nil {
      let destStatus = MIDIDestinationCreateWithProtocol(client, name as CFString, MIDIProtocolID._1_0, &destination) { eventList, _ in
        let observed = DispatchTime.now().uptimeNanoseconds
        var local: [Event] = []
        var localError: String? = nil
        let list = eventList.pointee
        if list.protocol != MIDIProtocolID._1_0 {
            localError = "unexpected callback protocol \(list.protocol)"
        } else {
            var packet = UnsafeMutablePointer<MIDIEventPacket>(mutating: eventList.pointer(to: \MIDIEventList.packet)!)
            for _ in 0..<list.numPackets {
                let timestamp = packet.pointee.timeStamp
                for index in 0..<packet.pointee.wordCount {
                    let word = withUnsafeBytes(of: packet.pointee.words) {
                        $0.load(fromByteOffset: Int(index) * MemoryLayout<UInt32>.size, as: UInt32.self)
                    }
                    let messageType = UInt8((word >> 28) & 0x0f)
                    let status = UInt8((word >> 16) & 0xff)
                    let data1 = UInt8((word >> 8) & 0x7f)
                    let data2 = UInt8(word & 0x7f)
                    if messageType == 0x2 {
                        let length = (0xc0...0xdf).contains(status) ? 2 : 3
                        local.append(Event(bytes: length == 2 ? [status, data1] : [status, data1, data2], scheduledTimestamp: timestamp, observedMonotonicNanoseconds: observed))
                    } else if messageType == 0x1 {
                        let systemStatus = status
                        let length = [UInt8(0xf1), 0xf3].contains(systemStatus) ? 2 : ([UInt8(0xf2)].contains(systemStatus) ? 3 : 1)
                        local.append(Event(bytes: length == 1 ? [systemStatus] : (length == 2 ? [systemStatus, data1] : [systemStatus, data1, data2]), scheduledTimestamp: timestamp, observedMonotonicNanoseconds: observed))
                    } else {
                        localError = "unexpected UMP message type \(messageType)"
                    }
                }
                packet = UnsafeMutablePointer(MIDIEventPacketNext(UnsafePointer(packet)))
            }
        }
        lock.lock()
        callbackCount += 1
        events.append(contentsOf: local)
        if callbackError == nil { callbackError = localError }
        lock.unlock()
        received.signal()
      }
      if destStatus == noErr { destinationCreated = true }
      else { result.error = "MIDIDestinationCreateWithProtocol OSStatus \(destStatus)" }
    }
    if result.error == nil {
      let portStatus = MIDIOutputPortCreate(client, "Codex Native Loopback Output" as CFString, &output)
      if portStatus == noErr { outputCreated = true }
      else { result.error = "MIDIOutputPortCreate OSStatus \(portStatus)" }
    }

    if result.error == nil {
      let capacity = 4096
      let storage = UnsafeMutableRawPointer.allocate(byteCount: capacity, alignment: MemoryLayout<MIDIEventList>.alignment)
      let list = storage.bindMemory(to: MIDIEventList.self, capacity: 1)
      var packet = MIDIEventListInit(list, MIDIProtocolID._1_0)
      for bytes in fixture {
          let status = bytes[0]
          let data1 = bytes.count > 1 ? UInt32(bytes[1]) : 0
          let data2 = bytes.count > 2 ? UInt32(bytes[2]) : 0
          let messageType: UInt32 = status >= 0xf0 ? 0x10000000 : 0x20000000
          let word = messageType | (UInt32(status) << 16) | (data1 << 8) | data2
          var mutableWord = word
          packet = MIDIEventListAdd(list, capacity, packet, 0, 1, &mutableWord)
      }
      let sendStatus = MIDISendEventList(output, destination, UnsafePointer(list))
      storage.deallocate()
      if sendStatus == noErr {
          result.sentEvents = fixture.count
          let deadline = DispatchTime.now() + timeoutSeconds
          if received.wait(timeout: deadline) == .timedOut {
              result.error = "timed out waiting for private destination callback after \(timeoutSeconds) seconds"
          } else {
              // One callback can carry the complete event list. Do not depend on callback count.
              while DispatchTime.now() < deadline {
                  lock.lock(); let count = events.count; lock.unlock()
                  if count >= fixture.count { break }
                  Thread.sleep(forTimeInterval: 0.005)
              }
              lock.lock(); let count = events.count; lock.unlock()
              if count < fixture.count { result.error = "timed out waiting for all fixture events after \(timeoutSeconds) seconds" }
          }
      } else { result.error = "MIDISendEventList OSStatus \(sendStatus)" }
    }
    lock.lock()
    result.callbackCount = callbackCount
    result.events = events
    result.receivedEvents = events.count
    result.error = result.error ?? callbackError
    lock.unlock()
    if result.error == nil && result.events.map(\.bytes) == fixture {
        result.status = "passed"
    } else if result.error == nil {
        result.error = "received events did not exactly match fixture bytes/order/count"
    }

    if outputCreated { result.cleanup["output_port"] = MIDIPortDispose(output) == noErr }
    if destinationCreated { result.cleanup["virtual_destination"] = MIDIEndpointDispose(destination) == noErr }
    if clientCreated { result.cleanup["client"] = MIDIClientDispose(client) == noErr }
    return result
}

let timeout = CommandLine.arguments.count > 1 ? (Double(CommandLine.arguments[1]) ?? 3.0) : 3.0
let result = run(timeoutSeconds: timeout)
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
let data = try encoder.encode(result)
print(String(data: data, encoding: .utf8)!)
