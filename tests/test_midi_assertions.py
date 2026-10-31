import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools.midi_assertions import analyze, check_expectations


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
            [],
        )
        for document in bad:
            with self.subTest(document=document), self.assertRaises(ValueError):
                check_expectations(self.events, document)


if __name__ == "__main__":
    unittest.main()
