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


def mocked_toolchain(native, returncode=0, timeout=False, collision_path=None):
    def fake_run(*args, **kwargs):
        command = args[0]
        if command[0] == "xcrun" and command[-1] == "--version":
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="Swift version mock\n", stderr="")
        if command[0] == "xcrun" and command[-1] == "--show-sdk-path":
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="/mock/MacOSX.sdk\n", stderr="")
        if command[0] == "xcrun" and command[-1] == "--show-sdk-version":
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="26.2\n", stderr="")
        if command[0] == "xcrun" and "-o" in command:
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        if timeout:
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))
        if collision_path is not None:
            collision_path.write_bytes(b"original result")
        return subprocess.CompletedProcess(args=command, returncode=returncode, stdout=json.dumps(native), stderr="")
    return fake_run


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
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "receipt.json"
            with patch("codex_devtools.midi_loopback.platform.platform", return_value="test macOS"), patch("codex_devtools.midi_loopback.platform.mac_ver", return_value=("15.7", "", "")), patch("codex_devtools.midi_loopback.subprocess.run", side_effect=mocked_toolchain({}, timeout=True)):
                receipt = loopback(out, 0.1)
            self.assertEqual(receipt["status"], "failed")
            self.assertIn("bounded runtime", receipt["error"])
            self.assertEqual(json.loads(out.read_text())["status"], "failed")

    def test_nonzero_native_exit_rejects_valid_passing_json(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "receipt.json"
            with patch("codex_devtools.midi_loopback.platform.platform", return_value="test macOS"), patch("codex_devtools.midi_loopback.platform.mac_ver", return_value=("15.7", "", "")), patch("codex_devtools.midi_loopback.subprocess.run", side_effect=mocked_toolchain(passing_native(), returncode=7)):
                receipt = loopback(out, 0.1)
            self.assertEqual(receipt["native"]["status"], "passed")
            self.assertEqual(receipt["status"], "failed")
            self.assertIn("exited with status 7", receipt["error"])

    def test_publication_collision_after_precheck_preserves_existing_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "receipt.json"
            fake_run = mocked_toolchain(passing_native(), collision_path=out)
            with patch("codex_devtools.midi_loopback.platform.platform", return_value="test macOS"), patch("codex_devtools.midi_loopback.platform.mac_ver", return_value=("15.7", "", "")), patch("codex_devtools.midi_loopback.subprocess.run", side_effect=fake_run):
                with self.assertRaises(FileExistsError):
                    loopback(out, 0.1)
            self.assertEqual(out.read_bytes(), b"original result")

    def test_preexisting_dangling_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "missing.json"
            target.symlink_to(Path(folder) / "not-created.json")
            with self.assertRaisesRegex(ValueError, "already exists"):
                loopback(target)
            self.assertTrue(target.is_symlink())


if __name__ == "__main__":
    unittest.main()
