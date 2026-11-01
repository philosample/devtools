"""Receipt checks for MIDI Monitor captures after inputs are disconnected."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from codex_devtools.midi_assertions import verify_capture_receipt
from codex_devtools.midi_capture import source_identity


class DisconnectedCaptureReceiptTests(unittest.TestCase):
    def test_saved_empty_streams_match_configured_and_resolved_receipt_sources(self):
        source = {"portInputStream": [{"uniqueID": 123, "name": "Capture"}]}
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / "capture.mmon"
            capture.write_bytes(b"saved capture with inputs disconnected")
            digest = hashlib.sha256(capture.read_bytes()).hexdigest()
            document = {
                "document_sha256": digest,
                "settings": {
                    "streamSettings": {
                        "portInputStream": [],
                        "spyingInputStream": [],
                    }
                },
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

    def test_source_identity_stays_strict_by_default(self):
        with self.assertRaisesRegex(ValueError, "at least one valid portInputStream"):
            source_identity({"portInputStream": []})
        self.assertEqual(
            source_identity({"portInputStream": []}, require_source=False),
            {"portInputStream": []},
        )

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
                "settings": {
                    "streamSettings": {
                        "portInputStream": [],
                        "spyingInputStream": [],
                    }
                },
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

    def test_disconnected_capture_validates_every_saved_stream(self):
        source = {"portInputStream": [{"uniqueID": 123, "name": "Capture"}]}
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / "capture.mmon"
            capture.write_bytes(b"saved capture with inputs disconnected")
            digest = hashlib.sha256(capture.read_bytes()).hexdigest()
            document = {
                "document_sha256": digest,
                "settings": {"streamSettings": {}},
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

            cases = (
                (
                    {"spyingInputStream": []},
                    "no portInputStream setting",
                ),
                (
                    {
                        "portInputStream": [],
                        "spyingInputStream": [],
                        "otherInputStream": [{"uniqueID": 456}],
                    },
                    "not disconnected",
                ),
                (
                    {
                        "portInputStream": [],
                        "spyingInputStream": [],
                        "otherInputStream": "bogus",
                    },
                    "source stream settings must be lists",
                ),
                (
                    {
                        "portInputStream": [],
                        "spyingInputStream": [],
                        "otherInputStream": [{"uniqueID": 456}, {"uniqueID": 456}],
                    },
                    "duplicate endpoint uniqueID",
                ),
                (
                    {
                        "portInputStream": [],
                        "spyingInputStream": [],
                        "otherInputStream": [{"uniqueID": True}],
                    },
                    "integer uniqueID",
                ),
            )
            for streams, message in cases:
                with (
                    self.subTest(streams=streams),
                    self.assertRaisesRegex(ValueError, message),
                ):
                    document["settings"]["streamSettings"] = streams
                    verify_capture_receipt(capture, document, receipt_path)

    def test_disconnected_capture_still_rejects_hash_and_event_count_mismatch(self):
        source = {"portInputStream": [{"uniqueID": 123, "name": "Capture"}]}
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / "capture.mmon"
            capture.write_bytes(b"saved capture with inputs disconnected")
            digest = hashlib.sha256(capture.read_bytes()).hexdigest()
            document = {
                "document_sha256": digest,
                "settings": {
                    "streamSettings": {
                        "portInputStream": [],
                        "spyingInputStream": [],
                    }
                },
                "events": [{"type": "clock"}],
            }
            receipt = {
                "schema": "devtools-midi-capture-v1",
                "status": "captured",
                "capture_file": capture.name,
                "capture_sha256": "0" * 64,
                "configured_settings": {"streamSettings": source},
                "resolved_settings": {"streamSettings": source},
                "event_count": 1,
            }
            receipt_path = Path(folder) / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))

            with self.assertRaisesRegex(ValueError, "SHA-256"):
                verify_capture_receipt(capture, document, receipt_path)

            receipt["capture_sha256"] = digest
            receipt["event_count"] = 2
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "event count"):
                verify_capture_receipt(capture, document, receipt_path)

            receipt["event_count"] = True
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "event count"):
                verify_capture_receipt(capture, document, receipt_path)

    def test_saved_capture_with_connected_input_is_rejected(self):
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

            for saved in (source, other_source):
                document["settings"]["streamSettings"] = saved
                with (
                    self.subTest(saved=saved),
                    self.assertRaisesRegex(ValueError, "not disconnected"),
                ):
                    verify_capture_receipt(capture, document, receipt_path)


if __name__ == "__main__":
    unittest.main()
