"""Synthetic NSKeyedArchive fixtures shaped from krevis/MIDIApps source.

The personal .mmon capture is deliberately not copied into this repository.
"""

import plistlib
import sys
import tempfile
import unittest
import contextlib
import io
import json
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools.midi_monitor import parse_document, write_csv, write_json
from codex_devtools.cli import main


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
    elif class_name == "SMSystemRealTimeMessage":
        objects = ["$null", {"NS.objects": [_uid(2)]}, event,
                   {"$classname": class_name}, "From Synth"]
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

    def test_realtime_message_has_no_archived_data_bytes(self):
        result = self.parse_bytes(synthetic_document(
            class_name="SMSystemRealTimeMessage", status=0xF8, data=b""))
        self.assertEqual(result["events"][0]["type"], "clock")
        self.assertEqual(result["events"][0]["raw"], "F8")

    def test_origin_is_archived_display_string_not_endpoint_object(self):
        data = synthetic_document()
        outer = plistlib.loads(data)
        archive = plistlib.loads(outer["messageData"])
        archive["$objects"][4] = {"$class": _uid(5), "name": "Synth"}
        archive["$objects"].append({"$classname": "SMEndpointDescription"})
        outer["messageData"] = plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)
        with self.assertRaisesRegex(ValueError, "originatingEndpoint is not an archived string"):
            self.parse_bytes(plistlib.dumps(outer, fmt=plistlib.FMT_BINARY))

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
            with self.assertRaises(FileExistsError):
                write_json(result, json_path)
            with self.assertRaises(FileExistsError):
                write_csv(result, csv_path)

    def test_report_cli_emits_provenance_and_checks_matching_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "capture-run.mmon"
            source = {"portInputStream": [{"uniqueID": 123, "name": "Capture"}]}
            outer = plistlib.loads(synthetic_document())
            outer["streamSettings"] = source
            capture.write_bytes(plistlib.dumps(outer, fmt=plistlib.FMT_BINARY))
            parsed = parse_document(capture)
            receipt = {
                "schema": "devtools-midi-capture-v1", "status": "captured",
                "capture_file": capture.name,
                "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
                "configured_settings": {"streamSettings": source},
                "resolved_settings": {"streamSettings": source},
                "event_count": len(parsed["events"]),
                "template_sha256": "template", "adapter_sha256": "adapter",
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            expectations = root / "expectations.json"
            expectations.write_text(json.dumps({"expectations": [
                {"type": "control_change", "channel": 3, "controller": 7, "value": 100, "min_count": 1}
            ]}))

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["midi", "report", str(capture), "--expect", str(expectations), "--receipt", str(receipt_path)])
            result = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertTrue(result["assertions"]["passed"])
            self.assertEqual(result["provenance"]["receipt"]["source_identity"], {"portInputStream": [123]})

            receipt["status"] = "inconclusive"
            receipt_path.write_text(json.dumps(receipt))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["midi", "report", str(capture), "--expect", str(expectations), "--receipt", str(receipt_path)])
            result = json.loads(output.getvalue())
            self.assertEqual(status, 1)
            self.assertFalse(result["assertions"]["passed"])
            self.assertIn("event buffer reached", result["provenance"]["capture_gate"])

            expectations.write_text('{"expectations": []}')
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                self.assertEqual(main(["midi", "report", str(capture), "--expect", str(expectations)]), 2)
            self.assertIn("at least one explicit success condition", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
