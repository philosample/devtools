"""Synthetic NSKeyedArchive fixtures shaped from krevis/MIDIApps source.

The personal .mmon capture is deliberately not copied into this repository.
"""

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools.midi_monitor import parse_document, write_csv, write_json


def _uid(index):
    return plistlib.UID(index)


def synthetic_document(*, class_name="SMVoiceMessage", status=0xB2, data=b"\x07\x64", outer_version=2):
    class_index = 3
    event = {"$class": _uid(class_index), "statusByte": status,
             "timeStampInNanos": 123456789, "clockTimeStamp": 42.25,
             "originatingEndpoint": _uid(4)}
    if class_name == "SMSystemExclusiveMessage":
        event.update(data=_uid(5), wasReceivedWithEOX=True)
        objects = ["$null", {"NS.objects": [_uid(2)]}, event,
                   {"$classname": class_name}, "From Synth", data]
    else:
        event["dataBytes"] = data
        objects = ["$null", {"NS.objects": [_uid(2)]}, event,
                   {"$classname": class_name}, "From Synth"]
    archive = {"$archiver": "NSKeyedArchiver", "$version": 100000,
               "$top": {"root": _uid(1)}, "$objects": objects}
    outer = {"version": outer_version, "streamSettings": {"portInputStream": []},
             "messageData": plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)}
    return plistlib.dumps(outer, fmt=plistlib.FMT_BINARY)


class MidiMonitorTests(unittest.TestCase):
    def parse_bytes(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.mmon"
            path.write_bytes(data)
            return parse_document(path)

    def test_decodes_cc_status_channel_raw_and_timestamp(self):
        result = self.parse_bytes(synthetic_document())
        event = result["events"][0]
        self.assertEqual(result["version"], 2)
        self.assertEqual(event["type"], "control_change")
        self.assertEqual(event["channel"], 3)
        self.assertEqual((event["controller"], event["value"]), (7, 100))
        self.assertEqual(event["raw"], "B2 07 64")
        self.assertEqual(event["timestamp"], 42.25)
        self.assertEqual(event["origin"], "From Synth")

    def test_sysex_raw_bytes_include_start_and_recorded_eox(self):
        result = self.parse_bytes(synthetic_document(class_name="SMSystemExclusiveMessage", status=0xF0, data=b"\x7d\x01"))
        self.assertEqual(result["events"][0]["raw"], "F0 7D 01 F7")

    def test_one_data_byte_voice_message_omits_archived_padding(self):
        result = self.parse_bytes(synthetic_document(status=0xC0, data=b"\x05\x00"))
        self.assertEqual(result["events"][0]["type"], "program_change")
        self.assertEqual(result["events"][0]["raw"], "C0 05")

    def test_rejects_truncated_input_and_unknown_schemas(self):
        for raw in (b"bplist00\x00", b"not a plist"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "malformed or truncated"):
                self.parse_bytes(raw)
        with self.assertRaisesRegex(ValueError, "unsupported MIDI Monitor document version"):
            self.parse_bytes(synthetic_document(outer_version=99))
        malformed = plistlib.loads(synthetic_document())
        malformed["messageData"] = plistlib.dumps({"$archiver": "Other", "$objects": [], "$top": {}}, fmt=plistlib.FMT_BINARY)
        with self.assertRaisesRegex(ValueError, "unknown messageData format"):
            self.parse_bytes(plistlib.dumps(malformed, fmt=plistlib.FMT_BINARY))
        with self.assertRaisesRegex(ValueError, "unsupported message class"):
            self.parse_bytes(synthetic_document(class_name="ArbitraryExecutableClass"))

    def test_writes_json_and_csv(self):
        result = self.parse_bytes(synthetic_document())
        with tempfile.TemporaryDirectory() as directory:
            json_path, csv_path = Path(directory) / "events.json", Path(directory) / "events.csv"
            write_json(result, json_path)
            write_csv(result, csv_path)
            self.assertIn('"control_change"', json_path.read_text())
            self.assertIn("controller", csv_path.read_text().splitlines()[0])
            self.assertIn("B2 07 64", csv_path.read_text())


if __name__ == "__main__":
    unittest.main()
