import sys
import hashlib
import json
import unittest
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools.midi_assertions import analyze, check_expectations, verify_capture_receipt


def event(kind, **kwargs):
    return {"type": kind, **kwargs}


class MidiAssertionsTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            event("note_on", channel=1, note=60, value=100, origin="A"),
            event("note_on", channel=1, note=60, value=0, origin="A"),  # MIDI note-off convention
            event("note_off", channel=1, note=61, value=64, origin="A"),  # unmatched
            event("note_on", channel=2, note=67, value=90, origin="B"),
            event("control_change", channel=1, controller=7, value=20, origin="A"),
            event("control_change", channel=1, controller=7, value=110, origin="A"),
            event("invalid", raw="00 FF", origin="B"),
            event("clock", timestamp=1.0, timestamp_unit="reference_seconds", origin="A"),
            event("clock", timestamp=1.01, timestamp_unit="reference_seconds", origin="A"),
            event("clock", timestamp=1.03, timestamp_unit="reference_seconds", origin="A"),
            event("clock", timestamp=12345, timestamp_unit="host_ticks_or_nanoseconds", origin="B"),
            event("program_change", timestamp=None, timestamp_unit=None, origin="A"),
        ]

    def test_analysis_counts_balance_cc_invalids_and_clock_intervals(self):
        report = analyze(self.events)
        self.assertEqual(report["event_counts"]["clock"], 4)
        self.assertEqual(report["by_origin"], {"A": 9, "B": 3})
        self.assertEqual(report["invalids"], {"count": 1, "events": [{"index": 6, "raw": "00 FF", "origin": "B"}]})
        self.assertEqual(report["note_balance"]["note_on_count"], 2)
        self.assertEqual(report["note_balance"]["note_off_count"], 2)
        self.assertEqual(report["note_balance"]["unmatched_note_off_count"], 1)
        self.assertEqual(report["note_balance"]["active_notes"], [{"origin": "B", "channel": 2, "note": 67, "active_count": 1}])
        self.assertEqual(report["cc_ranges"], [{"origin": "A", "channel": 1, "controller": 7, "count": 2, "min": 20, "max": 110}])
        clock = report["clock_intervals"]["A"]
        self.assertEqual(clock["interval_count"], 2)
        self.assertAlmostEqual(clock["min_seconds"], 0.01)
        self.assertAlmostEqual(clock["max_seconds"], 0.02)
        self.assertAlmostEqual(clock["mean_seconds"], 0.015)
        self.assertEqual(report["clock_intervals"]["B"]["interval_count"], 0)
        self.assertEqual(report["clock_intervals"]["B"]["ignored_timestamp_units"], {"host_ticks_or_nanoseconds": 1})
        self.assertEqual(report["unknown_timestamp_units"], {"host_ticks_or_nanoseconds": 1})
        self.assertEqual(report["events_without_timestamp"], 8)
        self.assertNotIn("bpm", clock)  # No tempo inference.

    def test_expectations_match_subsets_and_report_bounds(self):
        result = check_expectations(self.events, {"expectations": [
            {"type": "control_change", "controller": 7, "origin": "A", "min_count": 2, "max_count": 2},
            {"type": "note_on", "channel": 1, "value": 0, "min_count": 1},
            {"type": "clock", "origin": "B", "min_count": 1, "max_count": 1},
            {"type": "stop", "max_count": 0},
            {"raw": "00 FF", "type": "invalid", "min_count": 1},
        ]})
        self.assertTrue(result["passed"])
        self.assertEqual([check["count"] for check in result["checks"]], [2, 1, 1, 0, 1])
        failed = check_expectations(self.events, {"expectations": [{"type": "clock", "max_count": 2}]})
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["checks"][0]["count"], 4)

    def test_rejects_unknown_keys_and_invalid_rules(self):
        bad = (
            {"expectations": [{"foo": 1, "min_count": 1}]},
            {"expectations": [{"type": "clock"}]},
            {"expectations": [{"min_count": 1}]},
            {"expectations": [{"type": "clock", "min_count": -1}]},
            {"expectations": [{"channel": 17, "min_count": 1}]},
            {"expectations": [{"type": "clock", "min_count": 3, "max_count": 2}]},
            {"unexpected": [], "expectations": []},
            {"expectations": []},
            [],
        )
        for document in bad:
            with self.subTest(document=document), self.assertRaises(ValueError):
                check_expectations(self.events, document)

    def test_receipt_verification_checks_exact_document_bytes_and_sources(self):
        source = {"portInputStream": [{"uniqueID": 123, "name": "Capture"}]}
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            capture = run / "capture-run.mmon"
            capture.write_bytes(b"exact saved document")
            document = {"document_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
                        "settings": {"streamSettings": source}, "events": [{"type": "clock"}]}
            receipt_path = run / "receipt.json"
            receipt = {
                "schema": "devtools-midi-capture-v1", "status": "captured",
                "capture_file": capture.name,
                "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
                "configured_settings": {"streamSettings": source},
                "resolved_settings": {"streamSettings": source}, "event_count": 1,
                "template_sha256": "template-hash", "adapter_sha256": "adapter-hash",
            }
            receipt_path.write_text(json.dumps(receipt))
            result = verify_capture_receipt(capture, document, receipt_path)
            self.assertEqual(result["source_identity"], {"portInputStream": [123]})
            self.assertTrue(result["verified"])

            receipt["capture_sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                verify_capture_receipt(capture, document, receipt_path)

            receipt["capture_sha256"] = hashlib.sha256(capture.read_bytes()).hexdigest()
            receipt["resolved_settings"] = {"streamSettings": {"portInputStream": [{"uniqueID": 456}]}}
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "source identity"):
                verify_capture_receipt(capture, document, receipt_path)


if __name__ == "__main__":
    unittest.main()
