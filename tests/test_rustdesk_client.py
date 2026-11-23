"""Offline RustDesk macOS client setup and service-control regression tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "scripts/rustdesk-client-macos.sh"
ALL_IN_ONE = ROOT / "scripts/rustdesk-all-in-one-macos.sh"
KEY = "ZwaS9Qcax3FLDOptOLseeXUpvMI3o572yGXg3SThzMo="
HOST = "192.168.1.167"


class RustDeskMacClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.app = self.root / "Applications" / "RustDesk.app"
        binary = self.app / "Contents" / "MacOS" / "RustDesk"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 37\n")
        binary.chmod(0o755)
        self.plist = self.root / "service.plist"
        self.daemon = self.root / "daemon.plist"
        self.config = (
            self.home / "Library/Preferences/com.carriez.RustDesk/RustDesk2.toml"
        )
        self.config.parent.mkdir(parents=True)
        self.events = self.root / "events"
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        stubs = {
            "uname": "#!/bin/sh\nprintf 'Darwin\\n'\n",
            "pgrep": "#!/bin/sh\n[ \"$MOCK_RUNNING\" = 1 ]\n",
            "brew": (
                "#!/bin/sh\n"
                "printf 'brew %s\\n' \"$*\" >> \"$EVENTS\"\n"
                "exit 0\n"
            ),
            "open": "#!/bin/sh\nprintf 'open\\n' >> \"$EVENTS\"\n",
            "osascript": "#!/bin/sh\ncat >/dev/null\n",
            "launchctl": "#!/bin/sh\n[ \"$MOCK_SERVICE\" = 1 ]\n",
        }
        for name, script in stubs.items():
            command = self.bin_dir / name
            command.write_text(script)
            command.chmod(0o755)
        original = CLIENT.read_text()
        # The production script must still reject root. The test substitutes a
        # non-root EUID check because isolated CI may itself run as root.
        guard = "[[ $EUID -ne 0 ]] || die 'Run as your normal user, not with sudo'"
        self.assertIn(guard, original)
        patched = original.replace(guard, "[[ 1 -ne 0 ]] || die 'nonroot'")
        patched = patched.replace("/Applications/RustDesk.app", str(self.app))
        patched = patched.replace(
            "/Library/LaunchAgents/com.carriez.RustDesk_server.plist",
            str(self.plist),
        )
        patched = patched.replace(
            "/Library/LaunchDaemons/com.carriez.RustDesk_service.plist",
            str(self.daemon),
        )
        self.script = self.root / "client.sh"
        self.script.write_text(patched)
        self.script.chmod(0o755)

    def save_settings(self, *, host: str = HOST, key: str = KEY):
        self.config.write_text(
            "[options]\n"
            f"custom-rendezvous-server = '{host}'\n"
            f"key = '{key}'\n"
        )

    def run_client(self, command: str = "setup", *options: str, **flags: str):
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "PATH": f"{self.bin_dir}{os.pathsep}{env['PATH']}",
                "EVENTS": str(self.events),
                "MOCK_RUNNING": flags.get("running", "0"),
                "MOCK_SERVICE": flags.get("service", "0"),
            }
        )
        return subprocess.run(
            [
                "bash",
                str(self.script),
                command,
                "--host",
                HOST,
                "--key",
                KEY,
                *options,
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def test_outbound_only_no_service_preserves_saved_settings(self):
        self.save_settings()
        original = self.config.read_bytes()
        result = self.run_client("setup", "--skip-update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VERIFIED: saved ID server", result.stdout)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertIn("open", self.events.read_text())

    def test_outbound_only_mismatch_instructs_manual_configuration(self):
        self.save_settings(host="some-other-server")
        original = self.config.read_bytes()
        result = self.run_client("setup", "--skip-update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PENDING", result.stdout)
        self.assertIn(HOST, result.stdout)
        self.assertIn("Network", result.stdout)
        self.assertEqual(self.config.read_bytes(), original)

    def test_verify_is_read_only_and_requires_exact_server_and_key(self):
        self.save_settings()
        good = self.run_client("verify")
        self.assertEqual(good.returncode, 0, good.stderr)
        self.save_settings(key="different-public-key")
        saved = self.config.read_bytes()
        wrong = self.run_client("verify")
        self.assertEqual(wrong.returncode, 1)
        self.assertIn("PENDING", wrong.stdout)
        self.assertEqual(self.config.read_bytes(), saved)

    def test_service_requested_but_missing_fails_with_gui_instructions(self):
        self.save_settings()
        result = self.run_client("setup", "--skip-update", "--install-service")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Service Control > Install", result.stdout)
        self.assertIn("Outbound-only connections", result.stdout)

    def test_installed_service_is_detected_without_cli_install(self):
        self.save_settings()
        self.plist.touch()
        self.daemon.touch()
        result = self.run_client(
            "setup", "--skip-update", "--install-service", service="1"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("background service detected", result.stdout)

    def test_running_service_preserves_installed_app_during_update(self):
        self.save_settings()
        result = self.run_client("setup", running="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("preserving its installed app version", result.stdout)
        self.assertIn("open", self.events.read_text())
        self.assertNotIn("brew", self.events.read_text())

    def test_no_unsupported_cli_or_silent_config_rewrite(self):
        content = CLIENT.read_text()
        self.assertNotIn('"$BIN" --install-service', content)
        self.assertNotIn('"$BIN" --option', content)
        self.assertNotIn('sudo -v', content)
        self.assertNotIn('sudo chown', content)
        self.assertNotIn('Config::set_options', content)

    def test_all_in_one_rejects_missing_service_instead_of_claiming_success(self):
        source = ALL_IN_ONE.read_text()
        start = source.index("\nconfigure_client_service(){") + 1
        end = source.index("\nconfigure_client_autostart(){", start)
        function = source[start:end].replace(
            "/Library/LaunchAgents/com.carriez.RustDesk_server.plist",
            str(self.plist),
        ).replace(
            "/Library/LaunchDaemons/com.carriez.RustDesk_service.plist",
            str(self.daemon),
        )
        command = (
            "set -Eeuo pipefail\n"
            "INSTALL_SERVICE=1\n"
            "log(){ printf '%s\\n' \"$*\"; }\n"
            "die(){ printf 'ERROR: %s\\n' \"$*\" >&2; exit 2; }\n"
            + function
            + "\nconfigure_client_service\n"
        )
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{env['PATH']}",
                "MOCK_SERVICE": "0",
            }
        )
        missing = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertIn("Service Control", missing.stderr)
        self.plist.touch()
        self.daemon.touch()
        env["MOCK_SERVICE"] = "1"
        present = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(present.returncode, 0, present.stderr)


if __name__ == "__main__":
    unittest.main()
