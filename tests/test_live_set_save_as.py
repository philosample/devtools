"""Expose the CUA fake-app guard checks through the existing unittest entrypoint."""

import re
import subprocess
import unittest
from pathlib import Path

from codex_devtools.runtime import discover_node


class LiveSetSaveAsGuardTests(unittest.TestCase):
    def test_actual_javascript_guards_with_fake_app_only(self):
        runtime = discover_node()
        if not runtime["available"]:
            raise RuntimeError(
                "Node required for guard coverage; CUA/native UI is never used"
            )
        script = Path(__file__).with_name("live_set_save_as.test.mjs")
        result = subprocess.run(
            [runtime["path"], str(script)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        counts = re.findall(
            r"PASS (\d+) CUA Save As fake-app/guard checks; no native UI used",
            result.stdout,
        )
        self.assertEqual(len(counts), 1, result.stdout)
        self.assertGreater(
            int(counts[0]), 0, "zero guard checks is not passing coverage"
        )
