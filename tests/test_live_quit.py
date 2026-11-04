"""Guarded normal-quit policy tests; no Live or AX actions are run."""

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codex_devtools.cli import main
from codex_devtools.live_quit_control import (
    FORMAT,
    LIVE_BUNDLE_ID,
    run_live_quit,
)

PID = 12345
GENERATION = "1700000000123456"


class LiveQuitPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temp.name)
        self.app = self.root / "Ableton Live Beta.app"
        self.app.mkdir()
        self.source = self.root / "Owned.als"
        self.source.write_bytes(b"stable saved Set bytes")
        info = self.source.stat()
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.device = str(info.st_dev)
        self.inode = str(info.st_ino)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, **changes):
        args = {
            "expected_bundle_id": LIVE_BUNDLE_ID,
            "app_bundle": self.app,
            "expected_generation": GENERATION,
            "source_set": self.source,
            "source_sha256": self.sha,
            "source_device": self.device,
            "source_inode": self.inode,
        }
        args.update(changes)
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch("codex_devtools.live_quit_control.subprocess.run") as native,
        ):
            result = run_live_quit(PID, **args)
        return result, native

    def native_receipt(self, *, execute=False, status="ready"):
        pin = {
            "path": str(self.source),
            "sha256": self.sha,
            "bytes": self.source.stat().st_size,
            "device": self.device,
            "inode": self.inode,
        }
        requested = status in {"terminated", "stopped"} and execute
        terminated = status == "terminated"
        return {
            "format": FORMAT,
            "status": status,
            "phase": "complete"
            if status in {"ready", "terminated"}
            else "quit-deadline",
            "read_only": not execute,
            "target": {
                "pid": PID,
                "bundle_identifier": LIVE_BUNDLE_ID,
                "app_path": str(self.app),
                "executable_path": str(self.app / "Contents/MacOS/Live"),
                "process_generation": GENERATION,
            },
            "source_set_url": self.source.as_uri(),
            "source_file": pin,
            "quit": {
                "requested": requested,
                "accepted": True if terminated or requested else None,
                "terminated": terminated,
                "outcome": "terminated"
                if terminated
                else "unknown"
                if requested
                else "not_attempted",
                "force_quit": False,
            },
            "manual_inspection_required": requested and not terminated,
        }

    def test_input_rejections_happen_before_native_helper(self):
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch("codex_devtools.live_quit_control.subprocess.run") as native,
        ):
            with self.assertRaisesRegex(ValueError, "bundle identifier"):
                run_live_quit(
                    PID,
                    expected_bundle_id="com.other.app",
                    app_bundle=self.app,
                    expected_generation=GENERATION,
                    source_set=self.source,
                    source_sha256=self.sha,
                    source_device=self.device,
                    source_inode=self.inode,
                )
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                run_live_quit(
                    PID,
                    expected_bundle_id=LIVE_BUNDLE_ID,
                    app_bundle=self.app,
                    expected_generation=GENERATION,
                    source_set=self.source,
                    source_sha256="z" * 64,
                    source_device=self.device,
                    source_inode=self.inode,
                )
            native.assert_not_called()

    def test_default_is_read_only_and_never_passes_execute(self):
        receipt = self.native_receipt()
        completed = subprocess.CompletedProcess([], 0, json.dumps(receipt), "")
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_quit_control.subprocess.run",
                return_value=completed,
            ) as native,
        ):
            result = run_live_quit(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                app_bundle=self.app,
                expected_generation=GENERATION,
                source_set=self.source,
                source_sha256=self.sha,
                source_device=self.device,
                source_inode=self.inode,
            )
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["read_only"])
        self.assertNotIn("--execute", native.call_args.args[0])

    def test_explicit_execute_passes_one_opt_in_and_validates_termination_receipt(self):
        receipt = self.native_receipt(execute=True, status="terminated")
        completed = subprocess.CompletedProcess([], 0, json.dumps(receipt), "")
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_quit_control.subprocess.run",
                return_value=completed,
            ) as native,
        ):
            result = run_live_quit(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                app_bundle=self.app,
                expected_generation=GENERATION,
                source_set=self.source,
                source_sha256=self.sha,
                source_device=self.device,
                source_inode=self.inode,
                execute=True,
            )
        command = native.call_args.args[0]
        self.assertEqual(command.count("--execute"), 1)
        self.assertEqual(result["quit"]["outcome"], "terminated")

    def test_changed_native_source_pin_becomes_structured_unknown(self):
        receipt = self.native_receipt(execute=True, status="terminated")
        receipt["source_file"]["sha256"] = "0" * 64
        completed = subprocess.CompletedProcess([], 0, json.dumps(receipt), "")
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_quit_control.subprocess.run",
                return_value=completed,
            ),
        ):
            result = run_live_quit(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                app_bundle=self.app,
                expected_generation=GENERATION,
                source_set=self.source,
                source_sha256=self.sha,
                source_device=self.device,
                source_inode=self.inode,
                execute=True,
            )
        self.assertEqual(result["quit"]["outcome"], "unknown")
        self.assertTrue(result["manual_inspection_required"])

    def test_execute_ready_receipt_becomes_structured_unknown(self):
        receipt = self.native_receipt(execute=True, status="ready")
        completed = subprocess.CompletedProcess([], 0, json.dumps(receipt), "")
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_quit_control.subprocess.run",
                return_value=completed,
            ),
        ):
            result = run_live_quit(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                app_bundle=self.app,
                expected_generation=GENERATION,
                source_set=self.source,
                source_sha256=self.sha,
                source_device=self.device,
                source_inode=self.inode,
                execute=True,
            )
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["quit"]["outcome"], "unknown")
        self.assertIsNone(result["quit"]["requested"])
        self.assertTrue(result["manual_inspection_required"])

    def test_malformed_execute_status_and_receipt_types_become_unknown(self):
        for malformed in ([], {}, None):
            with self.subTest(malformed=malformed):
                receipt = self.native_receipt(execute=True, status="terminated")
                receipt["status"] = malformed
                completed = subprocess.CompletedProcess([], 0, json.dumps(receipt), "")
                with (
                    patch(
                        "codex_devtools.live_quit_control.platform.system",
                        return_value="Darwin",
                    ),
                    patch(
                        "codex_devtools.live_quit_control.subprocess.run",
                        return_value=completed,
                    ),
                ):
                    result = run_live_quit(
                        PID,
                        expected_bundle_id=LIVE_BUNDLE_ID,
                        app_bundle=self.app,
                        expected_generation=GENERATION,
                        source_set=self.source,
                        source_sha256=self.sha,
                        source_device=self.device,
                        source_inode=self.inode,
                        execute=True,
                    )
                self.assertEqual(result["quit"]["outcome"], "unknown")
                self.assertTrue(result["manual_inspection_required"])

    def test_wrapper_deadline_includes_admission_and_refuses_late_helper_start(self):
        from codex_devtools import live_quit_control

        normalize = live_quit_control._normalized_absolute

        def delayed_normalize(path, *, suffix):
            time.sleep(0.02)
            return normalize(path, suffix=suffix)

        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch("codex_devtools.live_quit_control.DEADLINE_SECONDS", 0.005),
            patch(
                "codex_devtools.live_quit_control._normalized_absolute",
                side_effect=delayed_normalize,
            ),
            patch("codex_devtools.live_quit_control.subprocess.run") as native,
        ):
            result = run_live_quit(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                app_bundle=self.app,
                expected_generation=GENERATION,
                source_set=self.source,
                source_sha256=self.sha,
                source_device=self.device,
                source_inode=self.inode,
                execute=True,
            )
        self.assertEqual(result["phase"], "admission-deadline")
        self.assertEqual(result["quit"]["outcome"], "not_attempted")
        self.assertFalse(result["manual_inspection_required"])
        native.assert_not_called()

    def test_same_absolute_deadline_is_passed_with_only_remaining_timeout(self):
        receipt = self.native_receipt(execute=True, status="terminated")
        completed = subprocess.CompletedProcess([], 0, json.dumps(receipt), "")
        start_ns = 10_000_000_000
        monotonic_values = iter(
            [
                start_ns,
                start_ns + 10_000_000,
                start_ns + 50_000_000,
                start_ns + 80_000_000,
                start_ns + 100_000_000,
            ]
        )
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch("codex_devtools.live_quit_control.DEADLINE_SECONDS", 0.25),
            patch(
                "codex_devtools.live_quit_control.time.clock_gettime_ns",
                side_effect=lambda clock_id: (
                    next(monotonic_values)
                    if clock_id == time.CLOCK_MONOTONIC
                    else self.fail("wrapper requested a different clock")
                ),
            ) as clock_read,
            patch(
                "codex_devtools.live_quit_control.subprocess.run",
                return_value=completed,
            ) as native,
        ):
            result = run_live_quit(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                app_bundle=self.app,
                expected_generation=GENERATION,
                source_set=self.source,
                source_sha256=self.sha,
                source_device=self.device,
                source_inode=self.inode,
                execute=True,
            )
        self.assertEqual(result["status"], "terminated")
        command = native.call_args.args[0]
        deadline_index = command.index("--deadline-monotonic-ns")
        self.assertEqual(command[deadline_index + 1], str(start_ns + 250_000_000))
        self.assertAlmostEqual(native.call_args.kwargs["timeout"], 0.2)
        self.assertEqual(clock_read.call_count, 5)

    @unittest.skipUnless(sys.platform == "darwin", "Darwin clock-coordinate check")
    def test_swift_clock_samples_are_bracketed_by_python_clock_gettime(self):
        probe = Path(__file__).with_name("live_quit_clock_probe.swift")
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            binary = Path(directory) / "live-quit-clock-probe"
            subprocess.run(
                ["/usr/bin/swiftc", str(probe), "-o", str(binary)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            for _ in range(5):
                before = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                result = subprocess.run(
                    [str(binary)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                after = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                swift_sample = int(result.stdout.strip())
                self.assertLessEqual(before, swift_sample)
                self.assertLessEqual(swift_sample, after)

    def test_outer_timeout_is_unknown_and_requires_manual_inspection(self):
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_quit_control.subprocess.run",
                side_effect=subprocess.TimeoutExpired("swift", 35),
            ),
        ):
            result = run_live_quit(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                app_bundle=self.app,
                expected_generation=GENERATION,
                source_set=self.source,
                source_sha256=self.sha,
                source_device=self.device,
                source_inode=self.inode,
                execute=True,
            )
        self.assertEqual(result["quit"]["outcome"], "unknown")
        self.assertIsNone(result["quit"]["requested"])
        self.assertTrue(result["manual_inspection_required"])
        self.assertFalse(result["quit"]["force_quit"])

    def test_native_dialog_after_request_stays_unknown_without_retry(self):
        receipt = self.native_receipt(execute=True, status="stopped")
        completed = subprocess.CompletedProcess([], 1, json.dumps(receipt), "")
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_quit_control.subprocess.run",
                return_value=completed,
            ) as native,
        ):
            result = run_live_quit(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                app_bundle=self.app,
                expected_generation=GENERATION,
                source_set=self.source,
                source_sha256=self.sha,
                source_device=self.device,
                source_inode=self.inode,
                execute=True,
            )
        self.assertTrue(result["quit"]["requested"])
        self.assertEqual(result["quit"]["outcome"], "unknown")
        self.assertTrue(result["manual_inspection_required"])
        self.assertEqual(native.call_count, 1)

    def test_pre_request_refusal_receipt_keeps_quit_not_attempted(self):
        receipt = self.native_receipt(execute=True, status="ready")
        receipt.update(status="stopped", phase="identity")
        completed = subprocess.CompletedProcess([], 1, json.dumps(receipt), "")
        with (
            patch(
                "codex_devtools.live_quit_control.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_quit_control.subprocess.run",
                return_value=completed,
            ),
        ):
            result = run_live_quit(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                app_bundle=self.app,
                expected_generation=GENERATION,
                source_set=self.source,
                source_sha256=self.sha,
                source_device=self.device,
                source_inode=self.inode,
                execute=True,
            )
        self.assertFalse(result["quit"]["requested"])
        self.assertEqual(result["quit"]["outcome"], "not_attempted")
        self.assertFalse(result["manual_inspection_required"])

    def test_cli_defaults_to_read_only_and_only_executes_with_flag(self):
        args = [
            "ax",
            "live-quit",
            "--pid",
            str(PID),
            "--expected-bundle-id",
            LIVE_BUNDLE_ID,
            "--app",
            str(self.app),
            "--expected-generation",
            GENERATION,
            "--set",
            str(self.source),
            "--source-sha256",
            self.sha,
            "--source-device",
            self.device,
            "--source-inode",
            self.inode,
        ]
        with patch(
            "codex_devtools.live_quit_control.run_live_quit",
            return_value={"status": "ready"},
        ) as run:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            self.assertFalse(run.call_args.kwargs["execute"])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main([*args, "--execute"]), 0)
            self.assertTrue(run.call_args.kwargs["execute"])

    def test_swift_checks_full_guards_again_before_single_normal_quit(self):
        source = Path(__file__).parents[1] / "codex_devtools/live_quit.swift"
        text = source.read_text(encoding="utf-8")
        final_guard = text.index('phase = "final-guard"')
        final_check = text.index(
            "_ = try verifyPreflight(deadline: deadline)", final_guard
        )
        request = text.index("quitAccepted = process.terminate()", final_check)
        self.assertLess(final_check, request)
        self.assertIn(
            "let deadline = options.deadlineMonotonicNanoseconds",
            text,
        )
        self.assertIn("clock_gettime(CLOCK_MONOTONIC, &value)", text)
        self.assertIn("AXUIElementSetMessagingTimeout(element, timeout)", text)
        self.assertIn("Double(remaining) / nanosecondsPerSecond", text)
        self.assertIn("guard now < deadline else { break }", text)
        self.assertNotIn("deadline - DispatchTime.now().uptimeNanoseconds", text)
        wrapper = Path(__file__).parents[1] / "codex_devtools/live_quit_control.py"
        wrapper_text = wrapper.read_text(encoding="utf-8")
        self.assertIn("time.clock_gettime_ns(time.CLOCK_MONOTONIC)", wrapper_text)
        self.assertNotIn("time.monotonic_ns()", wrapper_text)
        self.assertIn("process.bundleURL?.path == options.appPath", text)
        self.assertIn(
            'process.executableURL?.path == options.appPath + "/Contents/MacOS/Live"',
            text,
        )
        self.assertIn("expectedHash == options.sha256", text)
        self.assertIn(
            "play == 0 && link == 0 && arrangement == 0 && session == 0", text
        )
        self.assertNotIn("forceTerminate", text)
        self.assertNotIn("terminateNow", text)
        self.assertIn('"force_quit": false', text)

    def test_unavailable_unrelated_descriptions_do_not_hide_required_controls(self):
        source = Path(__file__).parents[1] / "codex_devtools/live_quit.swift"
        text = source.read_text(encoding="utf-8")
        optional = text[
            text.index("func optionalDescription(") : text.index("func role(")
        ]
        self.assertIn("catch let failure as AXReadFailure", optional)
        self.assertIn("failure.code == .attributeUnsupported", optional)
        self.assertIn(
            "failure.attribute == (kAXDescriptionAttribute as String)", optional
        )
        self.assertNotIn("catch {", optional)
        self.assertIn("return nil", optional)
        self.assertIn("if error == .noValue { return nil }", text)

        traversal = text[text.index("func tree(") : text.index("func checkboxValue(")]
        self.assertIn("while !queue.isEmpty", traversal)
        self.assertIn("result.count <= maxElements", traversal)
        self.assertIn("depth < maxDepth", traversal)
        self.assertIn("queue.append(contentsOf: children.map", traversal)

        transport = text[
            text.index("func stoppedTransport(") : text.index("func verifyPreflight(")
        ]
        self.assertIn("for element in try tree(window)", transport)
        self.assertIn('try optionalDescription(element) ?? ""', transport)
        for description, identifier in (
            ("Play", "Transport.Play"),
            ("Link", "Transport.AbletonLink"),
            ("Arrangement Record", "Transport.GlobalRecord"),
            ("Session Record", "Transport.SessionRecord"),
        ):
            self.assertIn(f'("{description}", "{identifier}")', transport)
        self.assertIn("found.count == definitions.count", transport)
        self.assertIn("duplicate exact transport control", transport)
        self.assertIn("transport \\(description) identifier changed", transport)
        self.assertIn(
            "play == 0 && link == 0 && arrangement == 0 && session == 0",
            transport,
        )


if __name__ == "__main__":
    unittest.main()
