import io
import json
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from codex_devtools.cli import main
from codex_devtools.midi_source_capture import (
    MAX_PACKET_DATA_BYTES,
    READY_SCHEMA,
    _assess,
    capture_source,
)

SOURCE_UID = 4242
PROCESS_ID = 13579


def native_capture(run_id, *, source_uid=SOURCE_UID, process_id=PROCESS_ID):
    packet = {
        "sequence": 0,
        "sourceUniqueID": source_uid,
        "coreMIDITimestamp": 123456,
        "observedMonotonicNanoseconds": 987654,
        "bytes": [0x92, 0x24, 0x00],
    }
    return {
        "status": "captured",
        "runID": run_id,
        "processID": process_id,
        "requestedSourceUID": source_uid,
        "source": {
            "uniqueID": source_uid,
            "offline": False,
            "name": "IAC Source",
            "displayName": "IAC Source",
        },
        "packets": [packet],
        "packetCount": 1,
        "byteCount": 3,
        "cleanup": {
            "sourceDisconnected": True,
            "inputPortDisposed": True,
            "clientDisposed": True,
        },
    }


def ready_marker(run_id, *, source_uid=SOURCE_UID, process_id=PROCESS_ID):
    return {
        "event": "READY",
        "schema": READY_SCHEMA,
        "run_id": run_id,
        "source_uid": source_uid,
        "process_id": process_id,
        "connected": True,
        "observed_monotonic_nanoseconds": 1234567,
    }


def fake_toolchain(*, calls):
    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Swift test toolchain\n", "")
        if command[-1] == "--show-sdk-path":
            return subprocess.CompletedProcess(command, 0, "/mock/MacOSX.sdk\n", "")
        if command[-1] == "--show-sdk-version":
            return subprocess.CompletedProcess(command, 0, "26.0\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


class FakeProcess:
    def __init__(self, command, *, mode="valid", ready_release=None, final_read=None):
        self.command = command
        self.pid = PROCESS_ID
        self.returncode = None
        source_uid = int(command[1])
        run_id = command[-1]
        marker = ready_marker(run_id, source_uid=source_uid, process_id=self.pid)
        native = native_capture(run_id, source_uid=source_uid, process_id=self.pid)
        if mode == "stale-run":
            marker["run_id"] = "00000000-0000-4000-8000-000000000000"
        elif mode == "wrong-source":
            marker["source_uid"] += 1
        elif mode == "wrong-pid":
            marker["process_id"] += 1
        elif mode == "unknown-marker":
            marker["event"] = "LISTENING"
        elif mode == "unknown-field":
            marker["unexpected"] = True
        marker_line = json.dumps(marker, separators=(",", ":"))
        native_line = json.dumps(native, separators=(",", ":"))
        if ready_release is not None and final_read is not None:
            self.stdout = StagedStdout(
                marker_line + "\n", native_line + "\n", ready_release, final_read
            )
            self.stderr = io.StringIO()
            self.dead_before_ready = mode == "dead-before-ready"
            return
        if mode == "partial-marker":
            self.stdout = io.StringIO(marker_line + native_line + "\n")
        elif mode == "no-marker":
            self.stdout = io.StringIO(native_line + "\n")
        else:
            self.stdout = io.StringIO(marker_line + "\n" + native_line + "\n")
        self.stderr = io.StringIO()
        self.dead_before_ready = mode == "dead-before-ready"

    def poll(self):
        return 1 if self.dead_before_ready else self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class StagedStdout:
    def __init__(self, ready_line, final_line, ready_release, final_read):
        self.ready_line = ready_line
        self.final_line = final_line
        self.ready_release = ready_release
        self.final_read = final_read

    def __iter__(self):
        yield self.ready_line
        if not self.ready_release.wait(timeout=2):
            return
        self.final_read.set()
        yield self.final_line


class SourceCaptureAssessmentTests(unittest.TestCase):
    def test_retains_exact_raw_packet_bytes_and_pins_source_and_process(self):
        native = native_capture("run-1")
        self.assertEqual(
            _assess(
                native,
                source_uid=SOURCE_UID,
                max_events=8,
                max_bytes=64,
                run_id="run-1",
                process_id=PROCESS_ID,
            ),
            (True, None),
        )

    def test_rejects_source_uid_mismatch(self):
        native = native_capture("run-1")
        native["source"]["uniqueID"] += 1
        self.assertIn(
            "pinned online source UID",
            _assess(
                native,
                source_uid=SOURCE_UID,
                max_events=8,
                max_bytes=64,
                run_id="run-1",
                process_id=PROCESS_ID,
            )[1],
        )

    def test_rejects_packet_source_mismatch_and_sequence_gap(self):
        native = native_capture("run-1")
        native["packets"][0]["sourceUniqueID"] += 1
        self.assertIn(
            "sequence or source identity",
            _assess(
                native,
                source_uid=SOURCE_UID,
                max_events=8,
                max_bytes=64,
                run_id="run-1",
                process_id=PROCESS_ID,
            )[1],
        )
        native = native_capture("run-1")
        native["packets"][0]["sequence"] = 2
        self.assertIn(
            "sequence or source identity",
            _assess(
                native,
                source_uid=SOURCE_UID,
                max_events=8,
                max_bytes=64,
                run_id="run-1",
                process_id=PROCESS_ID,
            )[1],
        )

    def test_accepts_full_fixed_payload_and_rejects_oversize_without_truncation(self):
        native = native_capture("run-1")
        native["packets"][0]["bytes"] = list(range(MAX_PACKET_DATA_BYTES))
        native["byteCount"] = MAX_PACKET_DATA_BYTES
        self.assertIsNone(
            _assess(
                native,
                source_uid=SOURCE_UID,
                max_events=2,
                max_bytes=1024,
                run_id="run-1",
                process_id=PROCESS_ID,
            )[1]
        )
        native["packets"][0]["bytes"] = [0] * (MAX_PACKET_DATA_BYTES + 1)
        native["byteCount"] = MAX_PACKET_DATA_BYTES + 1
        self.assertIn(
            "fixed packet storage",
            _assess(
                native,
                source_uid=SOURCE_UID,
                max_events=2,
                max_bytes=1024,
                run_id="run-1",
                process_id=PROCESS_ID,
            )[1],
        )

    def test_rejects_caps_missing_cleanup_and_byte_count_mismatch(self):
        native = native_capture("run-1")
        self.assertIn(
            "truncated",
            _assess(
                native,
                source_uid=SOURCE_UID,
                max_events=1,
                max_bytes=64,
                run_id="run-1",
                process_id=PROCESS_ID,
            )[1],
        )
        native = native_capture("run-1")
        native["cleanup"]["clientDisposed"] = False
        self.assertIn(
            "cleaned up",
            _assess(
                native,
                source_uid=SOURCE_UID,
                max_events=8,
                max_bytes=64,
                run_id="run-1",
                process_id=PROCESS_ID,
            )[1],
        )
        native = native_capture("run-1")
        native["byteCount"] = 2
        self.assertIn(
            "differs",
            _assess(
                native,
                source_uid=SOURCE_UID,
                max_events=8,
                max_bytes=64,
                run_id="run-1",
                process_id=PROCESS_ID,
            )[1],
        )


class SourceCaptureCommandTests(unittest.TestCase):
    def run_capture(
        self,
        directory,
        *,
        mode="valid",
        callback=None,
        ready_release=None,
        final_read=None,
    ):
        calls = []
        process_calls = []

        def popen(command, **kwargs):
            process_calls.append((command, kwargs))
            return FakeProcess(
                command, mode=mode, ready_release=ready_release, final_read=final_read
            )

        with (
            patch("codex_devtools.midi_source_capture.sys.platform", "darwin"),
            patch(
                "codex_devtools.midi_source_capture.platform.platform",
                return_value="macOS test",
            ),
            patch(
                "codex_devtools.midi_source_capture.platform.mac_ver",
                return_value=("15.0", "", ""),
            ),
            patch(
                "codex_devtools.midi_source_capture.subprocess.run",
                side_effect=fake_toolchain(calls=calls),
            ),
            patch(
                "codex_devtools.midi_source_capture.subprocess.Popen", side_effect=popen
            ),
        ):
            result = capture_source(
                Path(directory) / "capture.json",
                SOURCE_UID,
                0.5,
                8,
                64,
                on_ready=callback,
            )
        return result, calls, process_calls

    def test_ready_is_published_live_then_exact_packet_receipt_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = []
            result, calls, process_calls = self.run_capture(
                directory, callback=ready.append
            )
            self.assertEqual(result["status"], "captured")
            receipt = json.loads((Path(directory) / "capture.json").read_text())
            self.assertTrue(receipt["ready_validated"])
            self.assertEqual(ready, [receipt["ready"]])
            self.assertEqual(ready[0]["event"], "READY")
            self.assertEqual(ready[0]["source_uid"], SOURCE_UID)
            self.assertEqual(ready[0]["process_id"], PROCESS_ID)
            self.assertEqual(receipt["source_pin"]["uniqueID"], SOURCE_UID)
            self.assertEqual(
                receipt["native"]["packets"][0]["bytes"], [0x92, 0x24, 0x00]
            )
            self.assertEqual(
                receipt["native"]["packets"][0]["coreMIDITimestamp"], 123456
            )
            self.assertEqual(
                receipt["native"]["packets"][0]["observedMonotonicNanoseconds"], 987654
            )
            self.assertEqual(calls[-1][0], "xcrun")
            command, kwargs = process_calls[0]
            self.assertEqual(command[-5:-1], [str(SOURCE_UID), "0.5", "8", "64"])
            self.assertEqual(command[-1], receipt["run_id"])
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)

    def test_ready_callback_happens_before_child_final_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            ready_release = threading.Event()
            final_read = threading.Event()
            ready = []

            def release_after_ready(marker):
                self.assertFalse(final_read.is_set())
                ready.append(marker)
                ready_release.set()

            result, _, _ = self.run_capture(
                directory,
                callback=release_after_ready,
                ready_release=ready_release,
                final_read=final_read,
            )
            self.assertEqual(result["status"], "captured")
            self.assertEqual(len(ready), 1)
            self.assertTrue(final_read.is_set())

    def test_stale_partial_unknown_and_wrong_identity_ready_markers_fail_closed(self):
        for mode in (
            "stale-run",
            "wrong-source",
            "wrong-pid",
            "unknown-marker",
            "unknown-field",
            "partial-marker",
            "no-marker",
            "dead-before-ready",
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                ready = []
                result, _, _ = self.run_capture(
                    directory, mode=mode, callback=ready.append
                )
                self.assertEqual(result["status"], "failed", mode)
                self.assertFalse(result.get("ready_validated", False), mode)
                self.assertEqual(ready, [], mode)

    def test_listener_source_has_no_midi_output_or_send_api_and_rejects_oversize_before_copy(
        self,
    ):
        source = (
            Path(__file__).parents[1] / "codex_devtools" / "midi_source_capture.swift"
        )
        text = source.read_text()
        for forbidden in (
            "MIDIOutputPortCreate",
            "MIDISend",
            "MIDIDestinationCreate",
            "MIDIReceivedEventList",
            "MIDIGetDestination",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("MIDIInputPortCreate", text)
        self.assertIn("MIDIPortConnectSource", text)
        self.assertIn("length > MAX_PACKET_DATA_BYTES", text)
        self.assertLess(
            text.index("length > MAX_PACKET_DATA_BYTES"),
            text.index("withUnsafeBytes(of: packet.pointee.data)"),
        )
        self.assertLess(
            text.index("guard length <= raw.count"),
            text.index("Array(raw.prefix(length))"),
        )

    def test_cli_forwards_validated_ready_line_before_final_receipt(self):
        marker = ready_marker("run-1")

        def capture(*args, on_ready=None, **kwargs):
            on_ready(marker)
            return {"status": "captured", "ready": marker}

        output = StringIO()
        with (
            patch(
                "codex_devtools.midi_source_capture.capture_source", side_effect=capture
            ),
            redirect_stdout(output),
        ):
            status = main(
                [
                    "midi",
                    "source-capture",
                    "--source-uid",
                    str(SOURCE_UID),
                    "--out",
                    "/private/tmp/capture.json",
                ]
            )
        lines = output.getvalue().splitlines()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(lines[0]), marker)
        self.assertEqual(
            json.loads("\n".join(lines[1:])), {"status": "captured", "ready": marker}
        )

    def test_invalid_bounds_fail_before_toolchain(self):
        with tempfile.TemporaryDirectory() as directory:
            for kwargs in ({"seconds": 31}, {"max_events": 0}, {"max_bytes": 1048577}):
                with (
                    patch("codex_devtools.midi_source_capture.sys.platform", "darwin"),
                    patch("codex_devtools.midi_source_capture.subprocess.run") as run,
                ):
                    with self.assertRaises(ValueError):
                        capture_source(
                            Path(directory) / "capture.json", SOURCE_UID, **kwargs
                        )
                    run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
