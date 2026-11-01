"""Receipt checks for MIDI Monitor captures after inputs are disconnected."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from codex_devtools.midi_assertions import verify_capture_receipt


class DisconnectedCaptureReceiptTests(unittest.TestCase):
    def test_saved_empty_streams_match_configured_and_resolved_receipt_sources(self):
        source = {"portInputStream": [{"uniqueID": 123, "name": "Capture"}]}
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / "capture.mmon"
            capture.write_bytes(b"saved capture with inputs disconnected")
            digest = hashlib.sha256(capture.read_bytes()).hexdigest()
            document = {
                "document_sha256": digest,
                "settings": {"streamSettings": {"portInputStream": []}},
                "events": [{"type": "clock"}],
            }
            receipt = {
                "schema": "devtools-midi-capture-v1",
                "status": "captured",
                "capture_file": capture.name,
                "capture_sha256": digest,
                "configured_settings": {"streamSettings": source},
                "resolved_settings": {"streamSettings": source},
                "event_count": 1,
            }
            receipt_path = Path(folder) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))

            result = verify_capture_receipt(capture, document, receipt_path)

            self.assertTrue(result["verified"])
            self.assertEqual(result["source_identity"], {"portInputStream": [123]})

    def test_disconnected_capture_rejects_mismatched_configured_and_resolved_sources(
        self,
    ):
        configured = {"portInputStream": [{"uniqueID": 123, "name": "Capture"}]}
        forged_resolved = {"portInputStream": [{"uniqueID": 456, "name": "Other"}]}
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / "capture.mmon"
            capture.write_bytes(b"saved capture with inputs disconnected")
            digest = hashlib.sha256(capture.read_bytes()).hexdigest()
            document = {
                "document_sha256": digest,
                "settings": {"streamSettings": {"portInputStream": []}},
                "events": [],
            }
            receipt = {
                "schema": "devtools-midi-capture-v1",
                "status": "captured",
                "capture_file": capture.name,
                "capture_sha256": digest,
                "configured_settings": {"streamSettings": configured},
                "resolved_settings": {"streamSettings": forged_resolved},
                "event_count": 0,
            }
            receipt_path = Path(folder) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))

            with self.assertRaisesRegex(ValueError, "configured/resolved"):
                verify_capture_receipt(capture, document, receipt_path)

    def test_saved_capture_with_different_connected_input_is_rejected(self):
        source = {"portInputStream": [{"uniqueID": 123, "name": "Capture"}]}
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / "capture.mmon"
            capture.write_bytes(b"saved capture with inputs connected")
            digest = hashlib.sha256(capture.read_bytes()).hexdigest()
            other_source = {"portInputStream": [{"uniqueID": 456, "name": "Other"}]}
            document = {
                "document_sha256": digest,
                "settings": {"streamSettings": other_source},
                "events": [],
            }
            receipt = {
                "schema": "devtools-midi-capture-v1",
                "status": "captured",
                "capture_file": capture.name,
                "capture_sha256": digest,
                "configured_settings": {"streamSettings": source},
                "resolved_settings": {"streamSettings": source},
                "event_count": 0,
            }
            receipt_path = Path(folder) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))

            with self.assertRaisesRegex(ValueError, "source identity differs"):
                verify_capture_receipt(capture, document, receipt_path)


if __name__ == "__main__":
    unittest.main()
