import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_devtools.midi_source_capture import _assess, capture_source

SOURCE_UID = 4242


def native_capture():
    packet = {
        "sequence": 0,
        "sourceUniqueID": SOURCE_UID,
        "coreMIDITimestamp": 123456,
        "observedMonotonicNanoseconds": 987654,
        "bytes": [0x92, 0x24, 0x00],
    }
    return {
        "status": "captured",
        "source": {
            "uniqueID": SOURCE_UID,
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


def fake_toolchain(native, *, calls):
    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Swift test toolchain\n", "")
        if command[-1] == "--show-sdk-path":
            return subprocess.CompletedProcess(command, 0, "/mock/MacOSX.sdk\n", "")
        if command[-1] == "--show-sdk-version":
            return subprocess.CompletedProcess(command, 0, "26.0\n", "")
        if "-o" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, json.dumps(native), "")

    return run


class SourceCaptureAssessmentTests(unittest.TestCase):
    def test_retains_exact_raw_packet_bytes_and_pins_source(self):
        native = native_capture()
        self.assertEqual(
            _assess(native, source_uid=SOURCE_UID, max_events=8, max_bytes=64),
            (True, None),
        )

    def test_rejects_source_uid_mismatch(self):
        native = native_capture()
        native["source"]["uniqueID"] += 1
        self.assertIn(
            "pinned online source UID",
            _assess(native, source_uid=SOURCE_UID, max_events=8, max_bytes=64)[1],
        )

    def test_rejects_packet_source_mismatch(self):
        native = native_capture()
        native["packets"][0]["sourceUniqueID"] += 1
        self.assertIn(
            "packet source identity",
            _assess(native, source_uid=SOURCE_UID, max_events=8, max_bytes=64)[1],
        )

    def test_rejects_packet_sequence_gap(self):
        native = native_capture()
        native["packets"][0]["sequence"] = 2
        self.assertIn(
            "packet source identity",
            _assess(native, source_uid=SOURCE_UID, max_events=8, max_bytes=64)[1],
        )

    def test_rejects_caps_as_potentially_truncated(self):
        native = native_capture()
        self.assertIn(
            "truncated",
            _assess(native, source_uid=SOURCE_UID, max_events=1, max_bytes=64)[1],
        )
        self.assertIn(
            "truncated",
            _assess(native, source_uid=SOURCE_UID, max_events=8, max_bytes=3)[1],
        )

    def test_rejects_missing_cleanup_and_byte_count_mismatch(self):
        native = native_capture()
        native["cleanup"]["clientDisposed"] = False
        self.assertIn(
            "cleaned up",
            _assess(native, source_uid=SOURCE_UID, max_events=8, max_bytes=64)[1],
        )
        native = native_capture()
        native["byteCount"] = 2
        self.assertIn(
            "differs",
            _assess(native, source_uid=SOURCE_UID, max_events=8, max_bytes=64)[1],
        )


class SourceCaptureCommandTests(unittest.TestCase):
    def test_saves_exact_packets_and_passes_all_bounds_and_uid(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "capture.json"
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
                    side_effect=fake_toolchain(native_capture(), calls=calls),
                ),
            ):
                result = capture_source(out, SOURCE_UID, 0.5, 8, 64)
            self.assertEqual(result["status"], "captured")
            receipt = json.loads(out.read_text())
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
            self.assertEqual(calls[-1][-4:], [str(SOURCE_UID), "0.5", "8", "64"])

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

    def test_listener_source_has_no_midi_output_or_send_api(self):
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


if __name__ == "__main__":
    unittest.main()
