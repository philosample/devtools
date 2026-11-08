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
    _valid_position_report,
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
    def test_position_window_cli_routes_read_only_preflight_and_coordinates(self):
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
                    "position-window",
                    "--expected-generation",
                    "123456",
                    "--snapshot-observed-at-utc",
                    "2026-11-08T12:00:00.000Z",
                    "--snapshot-launch-date",
                    "2026-11-08T11:00:00.000Z",
                    "--window-identity",
                    "1685072721",
                    "--position-x",
                    "1000",
                    "--position-y",
                    "350",
                    "--receipt",
                    "/private/tmp/position.json",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "preflight_ready")
        self.assertEqual(run.call_args.kwargs["action"], "position-window")
        self.assertEqual(run.call_args.kwargs["position_x"], 1000.0)
        self.assertEqual(run.call_args.kwargs["position_y"], 350.0)
        self.assertFalse(run.call_args.kwargs["execute"])

    def test_position_window_native_command_pins_destination_only_in_preflight(self):
        preflight = _native_command(
            Path("/tmp/helper"),
            pid=PID,
            bundle_id=BUNDLE,
            set_path=Path("/private/tmp/recall.als"),
            action="position-window",
            play_for_seconds=None,
            expected_count=None,
            baseline_path=None,
            baseline_hash=None,
            window_identity="98765",
            expected_generation=123456,
            snapshot_observed_at_utc="2026-11-08T12:00:00.000Z",
            snapshot_launch_date="2026-11-08T11:00:00.000Z",
            position=(1000.0, 350.0),
        )
        self.assertNotIn("--execute", preflight)
        position_index = preflight.index("--position-x")
        self.assertEqual(preflight[position_index + 1], "1000.0")
        self.assertIn("--position-y", preflight)
        execute = _native_command(
            Path("/tmp/helper"),
            pid=PID,
            bundle_id=BUNDLE,
            set_path=Path("/private/tmp/recall.als"),
            action="position-window",
            play_for_seconds=None,
            expected_count=None,
            baseline_path=None,
            baseline_hash=None,
            expected_generation=123456,
            preflight_path=Path("/private/tmp/preflight.json"),
            preflight_hash="a" * 64,
        )
        self.assertIn("--execute", execute)
        self.assertNotIn("--position-x", execute)
        self.assertNotIn("--window-identity", execute)

    def test_position_window_preflight_and_execution_validate_native_receipts(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            set_path = root / "recall.als"
            set_path.write_bytes(b"test Set")
            preflight_path = root / "preflight.json"
            result_path = root / "result.json"
            window = {
                "identity": "98765",
                "role": "AXWindow",
                "subrole": "AXStandardWindow",
                "title": "Markov Transition Editor",
                "identifier": None,
                "document": "file:///tmp/Markov.maxpat",
                "window_number": 31,
                "cg_candidates": [{"number": 31, "layer": 0}],
                "geometry": {"x": 25.0, "y": 44.0, "width": 640.0, "height": 197.0},
            }
            requested = {"x": 1000.0, "y": 350.0}
            common = {
                "target": {
                    "pid": PID,
                    "bundle_identifier": BUNDLE,
                    "process_generation": 123456,
                },
                "set_url": set_path.as_uri(),
                "set_window_identity": "11111",
                "window": window,
                "requested_position": requested,
                "position_settable": True,
                "manual_inspection_required": False,
            }
            preflight = {
                **common,
                "format": "codex-devtools-live-position-window-preflight-v1",
                "status": "preflight_ready",
                "read_only": True,
                "observed_at_utc": "2026-11-08T12:00:00.000Z",
                "snapshot_observed_at_utc": "2026-11-08T12:00:00.000Z",
                "snapshot_launch_date": "2026-11-08T11:00:00.000Z",
                "actions": {},
            }
            after = {
                **window,
                "geometry": {**window["geometry"], "x": 1000.0, "y": 350.0},
            }
            result = {
                **common,
                "format": "codex-devtools-live-position-window-result-v1",
                "status": "complete",
                "read_only": False,
                "preflight_sha256": "PENDING",
                "window_after": after,
                "actions": {"position": "known"},
            }
            native_calls = []

            def native_run(command, **_kwargs):
                native_calls.append(command)
                if "--execute" in command:
                    result["preflight_sha256"] = hashlib.sha256(
                        preflight_path.read_bytes()
                    ).hexdigest()
                    report = result
                else:
                    report = preflight
                return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

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
                    side_effect=native_run,
                ),
            ):
                first = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="position-window",
                    window_identity="98765",
                    expected_generation=123456,
                    snapshot_observed_at_utc="2026-11-08T12:00:00.000Z",
                    snapshot_launch_date="2026-11-08T11:00:00.000Z",
                    position_x=1000,
                    position_y=350,
                    receipt_path=preflight_path,
                )
                digest = hashlib.sha256(preflight_path.read_bytes()).hexdigest()
                second = run_live_transport(
                    PID,
                    expected_bundle_id=BUNDLE,
                    set_path=set_path,
                    action="position-window",
                    expected_generation=123456,
                    preflight_receipt_path=preflight_path,
                    preflight_sha256=digest,
                    receipt_path=result_path,
                    execute=True,
                )
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    run_live_transport(
                        PID,
                        expected_bundle_id=BUNDLE,
                        set_path=set_path,
                        action="position-window",
                        expected_generation=123456,
                        preflight_receipt_path=preflight_path,
                        preflight_sha256="0" * 64,
                        receipt_path=root / "tampered-result.json",
                        execute=True,
                    )
            self.assertEqual(first["status"], "preflight_ready")
            self.assertEqual(second["status"], "complete")
            self.assertEqual(len(native_calls), 2)
            self.assertTrue(
                _valid_position_report(
                    second,
                    execute=True,
                    pid=PID,
                    bundle_id=BUNDLE,
                    set_path=set_path,
                    identity="98765",
                    generation=123456,
                    position=(1000.0, 350.0),
                    preflight_hash=digest,
                    returncode=0,
                )
            )
            self.assertNotIn("--position-x", native_calls[1])

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
                    "identity": "1685072721",
                    "role": "AXWindow",
                    "subrole": "AXStandardWindow",
                    "title": "[INTTR_Operator_Parameter_Window]",
                    "identifier": None,
                    "document": None,
                    "window_number": None,
                    "cg_candidates": [
                        {"number": 31731, "layer": 0},
                        {"number": 31737, "layer": 0},
                    ],
                    "geometry": {"x": 1, "y": 2, "width": 300, "height": 200},
                },
            }
            fields = {
                "execute": False,
                "pid": PID,
                "bundle_id": BUNDLE,
                "set_path": set_path,
                "identity": "1685072721",
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
            report["window"]["identity"] = "1685072721"
            report["window"]["cg_candidates"][1]["layer"] = 9
            self.assertFalse(_valid_focus_report(report, **fields))
            report["window"]["cg_candidates"][1]["layer"] = 0
            report["window"]["window_number"] = 31739
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
                "identity": "1685072721",
                "role": "AXWindow",
                "subrole": "AXStandardWindow",
                "title": "[INTTR_Operator_Parameter_Window]",
                "identifier": None,
                "document": None,
                "window_number": None,
                "cg_candidates": [
                    {"number": 31731, "layer": 0},
                    {"number": 31737, "layer": 0},
                ],
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
                    window_identity="1685072721",
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
    def test_swift_focus_accepts_and_pins_observed_embedded_max_document_url(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        lexical_start = source.index("func lexicalPath(")
        lexical_end = source.index("\nfunc setURL(", lexical_start)
        file_url_start = source.index("func fileURLPath(")
        file_url_end = source.index("\nfunc sameSetURL(", file_url_start)
        document_start = source.index("func validatedFocusWindowDocument(")
        document_end = source.index("\nfunc selectedFocusWindow(", document_start)
        receipt_start = source.index("func focusReceiptIsCurrent(")
        receipt_end = source.index("\nfunc focusWindowPreflightReport(", receipt_start)
        focus_record_start = source.index("func focusWindowRecord(")
        focus_record_end = source.index(
            "\nfunc validatedFocusWindowDocument(", focus_record_start
        )
        self.assertIn(
            "validatedFocusWindowDocument(optionalString(window, kAXDocumentAttribute as String))",
            source[focus_record_start:focus_record_end],
        )
        contract = "\n".join(
            (
                source[lexical_start:lexical_end],
                source[file_url_start:file_url_end],
                source[document_start:document_end],
                source[receipt_start:receipt_end],
            )
        )
        child_url = "file:///Users/philosample/workstation-docs/evidence/studio-control/2026-11-08-coordinator-playable/native592-tr-subwindow-opener/package/Markov%20Transition%20Editor.maxpat"
        harness = f"""
import Foundation
import ApplicationServices
struct TransportFailure: Error {{ let phase: String; let message: String }}
var phase = "focus-window-preflight"
func require(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {{
    if try !condition() {{ throw TransportFailure(phase: phase, message: message) }}
}}
{contract}
let childURL = {json.dumps(child_url)}
let validated = try validatedFocusWindowDocument(childURL)
precondition(validated == childURL)
let absent = try validatedFocusWindowDocument(nil)
precondition(absent == nil)
let now = Date(timeIntervalSince1970: 1_800_000_000)
let formatter = ISO8601DateFormatter()
formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
let window: [String: Any] = ["identity":"1685072721", "role":"AXWindow", "subrole":"AXStandardWindow", "title":"Markov Transition Editor", "identifier":NSNull(), "document":validated!, "window_number":NSNull(), "cg_candidates":[["number":31731,"layer":0]], "geometry":["x":100.0,"y":97.0,"width":760.0,"height":198.0]]
let receipt: [String: Any] = ["format":"codex-devtools-live-focus-window-preflight-v1", "status":"preflight_ready", "read_only":true, "observed_at_utc":formatter.string(from: now), "target":["pid":58198,"bundle_identifier":"com.ableton.live","process_generation":123456], "set_url":"file:///tmp/recall.als", "set_window_identity":"11111", "window":window]
precondition(focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072721", currentWindow:window, now:now))
var changed = window
changed["document"] = "file:///Users/philosample/other.maxpat"
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072721", currentWindow:changed, now:now))
for unsafe in ["https://example.test/patch.maxpat", childURL + "?query=1", childURL + "#fragment", "file:///tmp/../escape.maxpat"] {{
    do {{ _ = try validatedFocusWindowDocument(unsafe); preconditionFailure("unsafe AXDocument was accepted: \\(unsafe)") }} catch {{}}
}}
"""
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            main_swift = Path(temporary) / "main.swift"
            binary = Path(temporary) / "focus-document-policy"
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
    def test_swift_focus_candidate_set_accepts_only_bounded_exact_geometry_rows(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        start = source.index("struct FocusCGObservation {")
        end = source.index("\nfunc currentFocusCGCandidateSet(", start)
        matcher = source[start:end]
        harness = f"""
import Foundation
let maxWindows = 64
struct TransportFailure: Error {{ let phase: String; let message: String }}
var phase = "focus-window-preflight"
func require(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {{
    if try !condition() {{ throw TransportFailure(phase: phase, message: message) }}
}}
{matcher}
let pid: pid_t = 47314
let engineer = [
  FocusCGObservation(pid:pid, name:"[INTTR_Operator_Parameter_Window]", number:31737, layer:0, x:100, y:97, width:760, height:198, onscreen:true),
  FocusCGObservation(pid:pid, name:"[INTTR_Operator_Parameter_Window]", number:31731, layer:0, x:100, y:97, width:760, height:198, onscreen:true),
  FocusCGObservation(pid:pid, name:"[INTTR_Operator_Parameter_Window]", number:31729, layer:0, x:860, y:97, width:760, height:198, onscreen:true),
]
let tr = [
  FocusCGObservation(pid:pid, name:"TR-1000 Ctrl", number:31735, layer:3, x:100, y:97, width:760, height:198, onscreen:true),
  FocusCGObservation(pid:pid, name:"TR-1000 Ctrl", number:31730, layer:3, x:100, y:97, width:760, height:198, onscreen:true),
]
let geometry = ["x":100.0,"y":97.0,"width":760.0,"height":198.0]
let engineerResult = try focusCGCandidateSet(engineer, pid:pid, title:"[INTTR_Operator_Parameter_Window]", geometry:geometry)
let trResult = try focusCGCandidateSet(tr, pid:pid, title:"TR-1000 Ctrl", geometry:geometry)
precondition(engineerResult == [FocusCGCandidate(number:31731, layer:0), FocusCGCandidate(number:31737, layer:0)])
precondition(trResult == [FocusCGCandidate(number:31730, layer:3), FocusCGCandidate(number:31735, layer:3)])
do {{ _ = try focusCGCandidateSet([], pid:pid, title:"TR-1000 Ctrl", geometry:geometry); preconditionFailure("zero candidates accepted") }} catch {{}}
do {{ _ = try focusCGCandidateSet([FocusCGObservation(pid:pid, name:"TR-1000 Ctrl", number:1, layer:9, x:100, y:97, width:760, height:198, onscreen:true)], pid:pid, title:"TR-1000 Ctrl", geometry:geometry); preconditionFailure("unknown layer accepted") }} catch {{}}
do {{ _ = try focusCGCandidateSet(Array(repeating: engineer[0], count:65), pid:pid, title:"[INTTR_Operator_Parameter_Window]", geometry:geometry); preconditionFailure("oversized inventory accepted") }} catch {{}}
"""
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            main_swift = Path(temporary) / "main.swift"
            binary = Path(temporary) / "focus-candidates"
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
let window: [String: Any] = ["identity":"1685072721", "role":"AXWindow", "subrole":"AXStandardWindow", "title":"[INTTR_Operator_Parameter_Window]", "identifier":NSNull(), "document":NSNull(), "window_number":NSNull(), "cg_candidates":[["number":31731,"layer":0],["number":31737,"layer":0]], "geometry":["x":100.0,"y":97.0,"width":760.0,"height":198.0]]
let receipt: [String: Any] = ["format":"codex-devtools-live-focus-window-preflight-v1", "status":"preflight_ready", "read_only":true, "observed_at_utc":formatter.string(from: now), "target":["pid":58198,"bundle_identifier":"com.ableton.live","process_generation":123456], "set_url":"file:///tmp/recall.als", "set_window_identity":"11111", "window":window]
precondition(focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072721", currentWindow:window, now:now.addingTimeInterval(30)))
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072721", currentWindow:window, now:now.addingTimeInterval(31)))
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123457, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072721", currentWindow:window, now:now))
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/other.als", setWindowIdentity:"11111", identity:"1685072721", currentWindow:window, now:now))
var changed = window
changed["title"] = "Other"
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072721", currentWindow:changed, now:now))
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"22222", identity:"1685072721", currentWindow:window, now:now))
var changedCandidates = window
changedCandidates["cg_candidates"] = [["number":31731,"layer":0]]
precondition(!focusReceiptIsCurrent(receipt, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/recall.als", setWindowIdentity:"11111", identity:"1685072721", currentWindow:changedCandidates, now:now))
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
  "window_number": NSNull(), "cg_candidates": [["number": 31405, "layer": 0]],
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
    def test_swift_position_report_and_policy_match_python_caller_contract(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_transport.swift"
        ).read_text(encoding="utf-8")
        start = source.index("func positionWindowIsCurrent(")
        report_start = source.index("func positionWindowReport(", start)
        display_start = source.index("func positionFitsDisplay(", report_start)
        display_end = source.index(
            "\nfunc currentActiveDisplayContains(", display_start
        )
        settable_start = source.index("func positionSettableResult(", display_end)
        settable_end = source.index("\nfunc controlsState(", settable_start)
        success_start = source.index("func successfulHelperStatus(")
        success_end = source.index("\nfunc focusWindowAction(", success_start)
        action_start = source.index("func positionWindowAction(")
        action_end = source.index("\nfunc positionWindowIsCurrent(", action_start)
        action = source[action_start:action_end]
        self.assertEqual(action.count("AXUIElementSetAttributeValue("), 1)
        self.assertNotIn("AXUIElementPerformAction(", action)
        set_action_start = source.index("func setAction(")
        set_action_end = source.index("\nfunc press(", set_action_start)
        settle_start = source.index("struct PositionReadbackPending")
        settle_end = source.index("\nfunc waitForValueUntilDeadline(", settle_start)
        production = (
            source[start:report_start]
            + source[report_start:display_start]
            + source[display_start:display_end]
            + source[settable_start:settable_end]
            + source[success_start:success_end]
            + source[set_action_start:set_action_end]
            + source[settle_start:settle_end]
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            set_path = Path(temporary) / "M1-SDKFree-TR-Window592.als"
            set_path.write_bytes(b"selected Set fixture")
            harness = f"""
import Foundation
import ApplicationServices
import CryptoKit
struct TransportFailure: Error {{ let phase: String; let message: String }}
var phase = "position-window-preflight"
var actions: [String: [String: Any]] = [:]
var manualInspectionRequired = false
func require(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {{
    if try !condition() {{ throw TransportFailure(phase: phase, message: message) }}
}}
func pumpRunLoopTurn(until deadline: UInt64) {{}}
{production}
let now = Date(timeIntervalSince1970: 1_800_000_000)
let formatter = ISO8601DateFormatter()
formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
let url = {json.dumps(set_path.as_uri())}
let window: [String: Any] = [
  "identity":"98765", "role":"AXWindow", "subrole":"AXStandardWindow",
  "title":"Markov Transition Editor", "identifier":NSNull(), "document":"file:///tmp/Markov.maxpat",
  "window_number":31, "cg_candidates":[["number":31,"layer":0]],
  "geometry":["x":25.0,"y":44.0,"width":640.0,"height":197.0],
]
let requested = ["x":1000.0,"y":350.0]
let report = positionWindowReport(
  observedAt:formatter.string(from:now), snapshotObservedAt:formatter.string(from:now),
  snapshotLaunchDate:"2026-11-08T11:00:00.000Z", pid:58198, bundleID:"com.ableton.live",
  generation:123456, setURL:url, setWindowIdentity:"11111", window:window, position:requested
)
let valid = positionWindowIsCurrent(
  report, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:url,
  setWindowIdentity:"11111", identity:"98765", currentWindow:window, position:requested, now:now
)
precondition(valid)
precondition(!positionWindowIsCurrent(report, pid:58198, bundleID:"com.ableton.live", generation:123457, setURL:url, setWindowIdentity:"11111", identity:"98765", currentWindow:window, position:requested, now:now))
precondition(!positionWindowIsCurrent(report, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:"file:///tmp/other.als", setWindowIdentity:"11111", identity:"98765", currentWindow:window, position:requested, now:now))
precondition(!positionWindowIsCurrent(report, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:url, setWindowIdentity:"22222", identity:"98765", currentWindow:window, position:requested, now:now))
var changed = window
changed["geometry"] = ["x":26.0,"y":44.0,"width":640.0,"height":197.0]
precondition(!positionWindowIsCurrent(report, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:url, setWindowIdentity:"11111", identity:"98765", currentWindow:changed, position:requested, now:now))
precondition(!positionWindowIsCurrent(report, pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:url, setWindowIdentity:"11111", identity:"98765", currentWindow:window, position:requested, now:now.addingTimeInterval(31)))
precondition(positionFitsDisplay(CGPoint(x:1000,y:350), size:CGSize(width:640,height:197), displays:[CGRect(x:0,y:0,width:2000,height:1200)]))
precondition(!positionFitsDisplay(CGPoint(x:1900,y:1100), size:CGSize(width:640,height:197), displays:[CGRect(x:0,y:0,width:2000,height:1200)]))
do {{ _ = try positionSettableResult(error:.success, settable:false); preconditionFailure("unsupported AXPosition was accepted") }} catch {{}}
precondition(successfulHelperStatus(report["status"] as? String))
let data = try! JSONSerialization.data(withJSONObject:report, options:[.sortedKeys])
print(String(data:data, encoding:.utf8)!)
let receiptBytes = Data("reviewed receipt".utf8)
let receiptHash = SHA256.hash(data:receiptBytes).map {{ String(format:"%02x", $0) }}.joined()
precondition(positionReceiptHashMatches(receiptBytes, expected:receiptHash))
precondition(!positionReceiptHashMatches(receiptBytes, expected:String(repeating:"0", count:64)))
actions = ["position":["attempted":false,"outcome":"not_attempted"]]
manualInspectionRequired = false
setAction("position", outcome:"unknown")
precondition(actions["position"]?["attempted"] as? Bool == true)
precondition(actions["position"]?["outcome"] as? String == "unknown")
precondition(manualInspectionRequired)
var afterWindow = window
afterWindow["geometry"] = ["x":1000.25,"y":350.0,"width":640.0,"height":197.0]
precondition(positionReadbackMatches(afterWindow["geometry"] as! [String: Double], requested:requested, originalSize:CGSize(width:640,height:197)))
let execution = positionWindowResultReport(
  pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:url,
  setWindowIdentity:"11111", preflightHash:receiptHash, before:window,
  after:afterWindow, position:requested
)
precondition(successfulHelperStatus(execution["status"] as? String))
let executionData = try! JSONSerialization.data(withJSONObject:execution, options:[.sortedKeys])
print(String(data:executionData, encoding:.utf8)!)
var boundaryWindow = afterWindow
boundaryWindow["geometry"] = ["x":1000.5,"y":350.0,"width":640.0,"height":197.0]
precondition(!positionReadbackMatches(boundaryWindow["geometry"] as! [String: Double], requested:requested, originalSize:CGSize(width:640,height:197)))
let boundary = positionWindowResultReport(
  pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:url,
  setWindowIdentity:"11111", preflightHash:receiptHash, before:window,
  after:boundaryWindow, position:requested
)
let boundaryData = try! JSONSerialization.data(withJSONObject:boundary, options:[.sortedKeys])
print(String(data:boundaryData, encoding:.utf8)!)
var sizeChangedWindow = afterWindow
sizeChangedWindow["geometry"] = ["x":1000.25,"y":350.0,"width":640.002,"height":197.0]
precondition(!positionReadbackMatches(sizeChangedWindow["geometry"] as! [String: Double], requested:requested, originalSize:CGSize(width:640,height:197)))
let sizeChanged = positionWindowResultReport(
  pid:58198, bundleID:"com.ableton.live", generation:123456, setURL:url,
  setWindowIdentity:"11111", preflightHash:receiptHash, before:window,
  after:sizeChangedWindow, position:requested
)
let sizeChangedData = try! JSONSerialization.data(withJSONObject:sizeChanged, options:[.sortedKeys])
print(String(data:sizeChangedData, encoding:.utf8)!)
let settledWindow: [String: Any] = ["geometry":["x":1000.25,"y":350.0,"width":640.0,"height":197.0]]
let cgMiss = TransportFailure(phase:"position-window-readback", message:"no current-desktop CG window matches the selected AX window")
var fakeNow: UInt64 = 0
var setterCalls = 0
var readCalls = 0
let settled = try positionWindowWriteAndSettle(seconds:2, now:{{ fakeNow }}, pump:{{ _ in fakeNow += 1_000_000 }}, write:{{ setterCalls += 1 }}, read:{{
  readCalls += 1
  if readCalls == 1 {{ throw cgMiss }}
  return settledWindow
}})
precondition(setterCalls == 1 && readCalls == 2)
precondition(positionReadbackMatches(settled["geometry"] as! [String: Double], requested:requested, originalSize:CGSize(width:640,height:197)))
fakeNow = 0
setterCalls = 0
readCalls = 0
do {{
  _ = try positionWindowWriteAndSettle(seconds:2, now:{{ fakeNow }}, pump:{{ _ in fakeNow = 2_000_000_000 }}, write:{{ setterCalls += 1 }}, read:{{ readCalls += 1; throw cgMiss }})
  preconditionFailure("persistent CG miss was accepted")
}} catch let failure as TransportFailure {{
  precondition(failure.phase == "position-window-readback")
  precondition(failure.message.contains("did not settle"))
}}
precondition(setterCalls == 1 && readCalls == 1)
fakeNow = 0
setterCalls = 0
readCalls = 0
do {{
  _ = try positionWindowWriteAndSettle(seconds:2, now:{{ fakeNow }}, pump:{{ _ in fakeNow += 1_000_000 }}, write:{{ setterCalls += 1 }}, read:{{
    readCalls += 1
    fakeNow = 2_000_000_001
    return settledWindow
  }})
  preconditionFailure("late readback was accepted after the deadline")
}} catch let failure as TransportFailure {{ precondition(failure.message.contains("did not settle")) }}
precondition(setterCalls == 1 && readCalls == 1)
for driftMessage in ["Live process changed during window positioning", "exact current Set window changed during positioning", "selected window identity or metadata changed during positioning"] {{
  fakeNow = 0
  setterCalls = 0
  readCalls = 0
  do {{
    _ = try positionWindowWriteAndSettle(seconds:2, now:{{ fakeNow }}, pump:{{ _ in fakeNow += 1_000_000 }}, write:{{ setterCalls += 1 }}, read:{{
      readCalls += 1
      throw TransportFailure(phase:"position-window-readback", message:driftMessage)
    }})
    preconditionFailure("drift was accepted")
  }} catch let failure as TransportFailure {{ precondition(failure.message == driftMessage) }}
  precondition(setterCalls == 1 && readCalls == 1)
}}
"""
            main_swift = Path(temporary) / "main.swift"
            binary = Path(temporary) / "position-window-policy"
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
        report, execution, boundary, size_changed = (
            json.loads(line) for line in result.stdout.splitlines()
        )
        self.assertTrue(
            _valid_position_report(
                report,
                execute=False,
                pid=PID,
                bundle_id=BUNDLE,
                set_path=set_path,
                identity="98765",
                generation=123456,
                position=(1000.0, 350.0),
                preflight_hash=None,
                returncode=0,
            )
        )
        digest = execution["preflight_sha256"]
        self.assertTrue(
            _valid_position_report(
                execution,
                execute=True,
                pid=PID,
                bundle_id=BUNDLE,
                set_path=set_path,
                identity="98765",
                generation=123456,
                position=(1000.0, 350.0),
                preflight_hash=digest,
                returncode=0,
            )
        )
        for rejected in (boundary, size_changed):
            self.assertFalse(
                _valid_position_report(
                    rejected,
                    execute=True,
                    pid=PID,
                    bundle_id=BUNDLE,
                    set_path=set_path,
                    identity="98765",
                    generation=123456,
                    position=(1000.0, 350.0),
                    preflight_hash=digest,
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
                    "1685072721",
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
