import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from codex_devtools.cli import build_parser
from codex_devtools.live_midi_sync_control import (
    RECEIPT_FORMAT,
    _focus_diagnostics_valid,
    _native_receipt_valid,
    baseline_from_snapshot,
    historical_restore_expected,
    run_live_midi_sync,
    transition_plan,
    validate_baseline,
)

NAMES = ("A", "B (Port 1)", "C")
VALUES = {"A": 1, "B (Port 1)": 0, "C": 1}


def snapshot(values=None, *, observed_at=None):
    values = VALUES if values is None else values
    elements = [
        {
            "index": 0,
            "identity": "5000",
            "depth": 0,
            "role": "AXWindow",
            "title": "Settings",
        }
    ]
    elements.extend(
        {
            "index": index,
            "identity": str(5000 + index),
            "depth": 1,
            "role": "AXCheckBox",
            "description": f"{name}: MIDI Output: Sync",
            "enabled": True,
            "value": value,
        }
        for index, (name, value) in enumerate(values.items(), start=1)
    )
    elements.append(
        {
            "index": len(elements),
            "identity": str(5000 + len(elements)),
            "depth": 1,
            "role": "AXCheckBox",
            "description": "IAC Driver: MIDI Input: Sync",
            "enabled": True,
            "value": 1,
        }
    )
    return {
        "format": "codex-devtools-ax-snapshot-v1",
        "status": "observed",
        "read_only": True,
        "observed_at_utc": (observed_at or datetime.now(timezone.utc))
        .isoformat()
        .replace("+00:00", "Z"),
        "target": {
            "pid": 58198,
            "process_name": "Live",
            "bundle_identifier": "com.ableton.live",
            "launch_date": "2026-11-07T12:00:00.000Z",
        },
        "bounds": {
            "max_elements": 1500,
            "max_depth": 12,
            "max_windows": 64,
            "windows_observed": 1,
            "window_count": 1,
            "windows_truncated": False,
            "elements_observed": len(elements),
            "truncated": False,
            "window_title_filter": "Settings",
        },
        "windows": [{"index": 0, "title": "Settings", "identity": "7000"}],
        "trees": [{"window_index": 0, "elements": elements}],
    }


def baseline(values=None):
    return baseline_from_snapshot(
        snapshot(values),
        expected_pid=58198,
        expected_bundle_id="com.ableton.live",
        expected_count=len(VALUES),
    )


def historical_pair():
    original = json.loads(json.dumps(baseline()))
    original["observed_at_utc"] = (
        (datetime.now(timezone.utc) - timedelta(minutes=10))
        .isoformat()
        .replace("+00:00", "Z")
    )
    current = json.loads(json.dumps(original))
    current["observed_at_utc"] = (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    row = next(item for item in current["outputs"] if item["name"] == "A")
    row["value"] = 0
    checkbox = next(
        item for item in current["checkboxes"] if item["identity"] == row["identity"]
    )
    checkbox["value"] = 0
    return original, current


class LiveMidiSyncControlTests(unittest.TestCase):
    def test_complete_snapshot_builds_sorted_exact_baseline(self):
        result = baseline()
        self.assertEqual(result["expected_output_count"], 3)
        self.assertEqual([row["name"] for row in result["outputs"]], sorted(VALUES))
        self.assertEqual(
            {row["name"]: row["value"] for row in result["outputs"]}, VALUES
        )
        self.assertTrue(result["read_only"])
        self.assertEqual(
            result["settings_window"],
            {"title": "Settings", "index": 0, "identity": "7000"},
        )
        self.assertEqual(result["format"], "codex-devtools-live-midi-sync-baseline-v2")
        self.assertEqual(len(result["checkboxes"]), len(VALUES) + 1)

    def test_focus_diagnostics_keep_the_three_refusal_facts_separate(self):
        diagnostics = {
            "format": "codex-devtools-live-midi-sync-focus-diagnostics-v1",
            "captured_after_guard_refusal": True,
            "process_is_active": {"status": "observed", "value": True},
            "frontmost_pid": {"status": "observed", "value": 12345},
            "focused_window": {
                "status": "observed",
                "role": {"status": "observed", "value": "AXWindow"},
                "title": {"status": "observed", "value": "Settings"},
                "matches_settings": {"status": "observed", "value": False},
            },
        }
        self.assertTrue(_focus_diagnostics_valid(diagnostics))

    def test_focus_diagnostics_preserve_unavailable_and_ax_error_states(self):
        diagnostics = {
            "format": "codex-devtools-live-midi-sync-focus-diagnostics-v1",
            "captured_after_guard_refusal": True,
            "process_is_active": {
                "status": "unavailable",
                "reason": "process_unavailable",
            },
            "frontmost_pid": {
                "status": "unavailable",
                "reason": "no_frontmost_application",
            },
            "focused_window": {
                "status": "error",
                "error_code": -25204,
                "role": {"status": "error", "error_code": -25204},
                "title": {"status": "error", "error_code": -25204},
                "matches_settings": {
                    "status": "unavailable",
                    "reason": "focused_window_unavailable",
                },
            },
        }
        self.assertTrue(_focus_diagnostics_valid(diagnostics))
        unavailable_window = diagnostics | {
            "focused_window": {
                "status": "unavailable",
                "reason": "no_value",
                "role": {
                    "status": "unavailable",
                    "reason": "focused_window_unavailable",
                },
                "title": {
                    "status": "unavailable",
                    "reason": "focused_window_unavailable",
                },
                "matches_settings": {
                    "status": "unavailable",
                    "reason": "focused_window_unavailable",
                },
            },
        }
        self.assertTrue(_focus_diagnostics_valid(unavailable_window))

    def test_focus_diagnostics_reject_malformed_or_unbounded_facts(self):
        valid = {
            "format": "codex-devtools-live-midi-sync-focus-diagnostics-v1",
            "captured_after_guard_refusal": True,
            "process_is_active": {"status": "observed", "value": False},
            "frontmost_pid": {"status": "observed", "value": 12345},
            "focused_window": {
                "status": "observed",
                "role": {"status": "observed", "value": "AXWindow"},
                "title": {"status": "observed", "value": "Other window"},
                "matches_settings": {"status": "observed", "value": False},
            },
        }
        invalid = []
        extra = valid | {"whole_ax_tree": []}
        invalid.append(extra)
        malformed_pid = valid | {"frontmost_pid": {"status": "observed", "value": True}}
        invalid.append(malformed_pid)
        too_long_title = valid | {
            "focused_window": valid["focused_window"]
            | {"title": {"status": "observed", "value": "x" * 129}}
        }
        invalid.append(too_long_title)
        malformed_match = valid | {
            "focused_window": valid["focused_window"]
            | {"matches_settings": {"status": "observed", "value": 0}}
        }
        invalid.append(malformed_match)
        malformed_process_reason = valid | {
            "process_is_active": {"status": "unavailable", "reason": []}
        }
        invalid.append(malformed_process_reason)
        malformed_window_status = valid | {
            "focused_window": valid["focused_window"] | {"status": []}
        }
        invalid.append(malformed_window_status)
        malformed_window_status_dict = valid | {
            "focused_window": valid["focused_window"] | {"status": {}}
        }
        invalid.append(malformed_window_status_dict)
        malformed_window_reason = valid | {
            "focused_window": valid["focused_window"]
            | {
                "role": {"status": "unavailable", "reason": {}},
            }
        }
        invalid.append(malformed_window_reason)
        malformed_window_error_reason = valid | {
            "focused_window": valid["focused_window"]
            | {
                "role": {"status": "error", "reason": {}},
            }
        }
        invalid.append(malformed_window_error_reason)
        for reason in ([], {}):
            malformed_complete_unavailable_window = valid | {
                "focused_window": {
                    "status": "unavailable",
                    "reason": reason,
                    "role": {
                        "status": "unavailable",
                        "reason": "focused_window_unavailable",
                    },
                    "title": {
                        "status": "unavailable",
                        "reason": "focused_window_unavailable",
                    },
                    "matches_settings": {
                        "status": "unavailable",
                        "reason": "focused_window_unavailable",
                    },
                }
            }
            invalid.append(malformed_complete_unavailable_window)
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                self.assertFalse(_focus_diagnostics_valid(candidate))

    def test_focus_refusal_diagnostics_survive_wrapper_without_action_changes(self):
        observed = baseline()
        diagnostics = {
            "format": "codex-devtools-live-midi-sync-focus-diagnostics-v1",
            "captured_after_guard_refusal": True,
            "process_is_active": {"status": "observed", "value": True},
            "frontmost_pid": {"status": "observed", "value": 12345},
            "focused_window": {
                "status": "observed",
                "role": {"status": "observed", "value": "AXWindow"},
                "title": {"status": "observed", "value": "Settings"},
                "matches_settings": {"status": "observed", "value": False},
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            baseline_path = Path(temporary) / "baseline.json"
            receipt_path = Path(temporary) / "receipt.json"
            baseline_bytes = json.dumps(observed).encode()
            baseline_path.write_bytes(baseline_bytes)
            baseline_hash = hashlib.sha256(baseline_bytes).hexdigest()
            native_report = {
                "format": RECEIPT_FORMAT,
                "status": "stopped",
                "phase": "preflight",
                "operation": "isolate",
                "read_only": False,
                "target": observed["target"],
                "baseline_sha256": baseline_hash,
                "expected_output_count": observed["expected_output_count"],
                "actions": [
                    {
                        "name": row["name"],
                        "description": row["description"],
                        "baseline_value": row["value"],
                        "desired_value": 0,
                        "attempted": False,
                        "outcome": "not_attempted",
                        "actual_value": row["value"],
                    }
                    for row in observed["outputs"]
                ],
                "manual_inspection_required": False,
                "error": "exact Live Settings window is not foreground and focused",
                "focus_diagnostics": diagnostics,
            }
            result = subprocess.CompletedProcess(
                args=["swift"],
                returncode=1,
                stdout=json.dumps(native_report),
                stderr="",
            )
            with (
                patch(
                    "codex_devtools.live_midi_sync_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_midi_sync_control.subprocess.run",
                    return_value=result,
                ),
            ):
                report = run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="isolate",
                    baseline_path=baseline_path,
                    output_path=receipt_path,
                    execute=True,
                )
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
            malformed_diagnostics = diagnostics | {
                "process_is_active": {"status": "unavailable", "reason": []}
            }
            malformed_receipt = native_report | {
                "focus_diagnostics": malformed_diagnostics
            }
            malformed_receipt_path = Path(temporary) / "malformed-receipt.json"
            malformed_result = subprocess.CompletedProcess(
                args=["swift"],
                returncode=1,
                stdout=json.dumps(malformed_receipt),
                stderr="",
            )
            with (
                patch(
                    "codex_devtools.live_midi_sync_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_midi_sync_control.subprocess.run",
                    return_value=malformed_result,
                ),
            ):
                malformed = run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="isolate",
                    baseline_path=baseline_path,
                    output_path=malformed_receipt_path,
                    execute=True,
                )
            malformed_saved = json.loads(
                malformed_receipt_path.read_text(encoding="utf-8")
            )
            first_candidate = next(
                row["name"] for row in observed["outputs"] if row["value"] == 1
            )
            between_press_receipt = native_report | {
                "phase": "actions",
                "manual_inspection_required": True,
                "error": "target focus or process identity changed before next output",
                "actions": [
                    row
                    | (
                        {
                            "attempted": True,
                            "outcome": "known",
                            "actual_value": 0,
                            "prepress_elements": observed["bounds"][
                                "elements_observed"
                            ],
                            "prepress_windows": observed["bounds"]["window_count"],
                        }
                        if row["name"] == first_candidate
                        else {
                            "attempted": False,
                            "outcome": "not_attempted",
                            "actual_value": row["baseline_value"],
                        }
                    )
                    for row in native_report["actions"]
                ],
            }
            between_press_path = Path(temporary) / "between-press-receipt.json"
            between_press_result = subprocess.CompletedProcess(
                args=["swift"],
                returncode=1,
                stdout=json.dumps(between_press_receipt),
                stderr="",
            )
            with (
                patch(
                    "codex_devtools.live_midi_sync_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_midi_sync_control.subprocess.run",
                    return_value=between_press_result,
                ),
            ):
                between_press = run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="isolate",
                    baseline_path=baseline_path,
                    output_path=between_press_path,
                    execute=True,
                )
            between_press_saved = json.loads(
                between_press_path.read_text(encoding="utf-8")
            )
        self.assertEqual(report, saved)
        self.assertEqual(saved["focus_diagnostics"], diagnostics)
        self.assertFalse(saved["manual_inspection_required"])
        self.assertTrue(all(row["attempted"] is False for row in saved["actions"]))
        self.assertEqual(malformed, malformed_saved)
        self.assertEqual(malformed["phase"], "native-helper-invalid-receipt")
        self.assertTrue(malformed["manual_inspection_required"])
        self.assertTrue(
            all(
                row["attempted"] is None and row["outcome"] == "unknown"
                for row in malformed["actions"]
                if row["baseline_value"] == 1
            )
        )
        self.assertEqual(between_press, between_press_saved)
        self.assertEqual(between_press["phase"], "actions")
        self.assertTrue(between_press["manual_inspection_required"])
        self.assertEqual(between_press["focus_diagnostics"], diagnostics)
        between_rows = {row["name"]: row for row in between_press["actions"]}
        self.assertEqual(between_rows[first_candidate]["outcome"], "known")
        self.assertTrue(between_rows[first_candidate]["attempted"])
        self.assertEqual(between_rows[first_candidate]["actual_value"], 0)
        for name, row in between_rows.items():
            if name != first_candidate:
                self.assertFalse(row["attempted"])
                self.assertEqual(row["outcome"], "not_attempted")

    def test_swift_focus_diagnostics_are_read_only_and_failure_only(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_midi_sync.swift"
        ).read_text(encoding="utf-8")
        helper = source.split("func focusRefusalDiagnostic", 1)[1].split(
            "func recordFocusRefusal", 1
        )[0]
        self.assertIn("process.isActive", helper)
        self.assertIn("NSWorkspace.shared.frontmostApplication", helper)
        self.assertIn("kAXFocusedWindowAttribute", helper)
        self.assertIn("kAXRoleAttribute", helper)
        self.assertIn("kAXTitleAttribute", helper)
        self.assertIn("CFEqual(window, expectedSettings)", helper)
        self.assertNotIn("AXUIElementPerformAction", helper)
        self.assertNotIn("activate(", helper)
        error_wrapper = source.split("func focusedSettingsWindowOrRecord", 1)[1].split(
            "func targetCheck", 1
        )[0]
        self.assertIn("catch", error_wrapper)
        self.assertIn("recordFocusRefusal", error_wrapper)
        self.assertIn("throw error", error_wrapper)
        self.assertIn(
            "recordFocusRefusal(app: app, settings: settings, pid: options.pid, bundleID: options.bundleID)",
            source,
        )

    def test_swift_sets_actions_phase_before_each_between_press_focus_guard(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_midi_sync.swift"
        ).read_text(encoding="utf-8")
        loop = source.split("for name in candidates {", 1)[1].split(
            "let (freshSettings", 1
        )[0]
        self.assertLess(
            loop.index('phase = "actions"'),
            loop.index("let currentProcess = try targetCheck"),
        )

    def test_baseline_rejects_wrong_identity_incomplete_or_ambiguous_inventory(self):
        with self.assertRaisesRegex(ValueError, "exact PID"):
            baseline_from_snapshot(
                snapshot(),
                expected_pid=1,
                expected_bundle_id="com.ableton.live",
                expected_count=3,
            )
        with self.assertRaisesRegex(ValueError, "expected 4"):
            baseline_from_snapshot(
                snapshot(),
                expected_pid=58198,
                expected_bundle_id="com.ableton.live",
                expected_count=4,
            )
        with self.assertRaisesRegex(ValueError, "bundle identifier"):
            baseline_from_snapshot(
                snapshot(),
                expected_pid=58198,
                expected_bundle_id="wrong.bundle",
                expected_count=3,
            )
        malformed = snapshot()
        malformed["bounds"]["truncated"] = True
        with self.assertRaisesRegex(ValueError, "incomplete or out of bounds"):
            baseline_from_snapshot(
                malformed,
                expected_pid=58198,
                expected_bundle_id="com.ableton.live",
                expected_count=3,
            )
        duplicate_window = snapshot()
        duplicate_window["bounds"]["window_count"] = 2
        duplicate_window["bounds"]["windows_observed"] = 2
        duplicate_window["windows"].append({"index": 1, "title": "Settings"})
        with self.assertRaisesRegex(ValueError, "exactly one Settings"):
            baseline_from_snapshot(
                duplicate_window,
                expected_pid=58198,
                expected_bundle_id="com.ableton.live",
                expected_count=3,
            )
        unreadable_window = snapshot()
        unreadable_window["bounds"]["window_count"] = 2
        unreadable_window["bounds"]["windows_observed"] = 2
        unreadable_window["windows"].append({"index": 1, "title": ""})
        with self.assertRaisesRegex(ValueError, "invalid row"):
            baseline_from_snapshot(
                unreadable_window,
                expected_pid=58198,
                expected_bundle_id="com.ableton.live",
                expected_count=3,
            )
        disabled = snapshot()
        disabled["trees"][0]["elements"][1]["enabled"] = False
        with self.assertRaisesRegex(ValueError, "enabled checkbox"):
            baseline_from_snapshot(
                disabled,
                expected_pid=58198,
                expected_bundle_id="com.ableton.live",
                expected_count=3,
            )
        duplicate = snapshot()
        row = duplicate["trees"][0]["elements"][1].copy()
        row["index"] = len(duplicate["trees"][0]["elements"])
        duplicate["trees"][0]["elements"].append(row)
        duplicate["bounds"]["elements_observed"] += 1
        with self.assertRaisesRegex(
            ValueError, "duplicated or malformed|duplicate output Sync"
        ):
            baseline_from_snapshot(
                duplicate,
                expected_pid=58198,
                expected_bundle_id="com.ableton.live",
                expected_count=3,
            )
        for invalid_format in ([], {}):
            malformed_format = baseline()
            malformed_format["format"] = invalid_format
            with (
                self.subTest(format=invalid_format),
                self.assertRaisesRegex(ValueError, "format is unsupported"),
            ):
                validate_baseline(
                    malformed_format,
                    expected_pid=58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                )

    def test_baseline_freshness_is_bounded(self):
        observed = baseline()
        with self.assertRaisesRegex(ValueError, "freshness window"):
            validate_baseline(
                observed,
                expected_pid=58198,
                expected_bundle_id="com.ableton.live",
                expected_count=3,
                now=datetime.now(timezone.utc) + timedelta(minutes=6),
            )

    def test_legacy_v1_baseline_keeps_ordinary_validation_but_cannot_authorize_history(
        self,
    ):
        observed = json.loads(json.dumps(baseline()))
        observed["format"] = "codex-devtools-live-midi-sync-baseline-v1"
        observed["target"].pop("launch_date")
        observed["settings_window"].pop("identity")
        observed.pop("checkboxes")
        for row in observed["outputs"]:
            row.pop("identity")
            row.pop("index")
        self.assertIs(
            validate_baseline(
                observed,
                expected_pid=58198,
                expected_bundle_id="com.ableton.live",
                expected_count=3,
            ),
            observed,
        )
        old, current = historical_pair()
        old.update(observed)
        with self.assertRaisesRegex(ValueError, "complete v2"):
            historical_restore_expected(old, current)

    def test_historical_restore_accepts_expired_original_and_fresh_partial_sync_state(
        self,
    ):
        original, current = historical_pair()
        values = historical_restore_expected(original, current)
        output = next(row for row in original["outputs"] if row["name"] == "A")
        self.assertEqual(values[output["identity"]], 0)
        validate_baseline(
            original,
            expected_pid=58198,
            expected_bundle_id="com.ableton.live",
            expected_count=3,
            allow_stale=True,
        )
        validate_baseline(
            current,
            expected_pid=58198,
            expected_bundle_id="com.ableton.live",
            expected_count=3,
        )
        future_original = json.loads(json.dumps(original))
        future_original["observed_at_utc"] = (
            (datetime.now(timezone.utc) + timedelta(seconds=10))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.assertRaisesRegex(ValueError, "freshness window"):
            validate_baseline(
                future_original,
                expected_pid=58198,
                expected_bundle_id="com.ableton.live",
                expected_count=3,
                allow_stale=True,
            )

    def test_historical_restore_allows_settings_window_index_drift_only(self):
        original, current = historical_pair()
        original["bounds"]["window_count"] = 2
        original["bounds"]["windows_observed"] = 2
        current["bounds"]["window_count"] = 2
        current["bounds"]["windows_observed"] = 2
        current["settings_window"]["index"] = 1
        expected = historical_restore_expected(original, current)
        output = next(row for row in current["outputs"] if row["name"] == "A")
        self.assertEqual(expected[output["identity"]], output["value"])

        for mutation in (
            lambda window: window.update(identity="other-window"),
            lambda window: window.update(title="Other"),
            lambda window: window.update(index=2),
            lambda window: window.update(index=True),
        ):
            changed = json.loads(json.dumps(current))
            mutation(changed["settings_window"])
            with (
                self.subTest(window=changed["settings_window"]),
                self.assertRaisesRegex(ValueError, "settings_window"),
            ):
                historical_restore_expected(original, changed)

    def test_historical_restore_allows_only_valid_total_window_count_drift(self):
        original, current = historical_pair()
        original["bounds"]["window_count"] = 4
        original["bounds"]["windows_observed"] = 4
        current["bounds"]["window_count"] = 2
        current["bounds"]["windows_observed"] = 2
        expected = historical_restore_expected(original, current)
        output = next(row for row in current["outputs"] if row["name"] == "A")
        self.assertEqual(expected[output["identity"]], output["value"])

        for mutation in (
            lambda bounds: bounds.update(
                elements_observed=bounds["elements_observed"] + 1
            ),
            lambda bounds: bounds.update(window_count=0),
            lambda bounds: bounds.update(window_count=65),
            lambda bounds: bounds.update(window_count=True),
            lambda bounds: bounds.update(window_count=2.0),
            lambda bounds: bounds.update(windows_observed=3),
            lambda bounds: bounds.update(windows_observed=True),
            lambda bounds: bounds.update(windows_observed=2.0),
            lambda bounds: bounds.update(windows_truncated=True),
        ):
            changed = json.loads(json.dumps(current))
            mutation(changed["bounds"])
            with (
                self.subTest(bounds=changed["bounds"]),
                self.assertRaisesRegex(ValueError, "bounds"),
            ):
                historical_restore_expected(original, changed)

    def test_historical_restore_rejects_generation_keyset_non_sync_drift_and_off_to_on(
        self,
    ):
        original, current = historical_pair()
        cases = []
        generation = json.loads(json.dumps(current))
        generation["target"]["launch_date"] = "2026-11-07T12:00:02.000Z"
        cases.append(("generation", generation, "target"))
        settings = json.loads(json.dumps(current))
        settings["settings_window"]["identity"] = "other-window"
        cases.append(("window", settings, "settings_window"))
        bounds = json.loads(json.dumps(current))
        bounds["bounds"]["elements_observed"] += 1
        cases.append(("bounds", bounds, "bounds"))
        keyset = json.loads(json.dumps(current))
        keyset["checkboxes"].pop()
        cases.append(("keyset", keyset, "checkbox identity/order"))
        non_sync = json.loads(json.dumps(current))
        non_sync["checkboxes"][-1]["value"] = 0
        cases.append(("non-sync", non_sync, "outside output Sync"))
        originally_off = json.loads(json.dumps(current))
        off_row = next(
            row for row in originally_off["outputs"] if row["name"] == "B (Port 1)"
        )
        off_row["value"] = 1
        next(
            row
            for row in originally_off["checkboxes"]
            if row["identity"] == off_row["identity"]
        )["value"] = 1
        cases.append(("off-to-on", originally_off, "originally-off"))
        stale = json.loads(json.dumps(current))
        stale["observed_at_utc"] = (
            (datetime.now(timezone.utc) - timedelta(minutes=6))
            .isoformat()
            .replace("+00:00", "Z")
        )
        cases.append(("stale", stale, "freshness window"))
        for label, candidate, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, message):
                if label == "stale":
                    validate_baseline(
                        candidate,
                        expected_pid=58198,
                        expected_bundle_id="com.ableton.live",
                        expected_count=3,
                    )
                else:
                    historical_restore_expected(original, candidate)

    def test_isolate_only_changes_baseline_enabled_rows(self):
        plan = transition_plan("isolate", VALUES, VALUES)
        self.assertEqual(
            plan,
            [
                {"name": "A", "previous_value": 1, "desired_value": 0},
                {"name": "C", "previous_value": 1, "desired_value": 0},
            ],
        )

    def test_restore_accepts_partial_isolate_and_only_restores_original_ones(self):
        current = {"A": 0, "B (Port 1)": 0, "C": 1}
        self.assertEqual(
            transition_plan("restore", VALUES, current),
            [{"name": "A", "previous_value": 0, "desired_value": 1}],
        )

    def test_isolate_preflight_mismatch_and_unexpected_restore_values_stop(self):
        with self.assertRaisesRegex(ValueError, "changed from baseline"):
            transition_plan("isolate", VALUES, {"A": 0, "B (Port 1)": 0, "C": 1})
        with self.assertRaisesRegex(ValueError, "partial-isolation"):
            transition_plan("restore", VALUES, {"A": 1, "B (Port 1)": 1, "C": 0})
        with self.assertRaisesRegex(ValueError, "identities differ"):
            transition_plan("restore", VALUES, {"A": 0, "C": 0})

    def test_timeout_preserves_conservative_per_control_receipt(self):
        observed = baseline()
        with tempfile.TemporaryDirectory() as temporary:
            baseline_path = Path(temporary) / "baseline.json"
            receipt_path = Path(temporary) / "receipt.json"
            baseline_path.write_text(json.dumps(observed), encoding="utf-8")
            with (
                patch(
                    "codex_devtools.live_midi_sync_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_midi_sync_control.subprocess.run",
                    side_effect=subprocess.TimeoutExpired("swift", 180),
                ),
            ):
                report = run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="isolate",
                    baseline_path=baseline_path,
                    output_path=receipt_path,
                    execute=True,
                )
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(report, saved)
        self.assertEqual(saved["status"], "stopped")
        self.assertTrue(saved["manual_inspection_required"])
        outcomes = {row["name"]: row["outcome"] for row in saved["actions"]}
        self.assertEqual(
            outcomes, {"A": "unknown", "B (Port 1)": "not_attempted", "C": "unknown"}
        )

    def test_historical_restore_pins_both_hashes_and_calls_native_with_current_inventory(
        self,
    ):
        original, current = historical_pair()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_path = root / "original.json"
            current_path = root / "fresh-current.json"
            receipt_path = root / "receipt.json"
            original_bytes = json.dumps(original).encode()
            current_bytes = json.dumps(current).encode()
            baseline_path.write_bytes(original_bytes)
            current_path.write_bytes(current_bytes)
            baseline_hash = hashlib.sha256(original_bytes).hexdigest()
            current_hash = hashlib.sha256(current_bytes).hexdigest()
            observed_values = {row["name"]: row["value"] for row in original["outputs"]}
            current_values = {row["name"]: row["value"] for row in current["outputs"]}
            actions = []
            for row in original["outputs"]:
                name = row["name"]
                before = current_values[name]
                desired = row["value"]
                changed = before != desired
                item = {
                    "name": name,
                    "description": row["description"],
                    "baseline_value": desired,
                    "desired_value": desired,
                    "attempted": changed,
                    "outcome": "known" if changed else "not_attempted",
                    "actual_value": desired if changed else before,
                }
                if changed:
                    item.update(
                        prepress_elements=original["bounds"]["elements_observed"],
                        prepress_windows=original["bounds"]["window_count"],
                    )
                actions.append(item)
            native_report = {
                "format": RECEIPT_FORMAT,
                "status": "complete",
                "phase": "complete",
                "operation": "restore",
                "read_only": False,
                "target": original["target"],
                "baseline_sha256": baseline_hash,
                "scope": "historical-restore",
                "current_observation_sha256": current_hash,
                "expected_output_count": len(VALUES),
                "settings_window": {"title": "Settings"},
                "observed_elements_before": original["bounds"]["elements_observed"],
                "observed_elements_after": original["bounds"]["elements_observed"],
                "observed_windows_before": original["bounds"]["window_count"],
                "observed_windows_after": original["bounds"]["window_count"],
                "observed_values": observed_values,
                "actions": actions,
                "manual_inspection_required": False,
                "error": None,
            }
            commands = []

            def native_call(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps(native_report),
                    stderr="",
                )

            with (
                patch(
                    "codex_devtools.live_midi_sync_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_midi_sync_control.subprocess.run",
                    side_effect=native_call,
                ),
            ):
                report = run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="restore",
                    baseline_path=baseline_path,
                    historical_current_path=current_path,
                    output_path=receipt_path,
                    execute=True,
                )
            command = commands[0]
            self.assertIn("--historical-current-inventory", command)
            current_flag = command.index("--historical-current-inventory")
            self.assertEqual(command[current_flag + 1], str(current_path))
            hash_flag = command.index("--historical-current-sha256")
            self.assertEqual(command[hash_flag + 1], current_hash)
            self.assertEqual(report["baseline_sha256"], baseline_hash)
            self.assertEqual(report["current_observation_sha256"], current_hash)
            self.assertEqual(baseline_path.read_bytes(), original_bytes)
            self.assertEqual(current_path.read_bytes(), current_bytes)
            bad_hash = dict(native_report, current_observation_sha256="0" * 64)
            self.assertFalse(
                _native_receipt_valid(
                    bad_hash,
                    operation="restore",
                    baseline=original,
                    baseline_hash=baseline_hash,
                    current_hash=current_hash,
                    returncode=0,
                )
            )

    def test_historical_current_must_be_fresh_and_legacy_isolate_is_denied(self):
        original, current = historical_pair()
        stale = json.loads(json.dumps(current))
        stale["observed_at_utc"] = (
            (datetime.now(timezone.utc) - timedelta(minutes=6))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_path = root / "original.json"
            current_path = root / "stale-current.json"
            receipt_path = root / "receipt.json"
            baseline_path.write_text(json.dumps(original), encoding="utf-8")
            current_path.write_text(json.dumps(stale), encoding="utf-8")
            with (
                patch(
                    "codex_devtools.live_midi_sync_control.platform.system",
                    return_value="Darwin",
                ),
                patch("codex_devtools.live_midi_sync_control.subprocess.run") as native,
            ):
                report = run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="restore",
                    baseline_path=baseline_path,
                    historical_current_path=current_path,
                    output_path=receipt_path,
                    execute=True,
                )
            self.assertEqual(report["phase"], "baseline-validation")
            self.assertIn("freshness window", report["error"])
            native.assert_not_called()
            with self.assertRaisesRegex(ValueError, "only for restore"):
                run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="isolate",
                    baseline_path=baseline_path,
                    historical_current_path=current_path,
                    output_path=root / "other-receipt.json",
                    execute=True,
                )

    def test_historical_files_are_rechecked_after_pending_receipt_before_native_launch(
        self,
    ):
        original, current = historical_pair()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline_path = root / "original.json"
            current_path = root / "current.json"
            receipt_path = root / "receipt.json"
            baseline_path.write_text(json.dumps(original), encoding="utf-8")
            current_path.write_text(json.dumps(current), encoding="utf-8")
            from codex_devtools import live_midi_sync_control as control

            replace_receipt = control._replace_owned_json

            def mutate_after_pending(path, identity, report):
                result = replace_receipt(path, identity, report)
                if report.get("phase") == "native-helper-pending":
                    current_path.write_text(
                        json.dumps(current, indent=2), encoding="utf-8"
                    )
                return result

            with (
                patch(
                    "codex_devtools.live_midi_sync_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_midi_sync_control._replace_owned_json",
                    side_effect=mutate_after_pending,
                ),
                patch("codex_devtools.live_midi_sync_control.subprocess.run") as native,
            ):
                report = run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="restore",
                    baseline_path=baseline_path,
                    historical_current_path=current_path,
                    output_path=receipt_path,
                    execute=True,
                )
            self.assertEqual(report["phase"], "historical-current-revalidation")
            self.assertIn("changed after wrapper validation", report["error"])
            self.assertFalse(report["manual_inspection_required"])
            self.assertTrue(all(not row["attempted"] for row in report["actions"]))
            native.assert_not_called()

    def test_native_completion_receipt_requires_exact_attempt_and_readback_rows(self):
        observed = baseline()
        hash_value = "a" * 64
        report = {
            "format": RECEIPT_FORMAT,
            "status": "complete",
            "phase": "complete",
            "operation": "isolate",
            "read_only": False,
            "target": {
                "pid": 58198,
                "bundle_identifier": "com.ableton.live",
                "process_name": "Live",
            },
            "baseline_sha256": hash_value,
            "expected_output_count": 3,
            "settings_window": {"title": "Settings"},
            "observed_elements_before": 240,
            "observed_elements_after": 240,
            "observed_windows_before": 2,
            "observed_windows_after": 2,
            "observed_values": {"A": 0, "B (Port 1)": 0, "C": 0},
            "manual_inspection_required": False,
            "actions": [
                {
                    "name": "A",
                    "description": "A: MIDI Output: Sync",
                    "baseline_value": 1,
                    "desired_value": 0,
                    "attempted": True,
                    "outcome": "known",
                    "actual_value": 0,
                    "prepress_elements": 240,
                    "prepress_windows": 2,
                },
                {
                    "name": "B (Port 1)",
                    "description": "B (Port 1): MIDI Output: Sync",
                    "baseline_value": 0,
                    "desired_value": 0,
                    "attempted": False,
                    "outcome": "not_attempted",
                    "actual_value": 0,
                },
                {
                    "name": "C",
                    "description": "C: MIDI Output: Sync",
                    "baseline_value": 1,
                    "desired_value": 0,
                    "attempted": True,
                    "outcome": "known",
                    "actual_value": 0,
                    "prepress_elements": 240,
                    "prepress_windows": 2,
                },
            ],
        }
        self.assertTrue(
            _native_receipt_valid(
                report,
                operation="isolate",
                baseline=observed,
                baseline_hash=hash_value,
                returncode=0,
            )
        )
        report["actions"][0]["actual_value"] = 1
        self.assertFalse(
            _native_receipt_valid(
                report,
                operation="isolate",
                baseline=observed,
                baseline_hash=hash_value,
                returncode=0,
            )
        )
        report["actions"][0]["actual_value"] = 0
        report["actions"][0].pop("prepress_elements")
        self.assertFalse(
            _native_receipt_valid(
                report,
                operation="isolate",
                baseline=observed,
                baseline_hash=hash_value,
                returncode=0,
            )
        )

    def test_invalid_baseline_saves_not_attempted_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline_path = Path(temporary) / "baseline.json"
            receipt_path = Path(temporary) / "receipt.json"
            baseline_path.write_text("{}", encoding="utf-8")
            with patch(
                "codex_devtools.live_midi_sync_control.platform.system",
                return_value="Darwin",
            ):
                report = run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="restore",
                    baseline_path=baseline_path,
                    output_path=receipt_path,
                    execute=True,
                )
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(report, saved)
        self.assertEqual(saved["status"], "stopped")
        self.assertFalse(saved["manual_inspection_required"])
        self.assertEqual(saved["actions"], [])

    def test_execute_passes_selected_baseline_age_to_native_helper(self):
        observed = baseline()
        with tempfile.TemporaryDirectory() as temporary:
            baseline_path = Path(temporary) / "baseline.json"
            receipt_path = Path(temporary) / "receipt.json"
            baseline_path.write_text(json.dumps(observed), encoding="utf-8")
            native_result = subprocess.CompletedProcess(
                args=["swift"], returncode=1, stdout="not JSON", stderr=""
            )
            with (
                patch(
                    "codex_devtools.live_midi_sync_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_midi_sync_control.subprocess.run",
                    return_value=native_result,
                ) as native_run,
            ):
                report = run_live_midi_sync(
                    58198,
                    expected_bundle_id="com.ableton.live",
                    expected_count=3,
                    action="isolate",
                    baseline_path=baseline_path,
                    output_path=receipt_path,
                    execute=True,
                    max_baseline_age_seconds=27.5,
                )
            command = native_run.call_args.args[0]
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertIn("--max-baseline-age-seconds", command)
        flag_index = command.index("--max-baseline-age-seconds")
        self.assertEqual(command[flag_index + 1], "27.5")
        self.assertEqual(report, saved)
        self.assertTrue(saved["manual_inspection_required"])

    def test_swift_reenumerates_exact_inventory_and_checks_age_before_every_press(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_midi_sync.swift"
        ).read_text(encoding="utf-8")
        action_loop = source.split("for name in candidates {", 1)[1].split(
            'phase = "final-readback"', 1
        )[0]
        inventory = source.split("func matchingOutputs", 1)[1].split(
            "func validateBaselineFreshness", 1
        )[0]
        self.assertIn("role == (kAXCheckBoxRole as String)", inventory)
        self.assertIn("try enabled(element)", inventory)
        self.assertIn("try checkboxValue(element)", inventory)
        self.assertIn("matchingOutputs(app)", action_loop)
        self.assertIn("Set(freshControls.keys) == Set(original.keys)", action_loop)
        self.assertIn("CFEqual(fresh.element, previous.element)", action_loop)
        self.assertIn("fresh.description == descriptions[rowName]", action_loop)
        self.assertIn("fresh.value == current[rowName]", action_loop)
        self.assertIn("checkboxValue(freshControls[rowName]!.element)", action_loop)
        self.assertIn(
            "validateBaselineFreshness(historicalDate ?? baselineDate)", action_loop
        )
        press_index = action_loop.index("AXUIElementPerformAction(freshControl.element")
        self.assertLess(
            action_loop.rindex(
                "validateBaselineFreshness(historicalDate ?? baselineDate)"
            ),
            press_index,
        )
        self.assertIn("result.maximumBaselineAgeSeconds = parsed", source)
        self.assertIn("try validateBaselineFreshness(date)", source)

    def test_cli_keeps_subcommand_and_operation_arguments_distinct(self):
        args = build_parser().parse_args(
            [
                "ax",
                "live-midi-sync",
                "--pid",
                "58198",
                "--expected-bundle-id",
                "com.ableton.live",
                "--expected-count",
                "3",
                "--execute",
                "--action",
                "isolate",
                "--baseline",
                "/private/tmp/base.json",
                "--receipt",
                "/private/tmp/receipt.json",
            ]
        )
        self.assertEqual(args.action, "live-midi-sync")
        self.assertEqual(args.operation, "isolate")

    def test_cli_historical_inventory_is_restore_only(self):
        base_args = [
            "ax",
            "live-midi-sync",
            "--pid",
            "58198",
            "--expected-bundle-id",
            "com.ableton.live",
            "--expected-count",
            "3",
            "--execute",
            "--baseline",
            "/tmp/original-v2.json",
            "--historical-current-inventory",
            "/tmp/current-v2.json",
            "--receipt",
            "/tmp/restore.json",
            "--action",
            "restore",
        ]
        parsed = build_parser().parse_args(base_args)
        self.assertEqual(
            parsed.historical_current_inventory, Path("/tmp/current-v2.json")
        )
        base_args[base_args.index("restore")] = "isolate"
        stderr = io.StringIO()
        from codex_devtools.cli import main

        with redirect_stdout(io.StringIO()), patch("sys.stderr", stderr):
            status = main(base_args)
        self.assertEqual(status, 2)
        self.assertIn("only with --action restore", stderr.getvalue())

    def test_cli_default_inspect_is_read_only_and_can_write_new_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "baseline.json"
            output = io.StringIO()
            with (
                patch(
                    "codex_devtools.live_midi_sync_control.platform.system",
                    return_value="Darwin",
                ),
                patch("codex_devtools.ax_snapshot.snapshot", return_value=snapshot()),
                redirect_stdout(output),
            ):
                result = build_parser()
                args = result.parse_args(
                    [
                        "ax",
                        "live-midi-sync",
                        "--pid",
                        "58198",
                        "--expected-bundle-id",
                        "com.ableton.live",
                        "--expected-count",
                        "3",
                        "--out",
                        str(output_path),
                    ]
                )
                from codex_devtools.cli import main

                status = main(
                    [
                        "ax",
                        "live-midi-sync",
                        "--pid",
                        "58198",
                        "--expected-bundle-id",
                        "com.ableton.live",
                        "--expected-count",
                        "3",
                        "--out",
                        str(output_path),
                    ]
                )
                saved = json.loads(output_path.read_text(encoding="utf-8"))
        printed = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(args.action, "live-midi-sync")
        self.assertEqual(saved["status"], "observed")
        self.assertTrue(saved["read_only"])
        self.assertEqual(printed, saved)


class LiveMidiSyncNativeHistoricalPolicyTests(unittest.TestCase):
    def test_native_restore_arguments_and_each_press_revalidate_full_inventory(self):
        source = (
            Path(__file__).resolve().parents[1] / "codex_devtools/live_midi_sync.swift"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'guard result.operation == "restore", result.historicalCurrentHash.count == 64',
            source,
        )
        self.assertIn("options.historicalCurrentHash", source)
        self.assertIn("requirePinnedSHA256(", source)
        self.assertIn("currentDate.timeIntervalSince(baselineDate) >= -5", source)
        action_loop = source.split("for name in candidates {", 1)[1].split(
            'phase = "final-readback"', 1
        )[0]
        self.assertIn(
            "inputInventory(app, validateInputGroups: false, completeSyncInventory: true)",
            action_loop,
        )
        self.assertIn("compareSyncLiveInventory(", action_loop)
        self.assertIn(
            "validateBaselineFreshness(historicalDate ?? baselineDate)", action_loop
        )

    @unittest.skipUnless(
        Path("/usr/bin/swift").exists(),
        "Swift source-policy execution requires macOS toolchain",
    )
    def test_actual_native_historical_comparison_policy_without_ax_execution(self):
        original, current = historical_pair()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "inventory.json"
            fixture.write_text(json.dumps({"original": original, "current": current}))
            source = (
                Path(__file__).resolve().parents[1]
                / "codex_devtools/live_midi_sync.swift"
            ).read_text(encoding="utf-8")
            source = source.split("\ndo {\n    options = try parseOptions()", 1)[0]
            harness = root / "sync-policy.swift"
            harness.write_text(
                source
                + r"""
options.expectedCount = 3
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let fixture = try JSONSerialization.jsonObject(with: data) as! [String: Any]
let original = fixture["original"] as! [String: Any]
let current = fixture["current"] as! [String: Any]
let values = try historicalSyncRestoreValues(original, current)
let originalRows = try syncCheckboxRows(original, expectedCount: 3)
let firstOutput = originalRows.first { ($0["description"] as? String) == "A: MIDI Output: Sync" }!
precondition(values[firstOutput["identity"] as! String] == 0)
var indexDrift = current
var indexBounds = indexDrift["bounds"] as! [String: Any]
indexBounds["window_count"] = 2
indexBounds["windows_observed"] = 2
indexDrift["bounds"] = indexBounds
var originalIndexBounds = original["bounds"] as! [String: Any]
originalIndexBounds["window_count"] = 2
originalIndexBounds["windows_observed"] = 2
var originalWithTwoWindows = original
originalWithTwoWindows["bounds"] = originalIndexBounds
var indexWindow = indexDrift["settings_window"] as! [String: Any]
indexWindow["index"] = 1
indexDrift["settings_window"] = indexWindow
_ = try historicalSyncRestoreValues(originalWithTwoWindows, indexDrift)
var invalidWindow = indexDrift
var invalidWindowFields = invalidWindow["settings_window"] as! [String: Any]
invalidWindowFields["index"] = 2
invalidWindow["settings_window"] = invalidWindowFields
var invalidIndexRejected = false
do { _ = try historicalSyncRestoreValues(originalWithTwoWindows, invalidWindow) }
catch { invalidIndexRejected = true }
precondition(invalidIndexRejected, "out-of-range Settings index must fail closed")
invalidWindow = indexDrift
invalidWindowFields = invalidWindow["settings_window"] as! [String: Any]
invalidWindowFields["title"] = "Other"
invalidWindow["settings_window"] = invalidWindowFields
var invalidTitleRejected = false
do { _ = try historicalSyncRestoreValues(originalWithTwoWindows, invalidWindow) }
catch { invalidTitleRejected = true }
precondition(invalidTitleRejected, "Settings title change must fail closed")
func nativeShape(_ inventory: [String: Any]) -> [String: Any] {
    ["target": inventory["target"]!,
     "settings_window": inventory["settings_window"]!,
     "bounds": inventory["bounds"]!,
     "checkboxes": inventory["checkboxes"]!]
}
var originalCountDrift = original
var originalCountBounds = originalCountDrift["bounds"] as! [String: Any]
originalCountBounds["window_count"] = 4
originalCountBounds["windows_observed"] = 4
originalCountDrift["bounds"] = originalCountBounds
var currentCountDrift = current
var currentCountBounds = currentCountDrift["bounds"] as! [String: Any]
currentCountBounds["window_count"] = 2
currentCountBounds["windows_observed"] = 2
currentCountDrift["bounds"] = currentCountBounds
let countDriftValues = try historicalSyncRestoreValues(originalCountDrift, currentCountDrift)
let nativeCountComparison = try compareSyncInventoryValues(
    nativeShape(originalCountDrift), nativeShape(currentCountDrift), values: countDriftValues
)
precondition(nativeCountComparison.count == originalRows.count,
             "total window count drift 4 to 2 must preserve complete Sync identity")
func countDriftRejects(_ inventory: [String: Any]) -> Bool {
    do { _ = try historicalSyncRestoreValues(originalCountDrift, inventory); return false }
    catch { return true }
}
for (field, value): (String, Any) in [
    ("window_count", 0), ("window_count", 65), ("window_count", true),
    ("window_count", 2.0), ("windows_observed", 3), ("windows_observed", true),
    ("windows_observed", 2.0), ("windows_truncated", true),
    ("elements_observed", (currentCountBounds["elements_observed"] as! Int) + 1),
] {
    var invalid = currentCountDrift
    var bounds = invalid["bounds"] as! [String: Any]
    bounds[field] = value
    invalid["bounds"] = bounds
    precondition(countDriftRejects(invalid), "invalid or changed bound \(field) must stop")
    var invalidNativeRejected = false
    do {
        _ = try compareSyncInventoryValues(
            nativeShape(originalCountDrift), nativeShape(invalid), values: countDriftValues
        )
    } catch { invalidNativeRejected = true }
    precondition(invalidNativeRejected, "native invalid or changed bound \(field) must stop")
}
let live = nativeShape(current)
let compared = try compareSyncInventoryValues(current, live, values: values)
precondition(compared.count == originalRows.count, "Python v2 baseline must match native-shaped live inventory")
let twoWindowReference = nativeShape(originalWithTwoWindows)
let twoWindowLive = nativeShape(indexDrift)
let indexDriftValues = try historicalSyncRestoreValues(originalWithTwoWindows, indexDrift)
_ = try compareSyncInventoryValues(twoWindowReference, twoWindowLive, values: indexDriftValues)
func compareRejects(_ candidate: [String: Any]) -> Bool {
    do { _ = try compareSyncInventoryValues(current, candidate, values: values); return false }
    catch { return true }
}
var liveChanged = live
var liveTarget = liveChanged["target"] as! [String: Any]
liveTarget["launch_date"] = "2026-11-07T12:00:02.000Z"
liveChanged["target"] = liveTarget
precondition(compareRejects(liveChanged), "native process generation drift must stop")
liveChanged = live
var liveWindow = liveChanged["settings_window"] as! [String: Any]
liveWindow["identity"] = "other-window"
liveChanged["settings_window"] = liveWindow
precondition(compareRejects(liveChanged), "native Settings identity drift must stop")
liveChanged = live
liveWindow = liveChanged["settings_window"] as! [String: Any]
liveWindow["title"] = "Other"
liveChanged["settings_window"] = liveWindow
precondition(compareRejects(liveChanged), "native Settings title drift must stop")
liveChanged = live
liveWindow = liveChanged["settings_window"] as! [String: Any]
liveWindow["index"] = 1
liveChanged["settings_window"] = liveWindow
var indexRangeBounds = liveChanged["bounds"] as! [String: Any]
indexRangeBounds["window_count"] = 2
indexRangeBounds["windows_observed"] = 2
liveChanged["bounds"] = indexRangeBounds
var indexRangeReference = live
var referenceRangeBounds = indexRangeReference["bounds"] as! [String: Any]
referenceRangeBounds["window_count"] = 2
referenceRangeBounds["windows_observed"] = 2
indexRangeReference["bounds"] = referenceRangeBounds
let indexRangeValues = try historicalSyncRestoreValues(originalWithTwoWindows, indexDrift)
let indexRangeCompared = try compareSyncInventoryValues(indexRangeReference, liveChanged, values: indexRangeValues)
precondition(indexRangeCompared.count == originalRows.count,
             "native Settings index drift must preserve the identified complete inventory")
liveChanged = live
var liveRows = liveChanged["checkboxes"] as! [[String: Any]]
liveRows.removeLast()
liveChanged["checkboxes"] = liveRows
precondition(compareRejects(liveChanged), "native checkbox keyset drift must stop")
liveChanged = live
liveRows = liveChanged["checkboxes"] as! [[String: Any]]
liveRows[liveRows.count - 1]["value"] = 0
liveChanged["checkboxes"] = liveRows
precondition(compareRejects(liveChanged), "native non-Sync drift must stop")
liveChanged = live
liveRows = liveChanged["checkboxes"] as! [[String: Any]]
let offID = (current["outputs"] as! [[String: Any]]).first { ($0["name"] as? String) == "B (Port 1)" }!["identity"] as! String
let offCheckboxIndex = liveRows.firstIndex { ($0["identity"] as? String) == offID }!
liveRows[offCheckboxIndex]["value"] = 1
liveChanged["checkboxes"] = liveRows
precondition(compareRejects(liveChanged), "native originally-off output change must stop")
let pinned = Data([1, 2, 3, 4])
let pinnedHash = SHA256.hash(data: pinned).map { String(format: "%02x", $0) }.joined()
try requirePinnedSHA256(pinned, expected: pinnedHash, label: "fixture")
var tamperedBytes = pinned
tamperedBytes.append(5)
var hashRejected = false
do { _ = try requirePinnedSHA256(tamperedBytes, expected: pinnedHash, label: "fixture") }
catch { hashRejected = true }
precondition(hashRejected, "tampered current bytes must fail exact hash binding")
func rejects(_ value: [String: Any]) -> Bool {
    do { _ = try historicalSyncRestoreValues(original, value); return false }
    catch { return true }
}
var changed = current
var target = changed["target"] as! [String: Any]
target["launch_date"] = "2026-11-07T12:00:02.000Z"
changed["target"] = target
precondition(rejects(changed), "process generation drift must stop")
changed = current
var window = changed["settings_window"] as! [String: Any]
window["identity"] = "other-window"
changed["settings_window"] = window
precondition(rejects(changed), "Settings identity drift must stop")
changed = current
window = changed["settings_window"] as! [String: Any]
window["title"] = "Other"
changed["settings_window"] = window
precondition(rejects(changed), "Settings title drift must stop")
changed = current
window = changed["settings_window"] as! [String: Any]
window["index"] = true
changed["settings_window"] = window
precondition(rejects(changed), "invalid Settings index type must stop")
changed = current
var rows = changed["checkboxes"] as! [[String: Any]]
rows.removeLast()
changed["checkboxes"] = rows
precondition(rejects(changed), "checkbox keyset drift must stop")
changed = current
rows = changed["checkboxes"] as! [[String: Any]]
rows[rows.count - 1]["value"] = 0
changed["checkboxes"] = rows
precondition(rejects(changed), "non-Sync drift must stop")
changed = current
var outputs = changed["outputs"] as! [[String: Any]]
let offIndex = outputs.firstIndex { ($0["name"] as? String) == "B (Port 1)" }!
outputs[offIndex]["value"] = 1
changed["outputs"] = outputs
rows = changed["checkboxes"] as! [[String: Any]]
let offIdentity = outputs[offIndex]["identity"] as! String
let checkboxIndex = rows.firstIndex { ($0["identity"] as? String) == offIdentity }!
rows[checkboxIndex]["value"] = 1
changed["checkboxes"] = rows
precondition(rejects(changed), "originally-off Sync changing on must stop")
changed = current
changed["format"] = syncFormatV1
changed.removeValue(forKey: "checkboxes")
precondition(rejects(changed), "legacy v1 cannot authorize historical restore")
var staleRejected = false
do { try validateBaselineFreshness(Date().addingTimeInterval(-301)) }
catch { staleRejected = true }
precondition(staleRejected, "stale current inventory must stop")
print("native historical policy passed; no AX executed")
"""
            )
            result = subprocess.run(
                ["/usr/bin/swift", str(harness), str(fixture)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no AX executed", result.stdout)


if __name__ == "__main__":
    unittest.main()
