import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools.cli import main
from codex_devtools.live_midi_ax import ROWS, verify_studio_capture_isolation

VALUES = {
    "input_track": 1,
    "input_sync": 0,
    "input_remote": 0,
    "output_track": 1,
    "output_sync": 0,
    "output_remote": 0,
}
BOUNDS_FIELDS = (
    "max_elements",
    "max_depth",
    "max_windows",
    "windows_observed",
    "window_count",
    "windows_truncated",
    "elements_observed",
    "truncated",
    "window_title_filter",
)


def snapshot(values: dict[str, int], observed_at: datetime) -> dict:
    return {
        "format": "codex-devtools-ax-snapshot-v1",
        "status": "observed",
        "read_only": True,
        "observed_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
        "target": {
            "pid": 1234,
            "process_name": "Live",
            "bundle_identifier": "com.ableton.live",
        },
        "bounds": {
            "max_elements": 500,
            "max_depth": 10,
            "max_windows": 64,
            "windows_observed": 3,
            "window_count": 3,
            "windows_truncated": False,
            "elements_observed": len(values),
            "truncated": False,
            "window_title_filter": "Settings",
        },
        "windows": [
            {"index": 0, "title": "Settings"},
            {"index": 1, "title": "Other window"},
            {"index": 2, "title": "Another window"},
        ],
        "trees": [
            {
                "window_index": 0,
                "elements": [
                    {
                        "index": index,
                        "depth": 0,
                        "role": "AXCheckBox",
                        "description": ROWS[name],
                        "value": value,
                        "enabled": True,
                    }
                    for index, (name, value) in enumerate(values.items())
                ],
            }
        ],
    }


class LiveMidiAxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.times = [
            datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
            + timedelta(seconds=10 * i)
            for i in range(3)
        ]

    def tearDown(self):
        self.temp.cleanup()

    def write_triplet(self, values: list[dict[str, int]]) -> list[Path]:
        paths = [
            self.root / name for name in ("before.json", "off.json", "restored.json")
        ]
        for path, data, observed_at in zip(paths, values, self.times, strict=True):
            path.write_text(json.dumps(snapshot(data, observed_at)), encoding="utf-8")
        return paths

    def verify(self, paths: list[Path], **kwargs) -> dict:
        return verify_studio_capture_isolation(
            *paths,
            expected_bundle_id="com.ableton.live",
            **kwargs,
        )

    def test_valid_track_off_and_exact_restore(self):
        isolated = VALUES | {"input_track": 0}
        report = self.verify(self.write_triplet([VALUES, isolated, VALUES]))
        self.assertTrue(report["valid"])
        self.assertEqual(report["changed_during_isolation"], ["input_track"])
        self.assertEqual(
            report["switches"]["input_track"],
            {
                "description": ROWS["input_track"],
                "before": 1,
                "isolated": 0,
                "restored": 1,
            },
        )
        self.assertIsNone(report["freshness_gate"]["passed"])

    def test_no_value_children_errors_on_leaf_controls_remain_valid_metadata(self):
        paths = self.write_triplet([VALUES, VALUES | {"input_track": 0}, VALUES])
        for path in paths:
            observed = json.loads(path.read_text(encoding="utf-8"))
            for element in observed["trees"][0]["elements"]:
                element["children_error"] = -25212
            path.write_text(json.dumps(observed), encoding="utf-8")

        report = self.verify(paths)
        self.assertTrue(report["valid"])
        self.assertEqual(report["changed_during_isolation"], ["input_track"])

    def test_cli_emits_assertion_report(self):
        paths = self.write_triplet([VALUES, VALUES | {"input_track": 0}, VALUES])
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(
                [
                    "ax",
                    "verify-studio-capture",
                    *(str(path) for path in paths),
                    "--expected-bundle-id",
                    "com.ableton.live",
                ]
            )
        self.assertEqual(status, 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["valid"])
        self.assertEqual(len(report["snapshot_sha256"]), 3)

    def test_already_off_input_track_can_pass_without_a_change(self):
        off = VALUES | {"input_track": 0}
        report = self.verify(self.write_triplet([off, off, off]))
        self.assertTrue(report["valid"])
        self.assertEqual(report["changed_during_isolation"], [])

    def test_wrong_switch_or_failed_restoration_fails_assertion(self):
        isolated = VALUES | {"input_track": 0, "input_remote": 1}
        report = self.verify(self.write_triplet([VALUES, isolated, isolated]))
        self.assertFalse(report["valid"])
        self.assertFalse(report["checks"]["other_switches_match_expected"])
        self.assertFalse(report["checks"]["restored_exactly"])

    def test_duplicate_or_missing_exact_rows_are_rejected(self):
        duplicate_path = self.root / "duplicate.json"
        duplicate = snapshot(VALUES, self.times[0])
        duplicate_row = duplicate["trees"][0]["elements"][0].copy()
        duplicate_row["index"] = len(VALUES)
        duplicate["trees"][0]["elements"].append(duplicate_row)
        duplicate["bounds"]["elements_observed"] += 1
        duplicate_path.write_text(json.dumps(duplicate), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate AX row"):
            self.verify([duplicate_path, *self.write_triplet([VALUES] * 3)[1:]])

        missing_path = self.root / "missing.json"
        missing = snapshot(VALUES, self.times[0])
        missing["trees"][0]["elements"].pop()
        missing["bounds"]["elements_observed"] -= 1
        missing_path.write_text(json.dumps(missing), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing Studio Capture"):
            self.verify([missing_path, *self.write_triplet([VALUES] * 3)[1:]])

    def test_truncated_wrong_target_and_wrong_checkbox_are_rejected(self):
        truncated_path = self.root / "truncated.json"
        truncated = snapshot(VALUES, self.times[0])
        truncated["bounds"]["truncated"] = True
        truncated_path.write_text(json.dumps(truncated), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "tree is truncated"):
            self.verify([truncated_path, *self.write_triplet([VALUES] * 3)[1:]])

        wrong_target_paths = self.write_triplet([VALUES] * 3)
        wrong_target = json.loads(wrong_target_paths[0].read_text())
        wrong_target["target"]["bundle_identifier"] = "wrong.bundle"
        wrong_target_paths[0].write_text(json.dumps(wrong_target), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bundle identity changed"):
            self.verify(wrong_target_paths)

        wrong_control_path = self.root / "wrong-control.json"
        wrong_control = snapshot(VALUES, self.times[0])
        wrong_control["trees"][0]["elements"][0]["role"] = "AXButton"
        wrong_control_path.write_text(json.dumps(wrong_control), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected role"):
            self.verify([wrong_control_path, *self.write_triplet([VALUES] * 3)[1:]])

    def test_every_required_v1_bounds_field_must_be_present(self):
        for field in BOUNDS_FIELDS:
            path = self.root / f"missing-{field}.json"
            malformed = snapshot(VALUES, self.times[0])
            del malformed["bounds"][field]
            path.write_text(json.dumps(malformed), encoding="utf-8")
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "missing required fields"),
            ):
                self.verify([path, *self.write_triplet([VALUES] * 3)[1:]])

    def test_v1_bound_types_and_window_element_counts_must_agree(self):
        mutations = (
            ("windows_observed", 2),
            ("window_count", 4),
            ("elements_observed", 7),
            ("windows_truncated", True),
        )
        for field, value in mutations:
            path = self.root / f"invalid-{field}.json"
            malformed = snapshot(VALUES, self.times[0])
            malformed["bounds"][field] = value
            path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify([path, *self.write_triplet([VALUES] * 3)[1:]])

        windows_path = self.root / "short-windows.json"
        malformed = snapshot(VALUES, self.times[0])
        malformed["windows"].pop()
        windows_path.write_text(json.dumps(malformed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "windows array disagrees"):
            self.verify([windows_path, *self.write_triplet([VALUES] * 3)[1:]])

    def test_window_list_must_contain_exactly_one_settings_title(self):
        path = self.root / "duplicate-settings-title.json"
        malformed = snapshot(VALUES, self.times[0])
        malformed["windows"][1]["title"] = "Settings"
        path.write_text(json.dumps(malformed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly one Settings window title"):
            self.verify([path, *self.write_triplet([VALUES] * 3)[1:]])

    def test_every_v1_bounds_field_requires_its_declared_type(self):
        wrong_values = {
            "max_elements": True,
            "max_depth": "10",
            "max_windows": 64.0,
            "windows_observed": True,
            "window_count": "3",
            "windows_truncated": 0,
            "elements_observed": False,
            "truncated": 0,
            "window_title_filter": 1,
        }
        for field, value in wrong_values.items():
            path = self.root / f"wrong-type-{field}.json"
            malformed = snapshot(VALUES, self.times[0])
            malformed["bounds"][field] = value
            path.write_text(json.dumps(malformed), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify([path, *self.write_triplet([VALUES] * 3)[1:]])

    def test_optional_freshness_gate_rejects_stale_snapshots(self):
        paths = self.write_triplet([VALUES, VALUES | {"input_track": 0}, VALUES])
        with self.assertRaisesRegex(ValueError, "outside the requested age window"):
            self.verify(
                paths,
                max_age_seconds=80,
                now=self.times[-1] + timedelta(seconds=61),
            )
        report = self.verify(
            paths,
            max_age_seconds=80,
            now=self.times[-1] + timedelta(seconds=60),
        )
        self.assertTrue(report["freshness_gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
