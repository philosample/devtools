"""Offline RustDesk backup recovery tests; never launch Docker or stop services."""

from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rustdesk-all-in-one-macos.sh"


def backup_function() -> str:
    source = SCRIPT.read_text()
    start = source.index("\nbackup(){") + 1
    end = source.index("\n\nupdate(){", start)
    return source[start:end]


HARNESS = r"""
set -Eeuo pipefail
BASE=$1
EVENTS=$2
log(){ :; }
warn(){ printf 'WARNING: %s\n' "$*" >&2; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need_mac(){ :; }
need_deploy(){ :; }
compose(){
  printf '%s\n' "$*" >> "$EVENTS"
  case "$1" in
    ps) [[ $RUNNING == 0 ]] || printf 'mock-running-container\n';;
    stop) [[ $FAIL_STOP == 0 ]];;
    start) [[ $FAIL_START == 0 ]];;
  esac
}
tar(){
  if [[ $FAIL_TAR == 1 ]]; then
    : > "$2"
    return 27
  fi
  command tar "$@"
}
chmod(){
  if [[ $FAIL_CHMOD == 1 && $1 == 600 ]]; then return 13; fi
  command chmod "$@"
}
"""


class RustDeskBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / "server data"
        self.base.mkdir()
        (self.base / "data").mkdir()
        (self.base / "data" / "fake-test-only").write_text("not a real identity key")
        (self.base / ".env").write_text("RUSTDESK_IMAGE=dummy\n")
        (self.base / "compose.yaml").write_text("services: {}\n")
        self.events = Path(self.tmp.name) / "events.txt"

    def run_backup(self, **flags: int) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "RUNNING": "1",
                "FAIL_TAR": "0",
                "FAIL_CHMOD": "0",
                "FAIL_STOP": "0",
                "FAIL_START": "0",
            }
        )
        env.update({k: str(v) for k, v in flags.items()})
        return subprocess.run(
            [
                "bash",
                "-c",
                HARNESS + backup_function() + "\nbackup\n",
                "bash",
                str(self.base),
                str(self.events),
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def events_seen(self) -> list[str]:
        return self.events.read_text().splitlines()

    def test_success_restores_containers_and_creates_private_archive(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.events_seen(), ["ps --status running -q", "stop", "start"]
        )
        archives = list((self.base / "backups").glob("*.tar.gz"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)
        with tarfile.open(archives[0]) as tf:
            self.assertEqual(
                sorted(tf.getnames()),
                [".env", "compose.yaml", "data", "data/fake-test-only"],
            )

    def test_archive_failure_restarts_and_removes_partial_archive(self):
        result = self.run_backup(FAIL_TAR=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archive creation failed", result.stderr)
        self.assertEqual(
            self.events_seen(), ["ps --status running -q", "stop", "start"]
        )
        self.assertEqual(list((self.base / "backups").glob("*.tar.gz")), [])

    def test_permissions_failure_restarts_and_removes_unsafe_archive(self):
        result = self.run_backup(FAIL_CHMOD=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("permissions could not be secured", result.stderr)
        self.assertEqual(
            self.events_seen(), ["ps --status running -q", "stop", "start"]
        )
        self.assertEqual(list((self.base / "backups").glob("*.tar.gz")), [])

    def test_stop_failure_attempts_recovery_before_aborting(self):
        result = self.run_backup(FAIL_STOP=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.events_seen(), ["ps --status running -q", "stop", "start"]
        )
        self.assertEqual(list((self.base / "backups").glob("*.tar.gz")), [])

    def test_restart_failure_is_visible_and_never_claims_success(self):
        result = self.run_backup(FAIL_START=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not be restarted", result.stderr)
        self.assertEqual(
            self.events_seen(), ["ps --status running -q", "stop", "start"]
        )

    def test_inactive_server_is_not_started_by_backup(self):
        result = self.run_backup(RUNNING=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events_seen(), ["ps --status running -q"])
        self.assertEqual(len(list((self.base / "backups").glob("*.tar.gz"))), 1)


if __name__ == "__main__":
    unittest.main()
