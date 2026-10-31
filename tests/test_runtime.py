import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools.cli import main
from codex_devtools import runtime


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic node executable")
    path.chmod(0o755)
    return path


def version_result(path: Path, version: str = "v22.1.0"):
    return subprocess.CompletedProcess([str(path), "--version"], 0, version + "\n", "")


class NodeRuntimeTests(unittest.TestCase):
    def test_path_node_is_preferred_to_bundled_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path_node = executable(root / "bin" / "node")
            app = root / "Ableton Live.app"
            bundled_node = executable(app / runtime.LIVE_NODE_SUFFIX)
            with patch("codex_devtools.runtime.shutil.which", return_value=str(path_node)), \
                    patch("codex_devtools.runtime.subprocess.run", return_value=version_result(path_node)) as run:
                result = runtime.discover_node(app)
            self.assertTrue(result["available"])
            self.assertEqual(result["path"], str(path_node.resolve()))
            self.assertEqual(result["version"], "v22.1.0")
            self.assertEqual(result["provenance"], "PATH")
            run.assert_called_once_with([str(path_node.resolve()), "--version"], capture_output=True,
                                        text=True, timeout=5, check=False)
            self.assertNotEqual(result["path"], str(bundled_node.resolve()))

    def test_explicit_live_app_bundled_node_is_used_when_path_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Live Test.app"
            node = executable(app / runtime.LIVE_NODE_SUFFIX)
            with patch("codex_devtools.runtime.shutil.which", return_value=None), \
                    patch("codex_devtools.runtime.subprocess.run", return_value=version_result(node)) as run:
                result = runtime.discover_node(app)
            self.assertTrue(result["available"])
            self.assertEqual(result["path"], str(node.resolve()))
            self.assertEqual(result["provenance"], "explicit app bundled Max Node")
            self.assertEqual(result["app"], str(app.resolve()))
            run.assert_called_once()

    def test_explicit_max_app_bundle_path_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Max.app"
            node = executable(app / runtime.MAX_NODE_SUFFIX)
            with patch("codex_devtools.runtime.shutil.which", return_value=None), \
                    patch("codex_devtools.runtime.subprocess.run", return_value=version_result(node)):
                result = runtime.discover_node(app)
            self.assertTrue(result["available"])
            self.assertEqual(result["path"], str(node.resolve()))

    def test_path_version_failure_falls_back_to_bundled_node(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path_node = executable(root / "bin" / "node")
            app = root / "Ableton Live.app"
            bundled_node = executable(app / runtime.LIVE_NODE_SUFFIX)
            failed = subprocess.CompletedProcess([str(path_node), "--version"], 1, "", "broken")
            with patch("codex_devtools.runtime.shutil.which", return_value=str(path_node)), \
                    patch("codex_devtools.runtime.subprocess.run",
                          side_effect=[failed, version_result(bundled_node)]):
                result = runtime.discover_node(app)
            self.assertTrue(result["available"])
            self.assertEqual(result["path"], str(bundled_node.resolve()))
            self.assertEqual(result["attempts"][0]["status"], "unavailable")
            self.assertIn("status 1", result["attempts"][0]["error"])

    def test_unavailable_runtime_and_timeout_are_explicit_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Missing.app"
            with patch("codex_devtools.runtime.shutil.which", return_value=None):
                missing = runtime.discover_node(app)
            self.assertFalse(missing["available"])
            self.assertIsNone(missing["path"])
            self.assertIn("No usable Node.js runtime found", missing["error"])
            self.assertEqual(missing["attempts"][-1]["status"], "unavailable")

            path_node = executable(Path(directory) / "bin" / "node")
            timeout_error = subprocess.TimeoutExpired([str(path_node), "--version"], 5)
            with patch("codex_devtools.runtime.shutil.which", return_value=str(path_node)), \
                    patch("codex_devtools.runtime.subprocess.run", side_effect=timeout_error):
                timed_out = runtime.discover_node(app)
            self.assertFalse(timed_out["available"])
            self.assertIn("timed out after 5 seconds", timed_out["attempts"][0]["error"])

    def test_cli_json_reports_success_and_failure_exit_codes(self):
        success = {"runtime": "node", "available": True, "path": "/node", "version": "v22.1.0", "provenance": "PATH"}
        output = io.StringIO()
        with patch("codex_devtools.runtime.discover_node", return_value=success), contextlib.redirect_stdout(output):
            self.assertEqual(main(["runtime", "node", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue()), success)

        failure = {"runtime": "node", "available": False, "path": None, "version": None,
                   "provenance": None, "attempts": [], "error": "No usable Node.js runtime found"}
        output = io.StringIO()
        with patch("codex_devtools.runtime.discover_node", return_value=failure), contextlib.redirect_stdout(output):
            self.assertEqual(main(["runtime", "node", "--json"]), 2)
        self.assertEqual(json.loads(output.getvalue()), failure)


if __name__ == "__main__":
    unittest.main()
