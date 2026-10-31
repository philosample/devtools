import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from codex_devtools.midi_loopback import FIXTURE, _assess, loopback


def passing_native():
    return {
        "status": "passed", "sentEvents": 4, "receivedEvents": 4,
        "events": [{"bytes": event, "scheduledTimestamp": 0, "observedMonotonicNanoseconds": i + 1} for i, event in enumerate(FIXTURE)],
        "cleanup": {"output_port": True, "virtual_destination": True, "client": True},
    }


class LoopbackAssessmentTests(unittest.TestCase):
    def test_accepts_exact_positive_fixture_with_cleanup(self):
        self.assertEqual(_assess(passing_native()), (True, None))

    def test_rejects_empty_capture(self):
        native = passing_native()
        native.update(sentEvents=0, receivedEvents=0, events=[])
        self.assertFalse(_assess(native)[0])

    def test_rejects_wrong_order_or_bytes(self):
        native = passing_native()
        native["events"] = list(reversed(native["events"]))
        self.assertIn("bytes or event order", _assess(native)[1])

    def test_rejects_unconfirmed_cleanup(self):
        native = passing_native()
        native["cleanup"]["client"] = False
        self.assertIn("disposed", _assess(native)[1])

    def test_bounded_process_timeout_is_saved_as_failure(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        def fake_run(*args, **kwargs):
            if args[0][0] == "xcrun" and "--show-sdk-version" in args[0]:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="15.7\n", stderr="")
            if args[0][0] == "xcrun" and "-o" in args[0]:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            if args[0][0] == "xcrun":
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="Swift version mock\n", stderr="")
            raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))

        with tempfile.TemporaryDirectory() as folder, patch("codex_devtools.midi_loopback.platform.platform", return_value="test macOS"), patch("codex_devtools.midi_loopback.platform.mac_ver", return_value=("15.7", "", "")), patch("codex_devtools.midi_loopback.subprocess.run", side_effect=fake_run):
            out = Path(folder) / "receipt.json"
            receipt = loopback(out, 0.1)
            self.assertEqual(receipt["status"], "failed")
            self.assertIn("bounded runtime", receipt["error"])
            self.assertEqual(json.loads(out.read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
