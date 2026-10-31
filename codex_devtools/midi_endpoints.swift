// Read-only inventory. Never creates ports, sends MIDI, or changes routing.
import Foundation
import CoreMIDI

func text(_ endpoint: MIDIEndpointRef, _ key: CFString) -> String? {
    var result: Unmanaged<CFString>?
    guard MIDIObjectGetStringProperty(endpoint, key, &result) == noErr else { return nil }
    return result?.takeRetainedValue() as String?
}
var endpoints: [[String: Any]] = []
for direction in ["source", "destination"] {
    let count = direction == "source" ? MIDIGetNumberOfSources() : MIDIGetNumberOfDestinations()
    for i in 0..<count {
        let endpoint = direction == "source" ? MIDIGetSource(i) : MIDIGetDestination(i)
        var uid: Int32 = 0
        var offline: Int32 = 0
        guard MIDIObjectGetIntegerProperty(endpoint, kMIDIPropertyUniqueID, &uid) == noErr else { continue }
        MIDIObjectGetIntegerProperty(endpoint, kMIDIPropertyOffline, &offline)
        endpoints.append([
            "direction": direction, "uniqueID": uid, "offline": offline != 0,
            "name": text(endpoint, kMIDIPropertyName) ?? "",
            "displayName": text(endpoint, kMIDIPropertyDisplayName) ?? ""
        ])
    }
}
let data = try JSONSerialization.data(withJSONObject: endpoints, options: [.prettyPrinted, .sortedKeys])
print(String(data: data, encoding: .utf8)!)
