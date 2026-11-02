import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from codex_devtools.cli import build_parser, main
from codex_devtools.live_transport_control import (
    FORMAT,
    _valid_report,
    run_live_transport,
)

PID = 58198
BUNDLE = "com.ableton.live"


def baseline(values=None):
    names = [f"Output {index:02d}" for index in range(21)]
    values = values or {name: 0 for name in names}
    return {
        "format": "codex-devtools-live-midi-sync-baseline-v1",
        "status": "observed",
        "read_only": True,
        "observed_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "target": {
            "pid": PID,
            "process_name": "Live",
            "bundle_identifier": BUNDLE,
        },
        "settings_window": {"title": "Settings", "index": 0},
        "expected_output_count": 21,
        "bounds": {
            "elements_observed": 120,
            "max_depth": 12,
            "max_elements": 1500,
            "max_windows": 64,
            "truncated": False,
        },
        "outputs": [
            {
                "name": name,
                "description": f"{name}: MIDI Output: Sync",
                "role": "AXCheckBox",
                "enabled": True,
                "value": values[name],
            }
            for name in names
        ],
    }


def transport_report(path, action, *, seconds=None, status="complete"):
    if action == "inspect":
        return {
            "format": FORMAT,
            "status": "observed",
            "phase": "complete",
            "action": "inspect",
            "read_only": True,
            "target": {
                "pid": PID,
                "bundle_identifier": BUNDLE,
                "process_name": "Live",
            },
            "set_url": path.as_uri(),
            "set_window_title": path.stem,
            "transport": {
                "play": 0,
                "link": 0,
                "arrangement_record": 0,
                "session_record": 0,
            },
            "actions": {},
            "manual_inspection_required": False,
        }
    rows = {
        "settings_close": {"attempted": True, "outcome": "known"},
        "play": {"attempted": True, "outcome": "known"},
        "stop": {"attempted": True, "outcome": "known"},
    }
    if action == "stop":
        rows = {
            "settings_close": {"attempted": False, "outcome": "not_attempted"},
            "play": {"attempted": False, "outcome": "not_attempted"},
            "stop": {"attempted": False, "outcome": "verified_noop"},
        }
    return {
        "format": FORMAT,
        "status": status,
        "phase": "complete" if status == "complete" else "stop",
        "action": action,
        "read_only": False,
        "target": {
            "pid": PID,
            "bundle_identifier": BUNDLE,
            "process_name": "Live",
        },
        "set_url": path.as_uri(),
        "set_window_title": path.stem,
        "transport": {
            "play": 0,
            "link": 0,
            "arrangement_record": 0,
            "session_record": 0,
        },
        "play_for_seconds": seconds,
        "actions": rows,
        "manual_inspection_required": False,
    }


class LiveTransportControlTests(unittest.TestCase):
    def test_parser_keeps_command_and_operation_separate_and_defaults_to_inspect(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "ax",
                "live-transport",
                "--pid",
                str(PID),
                "--expected-bundle-id",
                BUNDLE,
                "--set",
                "/private/tmp/recall.als",
            ]
        )
        self.assertEqual(args.action, "live-transport")
        self.assertIsNone(args.operation)
        explicit = parser.parse_args(
            [
                "ax",
                "live-transport",
                "--pid",
                str(PID),
                "--expected-bundle-id",
                BUNDLE,
                "--set",
                "/private/tmp/recall.als",
                "--execute",
                "--action",
                "play",
                "--play-for-seconds",
                "3",
                "--expected-output-count",
                "21",
                "--receipt",
                "/private/tmp/transport.json",
            ]
        )
        self.assertEqual(explicit.action, "live-transport")
        self.assertEqual(explicit.operation, "play")

    def test_cli_routes_default_inspection_to_transport_handler(self):
        output = io.StringIO()
        with (
            patch(
                "codex_devtools.live_transport_control.run_live_transport",
                return_value={"status": "observed", "read_only": True},
            ) as run,
            redirect_stdout(output),
        ):
            code = main(
                [
                    "ax",
                    "live-transport",
                    "--pid",
                    str(PID),
                    "--expected-bundle-id",
                    BUNDLE,
                    "--set",
                    "/private/tmp/recall.als",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "observed")
        self.assertIsNone(run.call_args.kwargs["action"])
        self.assertFalse(run.call_args.kwargs["execute"])

    def test_cli_rejects_action_without_execute(self):
        with (
            patch("codex_devtools.live_transport_control.run_live_transport") as run,
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            code = main(
                [
                    "ax",
                    "live-transport",
                    "--pid",
                    str(PID),
                    "--expected-bundle-id",
                    BUNDLE,
                    "--set",
                    "/private/tmp/recall.als",
                    "--action",
                    "stop",
                ]
            )
        run.assert_not_called()
        self.assertEqual(code, 2)
        self.assertIn("--action requires --execute", stderr.getvalue())

    def test_default_inspect_is_read_only_and_exact_set_scoped(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            set_path = Path(temporary) / "recall.als"
            set_path.write_bytes(b"test Set")
            native = transport_report(set_path, "inspect")
            events = []
            completed = subprocess.CompletedProcess(
                args=["native"], returncode=0, stdout=json.dumps(native), stderr=""
            )
            with (
                patch(
                    "codex_devtools.live_transport_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_transport_control._compile_helper",
                    side_effect=lambda *_args: (
                        events.append("compile")
                        or subprocess.CompletedProcess(["swiftc"], 0, "", "")
                    ),
                ),
                patch(
                    "codex_devtools.live_transport_control.subprocess.run",
                    side_effect=lambda *_args, **_kwargs: (
                        events.append("inspect") or completed
                    ),
                ),
            ):
                result = run_live_transport(
                    PID, expected_bundle_id=BUNDLE, set_path=set_path
                )
        self.assertEqual(events, ["compile", "inspect"])
        self.assertTrue(result["read_only"])
        self.assertEqual(result["set_url"], set_path.as_uri())
        self.assertEqual(result["status"], "observed")

    def test_play_compiles_before_fresh_sync_check_and_native_helper(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            receipt_path = root / "play.json"
            events = []
            native_commands = []
            native = transport_report(set_path, "play", seconds=3)
            completed = subprocess.CompletedProcess(
                args=["native"], returncode=0, stdout=json.dumps(native), stderr=""
            )
            with (
                patch(
                    "codex_devtools.live_transport_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_transport_control._compile_helper",
                    side_effect=lambda *_args: (
                        events.append("compile")
                        or subprocess.CompletedProcess(["swiftc"], 0, "", "")
                    ),
                ),
                patch(
                    "codex_devtools.live_transport_control.run_live_midi_sync",
                    side_effect=lambda *_args, **_kwargs: (
                        events.append("sync-inspect") or baseline()
                    ),
                ),
                patch(
                    "codex_devtools.live_transport_control.subprocess.run",
                    side_effect=lambda *args, **_kwargs: (
                        native_commands.append(args[0])
                        or events.append("native-play-stop")
                        or completed
                    ),
                ),
            ):
                result = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="play",
                    play_for_seconds=3,
                    expected_count=21,
                    receipt_path=receipt_path,
                    execute=True,
                )
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(events, ["compile", "sync-inspect", "native-play-stop"])
        self.assertEqual(result, saved)
        self.assertEqual(result["status"], "complete")
        command = native_commands[0]
        self.assertIn("--play-for-seconds", command)
        self.assertIn("--baseline-sha256", command)
        self.assertIn("--expected-count", command)

    def test_play_refuses_output_sync_on_and_does_not_launch_helper(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            receipt_path = root / "play.json"
            with (
                patch(
                    "codex_devtools.live_transport_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_transport_control._compile_helper",
                    return_value=subprocess.CompletedProcess(["swiftc"], 0, "", ""),
                ),
                patch(
                    "codex_devtools.live_transport_control.run_live_midi_sync",
                    return_value=baseline(
                        {
                            "Output 00": 1,
                            **{f"Output {index:02d}": 0 for index in range(1, 21)},
                        }
                    ),
                ),
                patch(
                    "codex_devtools.live_transport_control.subprocess.run"
                ) as native_run,
            ):
                report = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="play",
                    play_for_seconds=1,
                    expected_count=21,
                    receipt_path=receipt_path,
                    execute=True,
                )
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        native_run.assert_not_called()
        self.assertEqual(report, saved)
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(report["phase"], "output-isolation-preflight")
        self.assertFalse(report["manual_inspection_required"])
        self.assertTrue(all(not row["attempted"] for row in report["actions"].values()))

    def test_compile_failure_precedes_sync_inspection_and_is_not_ambiguous(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            receipt_path = root / "play.json"
            with (
                patch(
                    "codex_devtools.live_transport_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_transport_control._compile_helper",
                    return_value=subprocess.CompletedProcess(
                        ["swiftc"], 1, "", "compile failed"
                    ),
                ),
                patch(
                    "codex_devtools.live_transport_control.run_live_midi_sync"
                ) as sync,
            ):
                report = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="play",
                    play_for_seconds=1,
                    expected_count=21,
                    receipt_path=receipt_path,
                    execute=True,
                )
            sync.assert_not_called()
        self.assertEqual(report["phase"], "helper-compile")
        self.assertFalse(report["manual_inspection_required"])

    def test_native_timeout_requires_manual_transport_inspection(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            receipt_path = root / "stop.json"
            with (
                patch(
                    "codex_devtools.live_transport_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_transport_control._compile_helper",
                    return_value=subprocess.CompletedProcess(["swiftc"], 0, "", ""),
                ),
                patch(
                    "codex_devtools.live_transport_control.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(["native"], 70),
                ),
            ):
                report = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="stop",
                    receipt_path=receipt_path,
                    execute=True,
                )
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(report, saved)
        self.assertTrue(report["manual_inspection_required"])
        self.assertEqual(report["actions"]["stop"]["outcome"], "unknown")

    def test_explicit_stop_uses_exact_set_without_sync_baseline(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            receipt_path = root / "stop.json"
            native = transport_report(set_path, "stop")
            completed = subprocess.CompletedProcess(
                args=["native"], returncode=0, stdout=json.dumps(native), stderr=""
            )
            with (
                patch(
                    "codex_devtools.live_transport_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_transport_control._compile_helper",
                    return_value=subprocess.CompletedProcess(["swiftc"], 0, "", ""),
                ),
                patch(
                    "codex_devtools.live_transport_control.run_live_midi_sync"
                ) as sync,
                patch(
                    "codex_devtools.live_transport_control.subprocess.run",
                    return_value=completed,
                ),
            ):
                report = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="stop",
                    receipt_path=receipt_path,
                    execute=True,
                )
            sync.assert_not_called()
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["actions"]["stop"]["outcome"], "verified_noop")

    def test_play_duration_and_output_count_are_bounded_before_helper_launch(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            for duration, expected_count, message in (
                (0, 21, "duration"),
                (16, 21, "duration"),
                (1, 20, "expected output count 21"),
            ):
                with (
                    patch(
                        "codex_devtools.live_transport_control.platform.system",
                        return_value="Darwin",
                    ),
                    patch(
                        "codex_devtools.live_transport_control._compile_helper"
                    ) as compile_helper,
                    self.assertRaisesRegex(ValueError, message),
                ):
                    run_live_transport(
                        PID,
                        expected_bundle_id=BUNDLE,
                        set_path=set_path,
                        action="play",
                        play_for_seconds=duration,
                        expected_count=expected_count,
                        receipt_path=root / f"bad-{duration}-{expected_count}.json",
                        execute=True,
                    )
                compile_helper.assert_not_called()

    def test_set_symlink_is_refused_before_helper_compile(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            real_set = root / "real.als"
            real_set.write_bytes(b"test Set")
            alias = root / "alias.als"
            alias.symlink_to(real_set)
            with (
                patch(
                    "codex_devtools.live_transport_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_transport_control._compile_helper"
                ) as compile_helper,
                self.assertRaisesRegex(ValueError, "regular file"),
            ):
                run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=alias,
                )
            compile_helper.assert_not_called()

    def test_report_validator_requires_verified_deadline_stop_and_truthful_uncertainty(
        self,
    ):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            set_path = Path(temporary) / "recall.als"
            set_path.write_bytes(b"test Set")
            report = transport_report(set_path, "play", seconds=2)
            kwargs = {
                "action": "play",
                "pid": PID,
                "bundle_id": BUNDLE,
                "set_path": set_path,
                "play_for_seconds": 2,
                "returncode": 0,
            }
            self.assertTrue(_valid_report(report, **kwargs))
            report["transport"]["link"] = 1
            report["manual_inspection_required"] = True
            self.assertTrue(_valid_report(report, **kwargs))
            report["transport"]["link"] = 0
            report["manual_inspection_required"] = False
            report["actions"]["stop"]["outcome"] = "not_attempted"
            report["actions"]["stop"]["attempted"] = False
            self.assertFalse(_valid_report(report, **kwargs))
            report["status"] = "stopped"
            report["error"] = "stop attempt was uncertain"
            report["manual_inspection_required"] = True
            report["actions"]["stop"]["outcome"] = "unknown"
            report["actions"]["stop"]["attempted"] = True
            kwargs["returncode"] = 1
            self.assertTrue(_valid_report(report, **kwargs))

    def test_swift_contract_uses_monotonic_deadline_exact_set_and_settings_baseline(
        self,
    ):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("DispatchTime.now().uptimeNanoseconds", source)
        self.assertIn("Thread.sleep(forTimeInterval:", source)
        self.assertIn(
            'try press(beforeStop["Stop"]!.element, actionName: "stop")', source
        )
        self.assertIn("try checkedBaseline()", source)
        self.assertIn(
            "try validateCurrentOutputs(settingsWindow, baseline: baseline)", source
        )
        self.assertIn(
            'optionalString($0, kAXSubroleAttribute as String) == "AXCloseButton"',
            source,
        )
        self.assertIn("CGWindowListCopyWindowInfo", source)
        self.assertIn("try sameSetURL(document, expected: url)", source)
        self.assertIn('"Transport.Play"', source)
        self.assertIn('"Transport.Stop"', source)
        self.assertIn('"Transport.AbletonLink"', source)
        self.assertIn('"Transport.GlobalRecord"', source)
        self.assertIn('"Transport.SessionRecord"', source)
        self.assertNotIn("AppleScript", source)
        sync_scan = source.index(
            "try validateCurrentOutputs(settings, baseline: baseline)"
        )
        close_button = source.index(
            'optionalString($0, kAXSubroleAttribute as String) == "AXCloseButton"'
        )
        self.assertLess(sync_scan, close_button)
        baseline_freshness = source.index(
            "try validateBaselineAge(observedAt)", sync_scan
        )
        play_press = source.index('try press(playControl, actionName: "play")')
        self.assertLess(baseline_freshness, play_press)
        deadline_wait = source.index("Thread.sleep(forTimeInterval:")
        timed_stop = source.index(
            'try press(beforeStop["Stop"]!.element, actionName: "stop")'
        )
        self.assertLess(deadline_wait, timed_stop)


if __name__ == "__main__":
    unittest.main()
