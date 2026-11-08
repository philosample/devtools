import hashlib
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
    _native_command,
    _valid_focus_report,
    _valid_report,
    run_live_transport,
)

PID = 58198
BUNDLE = "com.ableton.live"


def baseline(values=None, *, count=21):
    names = [f"Output {index:02d}" for index in range(count)]
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
        "expected_output_count": count,
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
        **(
            {
                "transport_before_stop": {
                    "play": 1,
                    "link": 0,
                    "arrangement_record": 0,
                    "session_record": 0,
                }
            }
            if action == "play"
            else {}
        ),
        **(
            {
                "play_readback": "verified",
                "stop_play_readback": "verified",
            }
            if action == "play"
            else {}
        ),
        "play_for_seconds": seconds,
        "actions": rows,
        "manual_inspection_required": False,
    }


class LiveTransportControlTests(unittest.TestCase):
    def test_focus_window_cli_preflight_and_execute_arguments_are_explicit(self):
        parser = build_parser()
        base = [
            "ax",
            "live-transport",
            "--pid",
            str(PID),
            "--expected-bundle-id",
            BUNDLE,
            "--set",
            "/private/tmp/recall.als",
            "--action",
            "focus-window",
            "--expected-generation",
            "123456",
            "--snapshot-observed-at-utc",
            "2026-11-08T12:00:00.000Z",
            "--snapshot-launch-date",
            "2026-11-08T11:00:00.000Z",
        ]
        preflight = parser.parse_args(
            base
            + ["--window-identity", "98765", "--receipt", "/private/tmp/preflight.json"]
        )
        self.assertFalse(preflight.execute)
        self.assertEqual(preflight.operation, "focus-window")
        self.assertEqual(preflight.window_identity, "98765")
        native = _native_command(
            Path("/tmp/helper"),
            pid=PID,
            bundle_id=BUNDLE,
            set_path=Path("/private/tmp/recall.als"),
            action="focus-window",
            play_for_seconds=None,
            expected_count=None,
            baseline_path=None,
            baseline_hash=None,
            window_identity="98765",
            expected_generation=123456,
            snapshot_observed_at_utc="2026-11-08T12:00:00.000Z",
            snapshot_launch_date="2026-11-08T11:00:00.000Z",
        )
        self.assertNotIn("--execute", native)
        self.assertIn("--window-identity", native)
        execute = _native_command(
            Path("/tmp/helper"),
            pid=PID,
            bundle_id=BUNDLE,
            set_path=Path("/private/tmp/recall.als"),
            action="focus-window",
            play_for_seconds=None,
            expected_count=None,
            baseline_path=None,
            baseline_hash=None,
            expected_generation=123456,
            preflight_path=Path("/private/tmp/preflight.json"),
            preflight_hash="a" * 64,
        )
        self.assertIn("--execute", execute)
        self.assertIn("--preflight-sha256", execute)
        self.assertNotIn("--window-identity", execute)

    def test_focus_window_report_is_bound_to_identity_generation_and_set(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            set_path = Path(temporary) / "recall.als"
            set_path.write_bytes(b"test Set")
            report = {
                "format": "codex-devtools-live-focus-window-preflight-v1",
                "status": "preflight_ready",
                "read_only": True,
                "actions": {},
                "target": {
                    "pid": PID,
                    "bundle_identifier": BUNDLE,
                    "process_generation": 123456,
                },
                "set_url": set_path.as_uri(),
                "set_window_identity": "11111",
                "snapshot_observed_at_utc": "2026-11-08T12:00:00.000Z",
                "snapshot_launch_date": "2026-11-08T11:00:00.000Z",
                "window": {
                    "identity": "1685072705",
                    "role": "AXWindow",
                    "subrole": "AXStandardWindow",
                    "title": "TR-1000 Ctrl",
                    "identifier": None,
                    "document": None,
                    "window_number": None,
                    "cg_window_number": 555,
                    "geometry": {"x": 1, "y": 2, "width": 300, "height": 200},
                },
            }
            fields = {
                "execute": False,
                "pid": PID,
                "bundle_id": BUNDLE,
                "set_path": set_path,
                "identity": "1685072705",
                "generation": 123456,
                "preflight_hash": None,
                "returncode": 0,
            }
            self.assertTrue(_valid_focus_report(report, **fields))
            report["target"]["process_generation"] += 1
            self.assertFalse(_valid_focus_report(report, **fields))
            report["target"]["process_generation"] = 123456
            report["window"]["identity"] = "1685072657"
            self.assertFalse(_valid_focus_report(report, **fields))

    def test_focus_window_execution_refuses_changed_preflight_hash_before_native_compile(
        self,
    ):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            preflight = root / "preflight.json"
            preflight.write_text("{}", encoding="utf-8")
            with (
                patch(
                    "codex_devtools.live_transport_control.platform.system",
                    return_value="Darwin",
                ),
                patch(
                    "codex_devtools.live_transport_control._compile_helper"
                ) as compile_helper,
                self.assertRaisesRegex(ValueError, "hash does not match"),
            ):
                run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="focus-window",
                    expected_generation=123456,
                    preflight_receipt_path=preflight,
                    preflight_sha256="0" * 64,
                    receipt_path=root / "result.json",
                    execute=True,
                )
            compile_helper.assert_not_called()

    def test_focus_window_python_caller_runs_hash_pinned_two_stage_contract(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            preflight_path = root / "preflight.json"
            result_path = root / "result.json"
            target = {
                "pid": PID,
                "bundle_identifier": BUNDLE,
                "process_generation": 123456,
            }
            window = {
                "identity": "1685072705",
                "role": "AXWindow",
                "subrole": "AXStandardWindow",
                "title": "TR-1000 Ctrl",
                "identifier": None,
                "document": None,
                "window_number": None,
                "cg_window_number": 555,
                "geometry": {"x": 1, "y": 2, "width": 300, "height": 200},
            }
            preflight = {
                "format": "codex-devtools-live-focus-window-preflight-v1",
                "status": "preflight_ready",
                "read_only": True,
                "actions": {},
                "target": target,
                "set_url": set_path.as_uri(),
                "set_window_identity": "11111",
                "snapshot_observed_at_utc": "2026-11-08T12:00:00.000Z",
                "snapshot_launch_date": "2026-11-08T11:00:00.000Z",
                "window": window,
            }
            complete = {
                "format": "codex-devtools-live-focus-window-result-v1",
                "status": "complete",
                "read_only": False,
                "actions": {"activate": "known", "raise": "known"},
                "target": target,
                "set_url": set_path.as_uri(),
                "preflight_sha256": "",
                "set_window_identity": "11111",
                "window": window,
            }
            results = [
                subprocess.CompletedProcess(["native"], 0, json.dumps(preflight), ""),
                subprocess.CompletedProcess(["native"], 0, json.dumps(complete), ""),
            ]
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
                    side_effect=results,
                ) as native_run,
            ):
                first = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="focus-window",
                    window_identity="1685072705",
                    expected_generation=123456,
                    snapshot_observed_at_utc="2026-11-08T12:00:00.000Z",
                    snapshot_launch_date="2026-11-08T11:00:00.000Z",
                    receipt_path=preflight_path,
                    execute=False,
                )
                digest = hashlib.sha256(preflight_path.read_bytes()).hexdigest()
                complete["preflight_sha256"] = digest
                results[1] = subprocess.CompletedProcess(
                    ["native"], 0, json.dumps(complete), ""
                )
                second = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="focus-window",
                    expected_generation=123456,
                    preflight_receipt_path=preflight_path,
                    preflight_sha256=digest,
                    receipt_path=result_path,
                    execute=True,
                )
            self.assertEqual(first["status"], "preflight_ready")
            self.assertEqual(second["status"], "complete", second)
            self.assertEqual(
                json.loads(result_path.read_text(encoding="utf-8")), second
            )
            self.assertNotIn("--execute", native_run.call_args_list[0].args[0])
            self.assertIn("--execute", native_run.call_args_list[1].args[0])
            self.assertIn(digest, native_run.call_args_list[1].args[0])

    @unittest.skipUnless(Path("/usr/bin/xcrun").exists(), "Swift toolchain required")
    def test_swift_focus_receipt_policy_accepts_only_fresh_exact_target(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        start = source.index("func focusReceiptIsCurrent(")
        end = source.index("\nfunc focusWindowAction(", start)
        function = source[start:end]
        harness = f"""
import Foundation
import ApplicationServices
{function}
let now = Date(timeIntervalSince1970: 1_800_000_000)
let formatter = ISO8601DateFormatter()
formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
let window: [String: Any] = ["identity":"1685072705", "role":"AXWindow", "subrole":"AXStandardWindow", "title":"TR-1000 Ctrl", "identifier":NSNull(), "document":NSNull(), "window_number":NSNull(), "cg_window_number":555, "geometry":["x":1.0,"y":2.0,"width":300.0,"height":200.0]]
let receipt: [String: Any] = ["format":"codex-devtools-live-focus-window-preflight-v1", "status":"preflight_ready", "read_only":true, "observed_at_utc":formatter.string(from: now), "target":["pid":58198,"bundle_identifier":"com.ableton.live","process_generation":123456], "set_url":"file:///tmp/recall.als", "set_window_identity":"11111", "window":window]
precondition(focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072705", currentWindow:window, now:now.addingTimeInterval(30)))
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072705", currentWindow:window, now:now.addingTimeInterval(31)))
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123457, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072705", currentWindow:window, now:now))
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/other.als", setWindowIdentity:"11111", identity:"1685072705", currentWindow:window, now:now))
var changed = window
changed["title"] = "Other"
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072705", currentWindow:changed, now:now))
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"22222", identity:"1685072705", currentWindow:window, now:now))
"""
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            main_swift = root / "main.swift"
            binary = root / "focus-policy"
            main_swift.write_text(harness, encoding="utf-8")
            compiled = subprocess.run(
                [
                    "/usr/bin/xcrun",
                    "swiftc",
                    "-framework",
                    "ApplicationServices",
                    str(main_swift),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, check=False, timeout=5
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(Path("/usr/bin/xcrun").exists(), "Swift toolchain required")
    def test_swift_focus_preflight_producer_matches_python_validator_and_success_status(
        self,
    ):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        start = source.index("func focusWindowPreflightReport(")
        end = source.index("\nfunc focusWindowAction(", start)
        production_contract = source[start:end]
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            set_path = Path(temporary) / "M1-SDKFree-TR-Window592.als"
            set_path.write_bytes(b"selected Set fixture")
            harness = f"""
import Foundation
import ApplicationServices
{production_contract}
let url = {json.dumps(set_path.as_uri())}
let window: [String: Any] = [
  "identity": "1668292321", "role": "AXWindow", "subrole": "AXStandardWindow",
  "title": "M1-SDKFree-TR-Window592", "identifier": NSNull(), "document": url,
  "geometry": ["x": 475.0, "y": 106.0, "width": 1920.0, "height": 1055.0],
  "window_number": NSNull(), "cg_window_number": 31405,
]
let report = focusWindowPreflightReport(
  observedAt: "2026-11-08T08:06:40.094Z",
  snapshotObservedAt: "2026-11-08T08:06:39.869Z",
  snapshotLaunchDate: "2026-11-08T06:16:59.511Z",
  pid: 47314, bundleID: "com.ableton.live", generation: 1794118619511410,
  setURL: url, setWindowIdentity: "1668292321", window: window
)
precondition((report["actions"] as? [String: Any])?.isEmpty == true)
precondition(successfulHelperStatus(report["status"] as? String))
precondition(successfulHelperStatus("observed"))
precondition(successfulHelperStatus("complete"))
precondition(!successfulHelperStatus("stopped"))
let data = try! JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)
"""
            main_swift = Path(temporary) / "main.swift"
            binary = Path(temporary) / "focus-preflight-contract"
            main_swift.write_text(harness, encoding="utf-8")
            compiled = subprocess.run(
                [
                    "/usr/bin/xcrun",
                    "swiftc",
                    "-framework",
                    "ApplicationServices",
                    str(main_swift),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, check=False, timeout=5
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(
            _valid_focus_report(
                report,
                execute=False,
                pid=47314,
                bundle_id="com.ableton.live",
                set_path=set_path,
                identity="1668292321",
                generation=1794118619511410,
                preflight_hash=None,
                returncode=0,
            )
        )

    @unittest.skipUnless(Path("/usr/bin/xcrun").exists(), "Swift toolchain required")
    def test_swift_cg_window_match_disambiguates_duplicate_title_and_size_by_position(
        self,
    ):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        start = source.index("func cgBoundsMatch(")
        end = source.index("\nfunc focusReceiptIsCurrent(", start)
        function = source[start:end]
        main_swift = f"""
import Foundation
{function}
let selected = ["x": 400.0, "y": 200.0, "width": 800.0, "height": 600.0]
let first = ["X": NSNumber(value: 400), "Y": NSNumber(value: 200), "Width": NSNumber(value: 800), "Height": NSNumber(value: 600)]
let duplicateTitleSameSizeElsewhere = ["X": NSNumber(value: 1400), "Y": NSNumber(value: 200), "Width": NSNumber(value: 800), "Height": NSNumber(value: 600)]
precondition(cgBoundsMatch(first, geometry:selected))
precondition(!cgBoundsMatch(duplicateTitleSameSizeElsewhere, geometry:selected))
"""
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            source_path = root / "main.swift"
            binary = root / "geometry-policy"
            source_path.write_text(main_swift, encoding="utf-8")
            compiled = subprocess.run(
                ["/usr/bin/xcrun", "swiftc", str(source_path), "-o", str(binary)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, check=False, timeout=5
            )
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_cli_routes_read_only_focus_window_preflight_to_transport_handler(self):
        output = io.StringIO()
        with (
            patch(
                "codex_devtools.live_transport_control.run_live_transport",
                return_value={"status": "preflight_ready", "read_only": True},
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
                    "--action",
                    "focus-window",
                    "--expected-generation",
                    "123456",
                    "--snapshot-observed-at-utc",
                    "2026-11-08T12:00:00.000Z",
                    "--snapshot-launch-date",
                    "2026-11-08T11:00:00.000Z",
                    "--window-identity",
                    "1685072705",
                    "--receipt",
                    "/private/tmp/preflight.json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "preflight_ready")
        self.assertEqual(run.call_args.kwargs["action"], "focus-window")
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

    def test_inspect_preserves_validated_native_failure_receipt(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            set_path = Path(temporary) / "recall.als"
            set_path.write_bytes(b"test Set")
            native = {
                "format": FORMAT,
                "action": "inspect",
                "actions": {},
                "target": {"pid": PID, "bundle_identifier": BUNDLE},
                "set_url": set_path.as_uri(),
                "status": "stopped",
                "phase": "identity",
                "error": "TransportFailure(phase: identity, message: AX tree would exceed element bound)",
                "read_only": True,
                "manual_inspection_required": False,
            }
            kwargs = {
                "action": "inspect",
                "pid": PID,
                "bundle_id": BUNDLE,
                "set_path": set_path,
                "play_for_seconds": None,
                "returncode": 1,
            }
            self.assertTrue(_valid_report(native, **kwargs))

            malformed = []
            for name, value in (
                ("format", "wrong-format"),
                ("action", "stop"),
                ("read_only", False),
                ("manual_inspection_required", True),
                ("phase", " "),
                ("error", ""),
                ("actions", {"play": {"attempted": False}}),
                ("status", "observed"),
            ):
                candidate = {**native, name: value}
                malformed.append(candidate)
            malformed.extend(
                (
                    {**native, "target": {"pid": PID + 1, "bundle_identifier": BUNDLE}},
                    {**native, "set_url": "file:///wrong.als"},
                    {**native, "transport": {"play": 0}},
                )
            )
            for candidate in malformed:
                with self.subTest(candidate=candidate):
                    self.assertFalse(_valid_report(candidate, **kwargs))

            completed = subprocess.CompletedProcess(
                args=["native"], returncode=1, stdout=json.dumps(native), stderr=""
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
                    "codex_devtools.live_transport_control.subprocess.run",
                    return_value=completed,
                ),
            ):
                result = run_live_transport(
                    PID, expected_bundle_id=BUNDLE, set_path=set_path
                )
        self.assertEqual(result, native)
        self.assertEqual(result["error"], native["error"])
        self.assertNotIn("transport", result)
        self.assertEqual(result["status"], "stopped")

    def test_inspect_failure_receipt_requires_nonzero_native_exit(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            set_path = Path(temporary) / "recall.als"
            set_path.write_bytes(b"test Set")
            report = {
                "format": FORMAT,
                "action": "inspect",
                "actions": {},
                "target": {"pid": PID, "bundle_identifier": BUNDLE},
                "set_url": set_path.as_uri(),
                "status": "stopped",
                "phase": "identity",
                "error": "inspection failed",
                "read_only": True,
                "manual_inspection_required": False,
            }
            self.assertFalse(
                _valid_report(
                    report,
                    action="inspect",
                    pid=PID,
                    bundle_id=BUNDLE,
                    set_path=set_path,
                    play_for_seconds=None,
                    returncode=0,
                )
            )

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

    def test_play_accepts_caller_count_23_only_for_23_observed_rows(self):
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

            def inspect_sync(_pid, *, expected_bundle_id, expected_count):
                events.append(("sync-inspect", expected_bundle_id, expected_count))
                return baseline(count=expected_count)

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
                    side_effect=inspect_sync,
                ),
                patch(
                    "codex_devtools.live_transport_control.subprocess.run",
                    side_effect=lambda *args, **_kwargs: (
                        native_commands.append(args[0]) or completed
                    ),
                ),
            ):
                report = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="play",
                    play_for_seconds=3,
                    expected_count=23,
                    receipt_path=receipt_path,
                    execute=True,
                )

        self.assertEqual(events, [("sync-inspect", BUNDLE, 23)])
        self.assertEqual(report["status"], "complete")
        command = native_commands[0]
        self.assertEqual(command[command.index("--expected-count") + 1], "23")

    def test_play_refuses_observed_sync_row_count_mismatch_before_native_helper(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            receipt_path = root / "play.json"
            sync_counts = []

            def inspect_sync(_pid, *, expected_bundle_id, expected_count):
                sync_counts.append(expected_count)
                return baseline(count=22)

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
                    side_effect=inspect_sync,
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
                    play_for_seconds=3,
                    expected_count=23,
                    receipt_path=receipt_path,
                    execute=True,
                )

        native_run.assert_not_called()
        self.assertEqual(sync_counts, [23])
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(report["phase"], "output-isolation-preflight")
        self.assertIn("row count", report["error"])
        self.assertTrue(all(not row["attempted"] for row in report["actions"].values()))

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

    def test_inspection_timeout_keeps_empty_read_only_actions(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
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
                )
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(report["phase"], "native-helper-timeout")
        self.assertTrue(report["read_only"])
        self.assertEqual(report["actions"], {})
        self.assertFalse(report["manual_inspection_required"])
        self.assertIn("observation is unavailable", report["error"])

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

    def test_native_stop_preflight_refusal_preserves_known_not_attempted_receipt(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            receipt_path = root / "refused-stop.json"
            refused = transport_report(set_path, "stop", status="stopped")
            refused["phase"] = "stop-preflight"
            refused["actions"]["stop"] = {
                "attempted": False,
                "outcome": "not_attempted",
                "detail": "exact Set is not on the current desktop",
            }
            refused["error"] = "exact Set is not on the current desktop"
            native = subprocess.CompletedProcess(
                args=["native"],
                returncode=1,
                stdout=json.dumps(refused),
                stderr="",
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
                    "codex_devtools.live_transport_control.subprocess.run",
                    return_value=native,
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
        self.assertEqual(report["set_url"], set_path.as_uri())
        self.assertEqual(report["actions"]["stop"]["outcome"], "not_attempted")
        self.assertFalse(report["actions"]["stop"]["attempted"])
        self.assertFalse(report["manual_inspection_required"])

    def test_early_native_play_refusal_preserves_duration_and_not_attempted_actions(
        self,
    ):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            receipt_path = root / "refused-play.json"
            refused = {
                "format": FORMAT,
                "status": "stopped",
                "phase": "identity",
                "action": "play",
                "read_only": False,
                "target": {"pid": PID, "bundle_identifier": BUNDLE},
                "set_url": set_path.as_uri(),
                "play_for_seconds": 3,
                "actions": {
                    name: {"attempted": False, "outcome": "not_attempted"}
                    for name in ("settings_close", "play", "stop")
                },
                "manual_inspection_required": False,
                "error": "Accessibility is not trusted",
            }
            native = subprocess.CompletedProcess(
                args=["native"],
                returncode=1,
                stdout=json.dumps(refused),
                stderr="",
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
                    "codex_devtools.live_transport_control.run_live_midi_sync",
                    return_value=baseline(),
                ),
                patch(
                    "codex_devtools.live_transport_control.subprocess.run",
                    return_value=native,
                ),
            ):
                report = run_live_transport(
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
        self.assertEqual(report, saved)
        self.assertEqual(report["phase"], "identity")
        self.assertEqual(report["set_url"], set_path.as_uri())
        self.assertEqual(report["play_for_seconds"], 3)
        self.assertTrue(all(not row["attempted"] for row in report["actions"].values()))
        self.assertTrue(
            all(row["outcome"] == "not_attempted" for row in report["actions"].values())
        )
        self.assertFalse(report["manual_inspection_required"])

    def test_play_duration_and_output_count_are_bounded_before_helper_launch(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            for duration, expected_count, message in (
                (0, 23, "duration"),
                (16, 23, "duration"),
                (1, None, "expected output count"),
                (1, 0, "expected output count"),
                (1, -1, "expected output count"),
                (1, 129, "expected output count"),
                (1, True, "expected output count"),
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
            report.pop("stop_play_readback")
            self.assertFalse(_valid_report(report, **kwargs))
            report["stop_play_readback"] = "verified"
            report["transport_before_stop"]["link"] = 1
            report["manual_inspection_required"] = False
            self.assertFalse(_valid_report(report, **kwargs))
            report["manual_inspection_required"] = True
            self.assertTrue(_valid_report(report, **kwargs))
            report["transport_before_stop"]["link"] = 0
            report["manual_inspection_required"] = False
            report.pop("transport_before_stop")
            self.assertFalse(_valid_report(report, **kwargs))
            report["transport_before_stop"] = {
                "play": 1,
                "link": 0,
                "arrangement_record": 0,
                "session_record": 0,
            }
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

    def test_report_model_keeps_unknown_play_with_verified_stop_cleanup(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            set_path = Path(temporary) / "recall.als"
            set_path.write_bytes(b"test Set")
            report = transport_report(set_path, "play", seconds=3, status="stopped")
            report["phase"] = "play-outcome-unknown"
            report["error"] = (
                "Play did not read back on before its acknowledgement bound"
            )
            report["manual_inspection_required"] = True
            report["play_readback"] = "not_observed_before_bound"
            report["actions"]["play"] = {"attempted": True, "outcome": "unknown"}
            kwargs = {
                "action": "play",
                "pid": PID,
                "bundle_id": BUNDLE,
                "set_path": set_path,
                "play_for_seconds": 3,
                "returncode": 1,
            }
            self.assertEqual(report["actions"]["stop"]["outcome"], "known")
            self.assertEqual(report["stop_play_readback"], "verified")
            self.assertIn("Play did not read back on", report["error"])
            self.assertTrue(_valid_report(report, **kwargs))

    @unittest.skipUnless(Path("/usr/bin/xcrun").exists(), "Swift toolchain required")
    def test_production_deadline_gate_models_delayed_and_expired_readbacks(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        function_start = source.index("func waitForValueUntilDeadline(")
        function_end = source.index("\nfunc waitForTransportValue(", function_start)
        production_gate = source[function_start:function_end]
        harness = f"""
{production_gate}
final class ClockBox {{ var value: UInt64 = 0; var reads = 0 }}
let delayedClock = ClockBox()
let delayedValues = [0, 0, 1]
let delayed = try! waitForValueUntilDeadline(
    1,
    deadline: 10,
    now: {{ delayedClock.value }},
    read: {{ let value = delayedValues[delayedClock.reads]; delayedClock.reads += 1; return value }},
    pump: {{ delayedClock.value += 1 }}
)
precondition(delayed && delayedClock.reads == 3 && delayedClock.value == 2)

let expiredClock = ClockBox()
expiredClock.value = 5
let expired = try! waitForValueUntilDeadline(
    1,
    deadline: 5,
    now: {{ expiredClock.value }},
    read: {{ expiredClock.reads += 1; return 1 }},
    pump: {{}}
)
precondition(!expired && expiredClock.reads == 0)

let lateClock = ClockBox()
let late = try! waitForValueUntilDeadline(
    1,
    deadline: 5,
    now: {{ lateClock.value }},
    read: {{ lateClock.value = 5; lateClock.reads += 1; return 1 }},
    pump: {{}}
)
precondition(!late && lateClock.reads == 1)
"""
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            main_swift = root / "main.swift"
            binary = root / "deadline-model"
            main_swift.write_text(harness, encoding="utf-8")
            compiled = subprocess.run(
                ["/usr/bin/xcrun", "swiftc", str(main_swift), "-o", str(binary)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, check=False, timeout=5
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_stop_success_receipt_preserves_verified_readback(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            receipt_path = root / "stop.json"
            native = transport_report(set_path, "stop")
            native["actions"]["stop"] = {"attempted": True, "outcome": "known"}
            native["stop_play_readback"] = "verified"
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
            saved = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(report, saved)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["actions"]["stop"]["outcome"], "known")
        self.assertEqual(report["stop_play_readback"], "verified")

    def test_swift_contract_uses_monotonic_deadline_exact_set_and_settings_baseline(
        self,
    ):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("DispatchTime.now().uptimeNanoseconds", source)
        self.assertIn("RunLoop.current.run(", source)
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
        self.assertIn("canonicalSetURL = URL(fileURLWithPath: options.setPath)", source)
        self.assertIn("canonicalSetURL?.absoluteString ?? options.setPath", source)
        self.assertIn(
            'if options.action == "play" { result["play_for_seconds"] = options.playForSeconds }',
            source,
        )
        self.assertIn(
            "if linkAtStop != 0 || arrangementAtStop != 0 || sessionAtStop != 0",
            source,
        )
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
        deadline_wait = source.index("pumpRunLoop(until: deadline)")
        timed_stop = source.index(
            'try press(beforeStop["Stop"]!.element, actionName: "stop")'
        )
        self.assertLess(deadline_wait, timed_stop)
        self.assertIn(
            "let deadline = start + UInt64(options.playForSeconds) * 1_000_000_000",
            source,
        )
        self.assertIn(
            "acknowledgementDeadline(maxSeconds: 2.0, cappedBy: deadline)", source
        )

    def test_activation_and_deadline_waits_pump_run_loop_with_monotonic_bounds(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        wait_until = source.split("func waitUntil(", maxsplit=1)[1].split(
            "func onscreenWindow(", maxsplit=1
        )[0]
        self.assertIn("DispatchTime.now().uptimeNanoseconds", wait_until)
        self.assertIn("pumpRunLoopTurn(until: deadline)", wait_until)
        self.assertNotIn("Thread.sleep", wait_until)
        self.assertIn("func pumpRunLoop(until deadline: UInt64)", source)
        self.assertIn("RunLoop.current.run(", source)
        self.assertNotIn("Thread.sleep", source)
        play_press = source.index('try press(playControl, actionName: "play")')
        play_ack = source.index(
            "let playAcknowledged = try waitForTransportValue(", play_press
        )
        attempted_gate = source.index(
            'if actions["play"]?["attempted"] as? Bool != true', play_ack
        )
        deadline_wait = source.index("pumpRunLoop(until: deadline)", attempted_gate)
        timed_stop = source.index(
            'try press(beforeStop["Stop"]!.element, actionName: "stop")', deadline_wait
        )
        stop_ack = source.index(
            "let stopAcknowledged = try waitForTransportValue(", timed_stop
        )
        self.assertLess(play_press, play_ack)
        self.assertLess(play_ack, attempted_gate)
        self.assertLess(attempted_gate, deadline_wait)
        self.assertLess(deadline_wait, timed_stop)
        self.assertLess(timed_stop, stop_ack)

    @unittest.skipUnless(Path("/usr/bin/xcrun").exists(), "Swift toolchain required")
    def test_only_exact_observed_window_sharing_overlay_signature_is_allowed(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        start = source.index("struct WindowSharingOverlayChildSignature")
        end = source.index("\nvar options =", start)
        production_signature = source[start:end]
        harness = f"""
let kAXWindowRole = "AXWindow"
let kAXButtonRole = "AXButton"
{production_signature}

let overlay = WindowSharingOverlaySignature(
    role: "AXWindow", subrole: "AXDialog", title: "Window", identifier: "_NS:8",
    children: [WindowSharingOverlayChildSignature(
        role: "AXButton", title: "WindowSharingSessionButton", identifier: nil, enabled: true
    )]
)
precondition(isObservedWindowSharingOverlay(overlay))
func changed(_ value: WindowSharingOverlaySignature, role: String? = nil,
             subrole: String? = nil, title: String? = nil, identifier: String? = nil,
             children: [WindowSharingOverlayChildSignature]? = nil) -> WindowSharingOverlaySignature {{
    WindowSharingOverlaySignature(
        role: role ?? value.role, subrole: subrole ?? value.subrole,
        title: title ?? value.title, identifier: identifier ?? value.identifier,
        children: children ?? value.children
    )
}}
precondition(!isObservedWindowSharingOverlay(changed(overlay, role: "AXDialog")))
precondition(!isObservedWindowSharingOverlay(changed(overlay, subrole: "AXSheet")))
precondition(!isObservedWindowSharingOverlay(changed(overlay, subrole: "AXPopover")))
precondition(!isObservedWindowSharingOverlay(changed(overlay, subrole: "AXFloatingWindow")))
precondition(!isObservedWindowSharingOverlay(changed(overlay, title: "Save Changes")))
precondition(!isObservedWindowSharingOverlay(changed(overlay, identifier: "_NS:9")))
precondition(!isObservedWindowSharingOverlay(changed(overlay, children: [])))
precondition(!isObservedWindowSharingOverlay(changed(overlay, children: overlay.children + overlay.children)))
let child = overlay.children[0]
precondition(!isObservedWindowSharingOverlay(WindowSharingOverlaySignature(
    role: overlay.role, subrole: overlay.subrole, title: nil, identifier: overlay.identifier,
    children: overlay.children
)))
precondition(!isObservedWindowSharingOverlay(WindowSharingOverlaySignature(
    role: overlay.role, subrole: overlay.subrole, title: overlay.title, identifier: nil,
    children: overlay.children
)))
precondition(!isObservedWindowSharingOverlay(changed(overlay, children: [
    WindowSharingOverlayChildSignature(role: child.role, title: nil, identifier: nil, enabled: true)
])))
precondition(!isObservedWindowSharingOverlay(changed(overlay, children: overlay.children + [
    WindowSharingOverlayChildSignature(role: "AXStaticText", title: "Confirm", identifier: nil, enabled: true)
])))
precondition(!isObservedWindowSharingOverlay(changed(overlay, children: [
    WindowSharingOverlayChildSignature(
        role: "AXButton", title: "Save", identifier: nil, enabled: true
    )
])))
precondition(!isObservedWindowSharingOverlay(changed(overlay, children: [
    WindowSharingOverlayChildSignature(
        role: "AXCheckBox", title: "WindowSharingSessionButton", identifier: nil, enabled: true
    )
])))
precondition(!isObservedWindowSharingOverlay(changed(overlay, children: [
    WindowSharingOverlayChildSignature(
        role: "AXButton", title: "WindowSharingSessionButton", identifier: "unexpected", enabled: true
    )
])))
precondition(!isObservedWindowSharingOverlay(changed(overlay, children: [
    WindowSharingOverlayChildSignature(
        role: "AXButton", title: "WindowSharingSessionButton", identifier: nil, enabled: false
    )
])))
let ordinaryDialog = WindowSharingOverlaySignature(
    role: "AXWindow", subrole: "AXDialog", title: "Save Changes", identifier: "_NS:8",
    children: [WindowSharingOverlayChildSignature(role: "AXButton", title: "Save", identifier: nil, enabled: true)]
)
precondition(!isObservedWindowSharingOverlay(ordinaryDialog))
"""
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            main_swift = root / "main.swift"
            binary = root / "overlay-signature-model"
            main_swift.write_text(harness, encoding="utf-8")
            compiled = subprocess.run(
                ["/usr/bin/xcrun", "swiftc", str(main_swift), "-o", str(binary)],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(binary)], capture_output=True, text=True, check=False, timeout=5
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        app_windows = source.split("func appWindows(", maxsplit=1)[1].split(
            "func isObservedWindowSharingOverlay(_ window:", maxsplit=1
        )[0]
        self.assertIn("isBlockingWindow", app_windows)
        self.assertIn("!isAllowedOverlay", app_windows)
        adapter = source.split(
            "func isObservedWindowSharingOverlay(_ window:", maxsplit=1
        )[1].split("\nfunc lexicalPath(", maxsplit=1)[0]
        self.assertIn("childElements.count == 1", adapter)
        self.assertIn("try role(button)", adapter)
        self.assertIn("try enabled(button)", adapter)


if __name__ == "__main__":
    unittest.main()
