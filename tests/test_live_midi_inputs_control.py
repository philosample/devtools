"""Source-only complete input matrices and action orchestration; no native AX calls."""

import copy
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from codex_devtools.cli import build_parser
from codex_devtools.live_midi_inputs_control import (
    FORMAT,
    _native,
    inventory_hash,
    reconcile,
    run_live_midi_inputs,
    transition_plan,
    validate_inventory,
)


def matrix():
    rows = []
    for name, flags in (
        ("IAC Driver (Studio Capture)", (1, 0, 1)),
        ("DIN port 2", (0, 1, 0)),
    ):
        for flag, value in zip(("Track", "Sync", "Remote"), flags, strict=True):
            rows.append(
                {
                    "identity": str(len(rows) + 10),
                    "index": len(rows) + 1,
                    "description": f"{name}: MIDI Input: {flag}",
                    "role": "AXCheckBox",
                    "enabled": True,
                    "value": value,
                }
            )
    for description, value in (
        ("IAC Driver (Studio Capture): MIDI Output: Track", 1),
        ("DIN port 2: MIDI Output: Sync", 1),
        ("Other Settings checkbox", 0),
    ):
        rows.append(
            {
                "identity": str(len(rows) + 10),
                "index": len(rows) + 1,
                "description": description,
                "role": "AXCheckBox",
                "enabled": True,
                "value": value,
            }
        )
    result = {
        "format": FORMAT,
        "status": "observed",
        "read_only": True,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "target": {
            "pid": 123,
            "bundle_identifier": "com.ableton.live",
            "process_name": "Live",
            "launch_date": "2026-11-02T00:00:00Z",
        },
        "bounds": {
            "max_elements": 1500,
            "max_depth": 12,
            "max_windows": 64,
            "truncated": False,
            "elements_observed": 40,
            "windows_observed": 2,
        },
        "settings_window": {"title": "Settings", "identity": "settings-123"},
        "expected_input_count": 2,
        "input_order": ["IAC Driver (Studio Capture)", "DIN port 2"],
        "checkboxes": rows,
    }
    return seal(result)


def seal(value):
    value["inventory_sha256"] = inventory_hash(value)
    return value


def validate(value):
    return validate_inventory(value, pid=123, bundle="com.ableton.live", count=2)


class InventoryTests(unittest.TestCase):
    def test_complete_mixed_inventory_and_readable_disabled_off(self):
        baseline = matrix()
        baseline["checkboxes"][1]["enabled"] = False
        self.assertEqual(
            validate(seal(baseline))["input_order"], baseline["input_order"]
        )
        actions = transition_plan(baseline, baseline, "isolate")
        self.assertEqual([row["identity"] for row in actions], ["10", "12", "14"])

    def test_reject_incomplete_unknown_ambiguous_identity_and_values(self):
        mutations = [
            lambda b: b["checkboxes"].pop(1),
            lambda b: b["checkboxes"][0].update(value=None),
            lambda b: b["checkboxes"][0].update(value=True),
            lambda b: b["checkboxes"][0].update(role="AXButton"),
            lambda b: b["checkboxes"][0].update(enabled=False),
            lambda b: b["checkboxes"][0].update(description="X: MIDI Input: Unknown"),
            lambda b: b["checkboxes"][1].update(identity="10"),
            lambda b: b["checkboxes"][1].update(
                description=b["checkboxes"][0]["description"]
            ),
            lambda b: b["checkboxes"].reverse(),
            lambda b: b["bounds"].update(truncated=True),
            lambda b: b["target"].update(pid=124),
            lambda b: b["target"].update(bundle_identifier="other"),
            lambda b: b.update(input_order=list(reversed(b["input_order"]))),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                baseline = matrix()
                mutation(baseline)
                with self.assertRaises(ValueError):
                    validate(seal(baseline))

    def test_hash_and_cross_schema_rejected(self):
        baseline = matrix()
        baseline["checkboxes"][0]["value"] = 0
        with self.assertRaises(ValueError):
            validate(baseline)
        baseline = matrix()
        baseline["format"] = "codex-devtools-live-midi-sync-baseline-v1"
        with self.assertRaises(ValueError):
            validate(seal(baseline))

    def test_exact_restore_accepts_partial_isolation_only(self):
        baseline = matrix()
        current = copy.deepcopy(baseline)
        current["checkboxes"][0]["value"] = 0
        self.assertEqual(len(transition_plan(baseline, seal(current), "restore")), 1)
        with self.assertRaises(ValueError):
            transition_plan(baseline, current, "isolate")
        current["checkboxes"][1]["value"] = 1
        with self.assertRaises(ValueError):
            transition_plan(baseline, current, "restore")

    def test_protect_output_other_checkboxes_order_and_process_launch(self):
        baseline = matrix()
        expected = {row["identity"]: row["value"] for row in baseline["checkboxes"]}
        for mutation in (
            lambda b: b["checkboxes"][7].update(value=0),
            lambda b: b["checkboxes"][8].update(value=1),
            lambda b: b["target"].update(launch_date="new process"),
            lambda b: b["checkboxes"][0].update(identity="replacement"),
            lambda b: b["settings_window"].update(identity="replacement"),
        ):
            current = copy.deepcopy(baseline)
            mutation(current)
            with self.assertRaises(ValueError):
                reconcile(baseline, seal(current), expected)


class ActionTests(unittest.TestCase):
    def run_action(self, directory, baseline, state, *, action="isolate", fault=None):
        path = Path(directory) / "baseline.json"
        path.write_text(json.dumps(baseline))
        receipt = Path(directory) / f"{action}.json"
        calls = []

        def native(pid, bundle, count, **kwargs):
            calls.append(kwargs)
            if kwargs.get("action"):
                row = kwargs["action"]
                self.assertEqual(kwargs["expected"]["checkboxes"], state["checkboxes"])
                if fault:
                    fault(state, len([call for call in calls if call.get("action")]))
                for control in state["checkboxes"]:
                    if control["identity"] == row["identity"]:
                        control["value"] = row["desired_value"]
            return seal(copy.deepcopy(state))

        with (
            patch(
                "codex_devtools.live_midi_inputs_control.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_midi_inputs_control._native", side_effect=native
            ),
        ):
            report = run_live_midi_inputs(
                123,
                expected_bundle_id="com.ableton.live",
                expected_count=2,
                execute=True,
                action=action,
                baseline_path=path,
                output_path=receipt,
            )
        self.assertEqual(json.loads(receipt.read_text()), report)
        return report, calls

    def test_isolate_restore_all_three_flags_and_unrelated_unchanged(self):
        baseline = matrix()
        state = copy.deepcopy(baseline)
        with tempfile.TemporaryDirectory() as directory:
            isolated, calls = self.run_action(directory, baseline, state)
            self.assertEqual(isolated["status"], "complete")
            self.assertEqual(len([call for call in calls if call.get("action")]), 3)
            self.assertTrue(all(row["value"] == 0 for row in state["checkboxes"][:6]))
            self.assertEqual(state["checkboxes"][6:], baseline["checkboxes"][6:])
            restored, _ = self.run_action(directory, baseline, state, action="restore")
            self.assertEqual(restored["status"], "complete")
            self.assertEqual(state["checkboxes"], baseline["checkboxes"])

    def test_changed_midsequence_stops_without_cleanup_or_retry(self):
        def fault(state, press):
            if press == 2:
                state["checkboxes"][7]["value"] = 0

        with tempfile.TemporaryDirectory() as directory:
            report, calls = self.run_action(directory, matrix(), matrix(), fault=fault)
        self.assertEqual(report["status"], "stopped")
        self.assertTrue(report["manual_inspection_required"])
        self.assertEqual(
            [row["outcome"] for row in report["actions"]],
            ["known", "unknown", "not_attempted"],
        )
        self.assertEqual(len([call for call in calls if call.get("action")]), 2)

    def test_press_error_keeps_unknown_and_explicit_partial_restore(self):
        def fault(state, press):
            if press == 2:
                raise subprocess.TimeoutExpired("native", 1)

        baseline = matrix()
        state = copy.deepcopy(baseline)
        with tempfile.TemporaryDirectory() as directory:
            report, _ = self.run_action(directory, baseline, state, fault=fault)
            self.assertEqual(report["status"], "stopped")
            self.assertTrue(report["manual_inspection_required"])
            restored, calls = self.run_action(
                directory, baseline, state, action="restore"
            )
            self.assertEqual(restored["status"], "complete")
            self.assertEqual(len([call for call in calls if call.get("action")]), 1)
            self.assertEqual(state["checkboxes"], baseline["checkboxes"])

    def test_stale_baseline_stops_before_any_native_action(self):
        baseline = matrix()
        baseline["observed_at_utc"] = (
            datetime.now(timezone.utc) - timedelta(seconds=301)
        ).isoformat()
        seal(baseline)
        with tempfile.TemporaryDirectory() as directory:
            report, calls = self.run_action(directory, baseline, matrix())
        self.assertEqual(report["status"], "stopped")
        self.assertFalse(calls)
        self.assertFalse(report["manual_inspection_required"])

    def test_default_native_and_cli_are_inspection(self):
        args = build_parser().parse_args(
            [
                "ax",
                "live-midi-inputs",
                "--pid",
                "123",
                "--expected-bundle-id",
                "com.ableton.live",
                "--expected-count",
                "2",
            ]
        )
        self.assertFalse(args.execute)
        self.assertIsNone(args.operation)
        with patch(
            "codex_devtools.live_midi_inputs_control.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "{}"),
        ) as run:
            _native(123, "com.ableton.live", 2)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--input-inventory") + 1], "inspect")
        self.assertNotIn("--operation", command)


class NativePolicySourceTests(unittest.TestCase):
    @unittest.skipUnless(
        Path("/usr/bin/swift").exists(),
        "Swift source-policy check requires macOS toolchain",
    )
    def test_actual_swift_comparison_policy_without_ax_execution(self):
        # Execute only the actual pure comparison functions; the native entrypoint
        # is removed, and no AX/NSRunningApplication function is called.
        source = (
            Path(__file__).resolve().parents[1] / "codex_devtools/live_midi_sync.swift"
        ).read_text()
        source = source.split("\ndo {\n    options = try parseOptions()", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "matrix.json"
            fixture.write_text(json.dumps(matrix()))
            harness = root / "policy.swift"
            harness.write_text(
                source
                + r"""
options.expectedCount = 2
let (_, baseline) = try inputJSON(CommandLine.arguments[1])
try compareInputs(baseline, baseline)
var changed = baseline
var rows = try inputRows(changed)
rows[0]["value"] = 0
changed["checkboxes"] = rows
var rejected = false
do { try compareInputs(baseline, changed) } catch { rejected = true }
precondition(rejected, "unexpected input changes must fail")
var permitted: [String: Int] = [:]
for row in try inputRows(baseline) { permitted[row["identity"] as! String] = row["value"] as? Int }
permitted["10"] = 0
try compareInputs(baseline, changed, values: permitted)
rows[7]["value"] = 0
changed["checkboxes"] = rows
rejected = false
do { try compareInputs(baseline, changed, values: permitted) } catch { rejected = true }
precondition(rejected, "unrelated output changes must fail")
rows = try inputRows(baseline)
rows[0]["identity"] = "replacement"
changed = baseline
changed["checkboxes"] = rows
rejected = false
do { try compareInputs(baseline, changed) } catch { rejected = true }
precondition(rejected, "replacement identity must fail")
changed = baseline
changed["format"] = "codex-devtools-live-midi-sync-baseline-v1"
rejected = false
do { _ = try inputRows(changed) } catch { rejected = true }
precondition(rejected, "output schema must fail")
print("native source policy passed; no AX executed")
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
