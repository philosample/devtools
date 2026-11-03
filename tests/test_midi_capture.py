import hashlib
import json
import plistlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_devtools.midi_capture import (
    _validate_ready_marker,
    _wait_for_final_capture,
    capture,
    make_template,
    source_identity,
)
from codex_devtools.midi_monitor import parse_document


class CaptureConfigurationTests(unittest.TestCase):
    def test_identity_ignores_display_name_and_empty_streams(self):
        self.assertEqual(
            source_identity({"portInputStream": [{"uniqueID": 1, "name": "old"}]}),
            source_identity(
                {
                    "portInputStream": [{"uniqueID": 1, "name": "new"}],
                    "spyingInputStream": [],
                }
            ),
        )
        with self.assertRaises(ValueError):
            source_identity({"portInputStream": [{"name": "no identity"}]})
        with self.assertRaisesRegex(ValueError, "at least one valid portInputStream"):
            source_identity({"portOutputStream": [{"uniqueID": 2}]})
        with self.assertRaisesRegex(ValueError, "endpoint objects"):
            source_identity({"portInputStream": "bogus"})
        with self.assertRaisesRegex(ValueError, "integer uniqueID"):
            source_identity({"portInputStream": [{"uniqueID": True}]})
        with self.assertRaisesRegex(ValueError, "duplicate endpoint"):
            source_identity({"portInputStream": [{"uniqueID": 1}, {"uniqueID": 1}]})

    def test_template_resolves_only_unique_online_source(self):
        inventory = [
            {
                "direction": "source",
                "name": "Capture",
                "displayName": "IAC Capture",
                "uniqueID": 123,
                "offline": False,
            }
        ]
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("codex_devtools.midi_capture.endpoints", return_value=inventory),
        ):
            out = Path(folder) / "template.mmon"
            make_template(out, "IAC Capture")
            document = parse_document(out)
            self.assertEqual(document["events"], [])
            self.assertEqual(
                document["settings"]["streamSettings"]["portInputStream"][0][
                    "uniqueID"
                ],
                123,
            )
            with self.assertRaises(ValueError):
                make_template(out, "Capture")
            inventory.append(dict(inventory[0]))
            with self.assertRaises(ValueError):
                make_template(Path(folder) / "duplicate.mmon", "Capture")


class CaptureSaveSettlementTests(unittest.TestCase):
    @staticmethod
    def document(*, connected):
        streams = (
            {"portInputStream": [{"uniqueID": 123}]}
            if connected
            else {
                "portInputStream": [],
                "spyingInputStream": [],
            }
        )
        return plistlib.dumps(
            {"version": 2, "streamSettings": streams},
            fmt=plistlib.FMT_BINARY,
        )

    def test_waits_for_delayed_atomic_disconnected_save(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.mmon"
            path.write_bytes(self.document(connected=True))
            final_bytes = self.document(connected=False)

            def finish_save():
                time.sleep(0.08)
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(final_bytes)
                temporary.replace(path)

            writer = threading.Thread(target=finish_save)
            writer.start()
            try:
                parsed, digest = _wait_for_final_capture(
                    path, timeout=1.0, stable_for=0.05, poll_interval=0.01
                )
            finally:
                writer.join()

            self.assertEqual(
                parsed["settings"]["streamSettings"]["portInputStream"], []
            )
            self.assertEqual(digest, hashlib.sha256(final_bytes).hexdigest())
            self.assertEqual(parsed["document_sha256"], digest)

    def test_persistent_connected_document_fails_after_bounded_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.mmon"
            path.write_bytes(self.document(connected=True))
            started = time.monotonic()
            with self.assertRaisesRegex(
                ValueError, "inputs were not disconnected before save settled"
            ):
                _wait_for_final_capture(
                    path, timeout=0.06, stable_for=0.02, poll_interval=0.01
                )
            self.assertLess(time.monotonic() - started, 0.5)

    def test_missing_or_malformed_saved_stream_settings_fail_closed(self):
        malformed_documents = {
            "missing streamSettings": {"version": 2},
            "non-mapping streamSettings": {
                "version": 2,
                "streamSettings": [],
            },
            "missing portInputStream": {"version": 2, "streamSettings": {}},
            "non-list portInputStream": {
                "version": 2,
                "streamSettings": {"portInputStream": "bogus"},
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.mmon"
            for label, document in malformed_documents.items():
                with self.subTest(label=label):
                    path.write_bytes(plistlib.dumps(document, fmt=plistlib.FMT_BINARY))
                    with self.assertRaisesRegex(
                        ValueError, "did not become parseable and stable"
                    ):
                        _wait_for_final_capture(
                            path, timeout=0.01, stable_for=0.005, poll_interval=0.002
                        )

    def test_malformed_saved_settings_write_failed_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template = root / "template.mmon"
            template_bytes = self.document(connected=True)
            template.write_bytes(template_bytes)
            out = root / "capture-run"

            def automation(command, **kwargs):
                Path(command[2]).write_bytes(
                    plistlib.dumps({"version": 2}, fmt=plistlib.FMT_BINARY)
                )
                Path(command[3]).write_bytes(template_bytes)
                Path(command[8]).write_text(command[9])

                class Process:
                    args = command
                    returncode = None
                    pid = 12345

                    def poll(self):
                        return self.returncode

                    def communicate(self, timeout=None):
                        self.returncode = 0
                        return "closed", ""

                    def kill(self):
                        self.returncode = -9

                return Process()

            real_wait = _wait_for_final_capture

            def bounded_wait(path):
                return real_wait(
                    path, timeout=0.02, stable_for=0.005, poll_interval=0.002
                )

            with (
                patch("codex_devtools.midi_capture.sys.platform", "darwin"),
                patch(
                    "codex_devtools.midi_capture.subprocess.Popen",
                    side_effect=automation,
                ),
                patch(
                    "codex_devtools.midi_capture._wait_for_final_capture",
                    side_effect=bounded_wait,
                ),
                self.assertRaisesRegex(ValueError, "partial evidence retained"),
            ):
                capture(template, out, seconds=1)

            receipt = json.loads((out / "receipt.json").read_text())
            saved_capture = out / receipt["capture_file"]
            self.assertEqual(receipt["status"], "failed")
            self.assertIn("streamSettings must be a dictionary", receipt["error"])
            self.assertEqual(
                receipt["capture_sha256"],
                hashlib.sha256(saved_capture.read_bytes()).hexdigest(),
            )


class CaptureReadyMarkerTests(unittest.TestCase):
    class LiveProcess:
        def __init__(self, returncode=None):
            self.returncode = returncode
            self.pid = 789

        def poll(self):
            return self.returncode

    def test_valid_marker_is_tied_to_live_run_and_preflight_source(self):
        source_identity = {"portInputStream": [123]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            preflight = root / "preflight.mmon"
            preflight.write_bytes(
                plistlib.dumps(
                    {
                        "version": 2,
                        "streamSettings": {
                            "portInputStream": [{"uniqueID": 123}],
                            "spyingInputStream": [],
                        },
                    },
                    fmt=plistlib.FMT_BINARY,
                )
            )
            expected = {
                "schema": "devtools-midi-capture-ready-v1",
                "run_id": "run-123",
                "capture_process_pid": 456,
                "capture_file": "capture-run.mmon",
                "capture_document_url": "file:///tmp/capture-run.mmon",
                "template_sha256": "a" * 64,
                "configured_source_identity": source_identity,
            }
            marker = root / "capture-ready.json"
            marker.write_text(json.dumps(expected))

            ready = _validate_ready_marker(
                marker,
                expected,
                self.LiveProcess(),
                preflight,
                source_identity,
            )

            self.assertEqual(ready["run_id"], "run-123")
            self.assertEqual(ready["capture_process_pid"], 456)
            self.assertEqual(ready["automation_process_pid"], 789)
            self.assertEqual(
                ready["preflight_sha256"],
                hashlib.sha256(preflight.read_bytes()).hexdigest(),
            )

    def test_partial_stale_mismatched_and_exited_markers_fail_closed(self):
        source_identity = {"portInputStream": [123]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            preflight = root / "preflight.mmon"
            preflight.write_bytes(
                plistlib.dumps(
                    {
                        "version": 2,
                        "streamSettings": {
                            "portInputStream": [{"uniqueID": 123}],
                        },
                    },
                    fmt=plistlib.FMT_BINARY,
                )
            )
            expected = {
                "schema": "devtools-midi-capture-ready-v1",
                "run_id": "current-run",
                "capture_process_pid": 456,
                "capture_file": "capture-run.mmon",
                "capture_document_url": "file:///tmp/capture-run.mmon",
                "template_sha256": "a" * 64,
                "configured_source_identity": source_identity,
            }
            marker = root / "capture-ready.json"
            cases = (
                ("partial", "{", self.LiveProcess(), "incomplete"),
                (
                    "stale run",
                    json.dumps({**expected, "run_id": "previous-run"}),
                    self.LiveProcess(),
                    "does not match",
                ),
                (
                    "wrong process",
                    json.dumps({**expected, "capture_process_pid": 789}),
                    self.LiveProcess(),
                    "does not match",
                ),
                (
                    "process already exited",
                    json.dumps(expected),
                    self.LiveProcess(returncode=0),
                    "exited before readiness",
                ),
            )
            for label, contents, process, message in cases:
                with self.subTest(label=label):
                    marker.write_text(contents)
                    with self.assertRaisesRegex(ValueError, message):
                        _validate_ready_marker(
                            marker,
                            expected,
                            process,
                            preflight,
                            source_identity,
                        )
            marker.write_text(json.dumps(expected))
            with self.assertRaisesRegex(ValueError, "differ from configured"):
                _validate_ready_marker(
                    marker,
                    expected,
                    self.LiveProcess(),
                    preflight,
                    {"portInputStream": [999]},
                )

    def test_applescript_publishes_marker_after_clear_at_capture_start(self):
        script = (
            Path(__file__).parents[1] / "codex_devtools" / "midi_capture.applescript"
        ).read_text()
        clear_position = script.index('click button "Clear" of eventContainer')
        boundary_position = script.index("set startedAt to (current date)")
        publish_position = script.index("set readyMarkerTempPath")
        capture_wait_position = script.index("delay secondsToCapture")
        self.assertLess(clear_position, boundary_position)
        self.assertLess(boundary_position, publish_position)
        self.assertLess(publish_position, capture_wait_position)
        self.assertIn("/bin/ln ", script)


if __name__ == "__main__":
    unittest.main()
