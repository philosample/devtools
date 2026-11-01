import hashlib
import json
import plistlib
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_devtools.midi_capture import (
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
                return subprocess.CompletedProcess(command, 0, "closed", "")

            real_wait = _wait_for_final_capture

            def bounded_wait(path):
                return real_wait(
                    path, timeout=0.02, stable_for=0.005, poll_interval=0.002
                )

            with (
                patch("codex_devtools.midi_capture.sys.platform", "darwin"),
                patch(
                    "codex_devtools.midi_capture.subprocess.run", side_effect=automation
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


if __name__ == "__main__":
    unittest.main()
