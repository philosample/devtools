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
            "depth": 0,
            "role": "AXWindow",
            "title": "Settings",
        }
    ]
    elements.extend(
        {
            "index": index,
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
        "windows": [{"index": 0, "title": "Settings"}],
        "trees": [{"window_index": 0, "elements": elements}],
    }


def baseline(values=None):
    return baseline_from_snapshot(
        snapshot(values),
        expected_pid=58198,
        expected_bundle_id="com.ableton.live",
        expected_count=len(VALUES),
    )


class LiveMidiSyncControlTests(unittest.TestCase):
    def test_complete_snapshot_builds_sorted_exact_baseline(self):
        result = baseline()
        self.assertEqual(result["expected_output_count"], 3)
        self.assertEqual([row["name"] for row in result["outputs"]], sorted(VALUES))
        self.assertEqual(
            {row["name"]: row["value"] for row in result["outputs"]}, VALUES
        )
        self.assertTrue(result["read_only"])
        self.assertEqual(result["settings_window"], {"title": "Settings", "index": 0})

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
        malformed_window_reason = valid | {
            "focused_window": valid["focused_window"]
            | {
                "role": {"status": "unavailable", "reason": {}},
            }
        }
        invalid.append(malformed_window_reason)
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
        with self.assertRaisesRegex(ValueError, "duplicate output Sync"):
            baseline_from_snapshot(
                duplicate,
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
        self.assertIn("validateBaselineFreshness(baselineDate)", action_loop)
        press_index = action_loop.index("AXUIElementPerformAction(freshControl.element")
        self.assertLess(
            action_loop.rindex("validateBaselineFreshness(baselineDate)"), press_index
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


if __name__ == "__main__":
    unittest.main()
