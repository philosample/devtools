"""Ensure RustDesk readiness cannot match its own setup script."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rustdesk-all-in-one-macos.sh"


def readiness_functions() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    constant = next(
        line
        for line in source.splitlines()
        if line.startswith("RUSTDESK_PROCESS_PATTERN=")
    )
    start = source.index("\nwrite_readiness_json(){") + 1
    end = source.index("\nfull_setup(){", start)
    return constant + "\n" + source[start:end]


FAKE_PGREP = r"""#!/usr/bin/env python3
import json
import os
import re
import sys
args = sys.argv[1:]
with open(os.environ['PGREP_CALLS'], 'a', encoding='utf-8') as log:
    log.write(json.dumps(args) + '\n')
if len(args) != 2 or args[0] != '-f':
    sys.exit(2)
pattern = args[1].replace('[[:space:]]', r'\s')
sys.exit(0 if re.search(pattern, os.environ.get('SIMULATED_PROCESS', '')) else 1)
"""


class RustDeskReadinessTests(unittest.TestCase):
    def run_report(self, process: str) -> tuple[str, dict, list[list[str]]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            pgrep = bin_dir / "pgrep"
            pgrep.write_text(FAKE_PGREP, encoding="utf-8")
            pgrep.chmod(0o755)
            calls = root / "pgrep-calls.jsonl"
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                    "PGREP_CALLS": str(calls),
                    "SIMULATED_PROCESS": process,
                }
            )
            shell = (
                "set -Eeuo pipefail\n"
                "HOST=''\nKEY=''\n"
                "log(){ :; }\n"
                "login_item_status(){ echo absent; }\n"
                "service_status(){ echo unverified; }\n"
                "ac_sleep_status(){ echo 0; }\n"
                + readiness_functions()
                + "\nclient_report\n"
            )
            completed = subprocess.run(
                ["bash", "-c", shell],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report_path = (
                home / "Library" / "Application Support" / "rustdesk-setup" / "readiness.json"
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            observed_calls = [
                json.loads(line)
                for line in calls.read_text(encoding="utf-8").splitlines()
            ]
            return completed.stdout, report, observed_calls

    def test_setup_script_is_not_a_rustdesk_process(self):
        output, report, calls = self.run_report(
            "bash ./scripts/rustdesk-all-in-one-macos.sh readiness"
        )
        self.assertIn("CHECK RustDesk process not detected", output)
        self.assertIs(report["process_detected"], False)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[0][0], "-f")

    def test_real_application_process_is_reported(self):
        output, report, calls = self.run_report(
            "/Applications/RustDesk.app/Contents/MacOS/RustDesk --service"
        )
        self.assertIn("PASS RustDesk process appears active", output)
        self.assertIs(report["process_detected"], True)
        self.assertEqual(len(calls), 2)

    def test_similarly_named_executable_is_not_reported(self):
        output, report, _ = self.run_report(
            "/Applications/RustDesk.app/Contents/MacOS/RustDesk-backup"
        )
        self.assertIn("CHECK RustDesk process not detected", output)
        self.assertIs(report["process_detected"], False)


if __name__ == "__main__":
    unittest.main()
