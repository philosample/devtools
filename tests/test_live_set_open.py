"""Filesystem and native-receipt/source contracts only; no Live or UI actions."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codex_devtools.cli import main
from codex_devtools.live_set_open import (
    ACTION_NAMES,
    FORMAT,
    _backup,
    _read_set,
    run_set_open,
)
from codex_devtools.maxfiles import sha256

PID = 84769
BUNDLE = "com.ableton.live"


class SameProcessSetOpenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "Owned.als"
        self.destination = self.root / "Existing.als"
        self.source.write_bytes(b"owned-source-before-save")
        self.destination.write_bytes(b"reviewed-destination")
        self.source_sha = sha256(self.source.read_bytes())
        self.destination_sha = sha256(self.destination.read_bytes())
        self.backup_root = self.root / "backups"
        self.backup_root.mkdir(mode=0o700)
        self.app = self.root / "Live.app"
        self.app.mkdir()

    def options(self, **changes):
        options = {
            "expected_bundle_id": BUNDLE,
            "source_set": self.source,
            "destination_set": self.destination,
            "source_sha256": self.source_sha,
            "destination_sha256": self.destination_sha,
            "app_bundle": self.app,
        }
        options.update(changes)
        return options

    def report(self, *, execute=False, save=False, command=None, write_source=True):
        actions = dict.fromkeys(ACTION_NAMES, "not_attempted")
        if execute:
            actions.update(focus="verified", open="exact_set_window_observed")
            if save:
                actions.update(menu_navigation="verified", save="source_observed")
                if write_source:
                    self.source.write_bytes(b"explicitly-saved-owned-state")
        source_sha = sha256(self.source.read_bytes())
        report = {
            "format": FORMAT,
            "status": "opened" if execute else "ready",
            "read_only": not execute,
            "target": {"pid": PID, "bundle_identifier": BUNDLE},
            "source_set_url": self.source.as_uri(),
            "destination_set_url": self.destination.as_uri(),
            "source_file": _read_set(self.source, source_sha)[1],
            "destination_file": _read_set(self.destination, self.destination_sha)[1],
            "save_owned_source": save,
            "actions": actions,
        }
        if execute:
            report["same_process"] = True
        if execute and save:
            receipt_path = command[command.index("--backup-receipt") + 1]
            backup = json.loads(Path(receipt_path).read_text())
            report.update(
                backup_receipt=receipt_path, backup_file=backup["backup"]["path"]
            )
        return report

    def call(self, report=None, native=None, **changes):
        def completed(command, **kwargs):
            value = native(command) if native else report
            return subprocess.CompletedProcess(
                command, 1 if value["status"] == "stopped" else 0, json.dumps(value), ""
            )

        with (
            patch(
                "codex_devtools.live_set_open.platform.system", return_value="Darwin"
            ),
            patch(
                "codex_devtools.live_set_open.subprocess.run", side_effect=completed
            ) as runner,
        ):
            result = run_set_open(PID, **self.options(**changes))
        self.runner = runner
        return result

    def test_preflight_does_not_create_backups_or_attempt_actions(self):
        before = self.source.read_bytes()
        report = self.call(
            self.report(save=True), save_owned_source=True, backup_root=self.backup_root
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(self.backup_root.iterdir()), [])
        command = self.runner.call_args.args[0]
        self.assertIn("--open-existing", command)
        self.assertNotIn("--execute", command)
        self.assertNotIn("--backup-receipt", command)
        self.assertEqual(self.runner.call_args.kwargs["timeout"], 240)

    def test_open_without_save_requires_same_pid_and_never_authorizes_save(self):
        result = self.call(self.report(execute=True), execute=True)
        self.assertEqual(result["status"], "opened")
        self.assertIsNone(result["source_backup"])
        self.assertNotIn("--save-owned-source", self.runner.call_args.args[0])
        self.assertEqual(result["actions"]["save"], "not_attempted")
        self.assertEqual(result["actions"]["quit"], "not_attempted")

    def test_explicit_save_backup_and_receipt_exist_and_hash_match_before_native_call(
        self,
    ):
        before = self.source.read_bytes()

        def native(command):
            receipt_path = Path(command[command.index("--backup-receipt") + 1])
            receipt = json.loads(receipt_path.read_text())
            backup_file = Path(receipt["backup"]["path"])
            self.assertEqual(self.source.read_bytes(), before)
            self.assertEqual(backup_file.read_bytes(), before)
            self.assertEqual(receipt["source"]["sha256"], self.source_sha)
            self.assertEqual(
                receipt["backup"]["sha256"], sha256(backup_file.read_bytes())
            )
            self.assertEqual(receipt_path.stat().st_mode & 0o077, 0)
            self.assertEqual(backup_file.stat().st_mode & 0o077, 0)
            return self.report(execute=True, save=True, command=command)

        result = self.call(
            native=native,
            execute=True,
            save_owned_source=True,
            backup_root=self.backup_root,
        )
        self.assertEqual(result["status"], "opened")
        self.assertEqual(
            result["source_file"]["sha256"], sha256(self.source.read_bytes())
        )
        self.assertNotEqual(result["source_file"]["sha256"], self.source_sha)
        self.assertEqual(
            Path(result["source_backup"]["backup"]["path"]).read_bytes(), before
        )
        self.assertEqual(self.runner.call_count, 1)

    def test_timeout_preserves_backup_and_unknown_outcomes_without_retry(self):
        with (
            patch(
                "codex_devtools.live_set_open.platform.system", return_value="Darwin"
            ),
            patch(
                "codex_devtools.live_set_open.subprocess.run",
                side_effect=subprocess.TimeoutExpired("swift", 240),
            ) as runner,
        ):
            result = run_set_open(
                PID,
                **self.options(
                    execute=True, save_owned_source=True, backup_root=self.backup_root
                ),
            )
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["phase"], "outer-timeout")
        self.assertEqual(result["actions"]["save"], "outcome_unknown")
        self.assertEqual(result["actions"]["open"], "outcome_unknown")
        self.assertEqual(result["actions"]["quit"], "not_attempted")
        self.assertTrue(result["manual_inspection_required"])
        self.assertTrue(Path(result["source_backup"]["receipt_path"]).is_file())
        self.assertEqual(runner.call_count, 1)

    def test_bad_pins_and_missing_save_authority_stop_before_native_or_backup(self):
        changes = [
            {"source_sha256": "0" * 64},
            {"destination_sha256": "0" * 64},
            {"source_sha256": "not-a-hash"},
            {"save_owned_source": True},
            {"backup_root": self.backup_root},
        ]
        for change in changes:
            with (
                self.subTest(change=change),
                patch(
                    "codex_devtools.live_set_open.platform.system",
                    return_value="Darwin",
                ),
                patch("codex_devtools.live_set_open.subprocess.run") as native,
            ):
                with self.assertRaises(ValueError):
                    run_set_open(PID, **self.options(execute=True, **change))
                native.assert_not_called()
        self.assertEqual(list(self.backup_root.iterdir()), [])

    def test_symlinks_git_paths_and_hardlink_aliases_are_refused(self):
        for kind in ("symlink", "git", "hardlink"):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == "symlink":
                    self.destination.unlink()
                    self.destination.symlink_to(self.source)
                if kind == "git":
                    (self.root / ".git").mkdir()
                if kind == "hardlink":
                    self.destination.unlink()
                    os.link(self.source, self.destination)
                    self.destination_sha = self.source_sha
                with (
                    patch(
                        "codex_devtools.live_set_open.platform.system",
                        return_value="Darwin",
                    ),
                    patch("codex_devtools.live_set_open.subprocess.run") as native,
                ):
                    with self.assertRaises(ValueError):
                        run_set_open(PID, **self.options())
                    native.assert_not_called()

    def test_backup_root_must_be_private_canonical_and_outside_git(self):
        self.backup_root.chmod(0o755)
        with (
            patch(
                "codex_devtools.live_set_open.platform.system", return_value="Darwin"
            ),
            patch("codex_devtools.live_set_open.subprocess.run") as native,
        ):
            with self.assertRaisesRegex(ValueError, "private and owned"):
                run_set_open(
                    PID,
                    **self.options(
                        execute=True,
                        save_owned_source=True,
                        backup_root=self.backup_root,
                    ),
                )
            native.assert_not_called()
        self.backup_root.chmod(0o700)
        linked = self.root / "linked-backups"
        linked.symlink_to(self.backup_root)
        with (
            patch(
                "codex_devtools.live_set_open.platform.system", return_value="Darwin"
            ),
            self.assertRaises(ValueError),
        ):
            run_set_open(
                PID, **self.options(save_owned_source=True, backup_root=linked)
            )

    def test_noop_save_or_unexpected_source_write_never_passes(self):
        result = self.call(
            native=lambda command: self.report(
                execute=True, save=True, command=command, write_source=False
            ),
            execute=True,
            save_owned_source=True,
            backup_root=self.backup_root,
        )
        self.assertEqual(result["status"], "stopped")
        self.assertIn("transition", result["error"])
        self.assertTrue(result["source_backup"])
        self.setUp()

        def unexpected(command):
            self.source.write_bytes(b"unapproved-source-write")
            return self.report(execute=True)

        result = self.call(native=unexpected, execute=True)
        self.assertEqual(result["status"], "stopped")
        self.assertIn("without explicit Save", result["error"])

    def test_wrong_pid_url_hash_or_action_receipt_remains_unknown(self):
        for kind in ("pid", "url", "pin", "quit", "save", "same-process"):
            with self.subTest(kind=kind):
                report = self.report(execute=True)
                if kind == "pid":
                    report["target"]["pid"] += 1
                if kind == "url":
                    report["destination_set_url"] = self.source.as_uri()
                if kind == "pin":
                    report["destination_file"]["sha256"] = "0" * 64
                if kind == "quit":
                    report["actions"]["quit"] = "outcome_unknown"
                if kind == "save":
                    report["actions"]["save"] = "source_observed"
                if kind == "same-process":
                    report["same_process"] = False
                result = self.call(report, execute=True)
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["actions"]["open"], "outcome_unknown")

    def test_native_prompt_stop_and_invalid_json_never_trigger_followup(self):
        report = self.report(execute=True)
        report.update(
            status="stopped",
            phase="verify-opened-existing-set",
            error="unexpected prompt",
            manual_inspection_required=True,
        )
        report["actions"]["open"] = "outcome_unknown"
        result = self.call(report, execute=True)
        self.assertEqual(result["error"], "unexpected prompt")
        self.assertEqual(self.runner.call_count, 1)
        with (
            patch(
                "codex_devtools.live_set_open.platform.system", return_value="Darwin"
            ),
            patch(
                "codex_devtools.live_set_open.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "not-json", ""),
            ) as native,
        ):
            result = run_set_open(PID, **self.options(execute=True))
        self.assertEqual(result["phase"], "native-receipt")
        self.assertEqual(native.call_count, 1)

    def test_cli_is_explicit_and_uses_the_shared_swift_mode(self):
        args = [
            "ax",
            "live-set-open",
            "--pid",
            str(PID),
            "--expected-bundle-id",
            BUNDLE,
            "--source-set",
            str(self.source),
            "--destination-set",
            str(self.destination),
            "--source-sha256",
            self.source_sha,
            "--destination-sha256",
            self.destination_sha,
            "--app",
            str(self.app),
        ]
        with (
            patch(
                "codex_devtools.live_set_open.run_set_open", return_value=self.report()
            ) as call,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(args), 0)
        self.assertFalse(call.call_args.kwargs["execute"])
        self.assertFalse(call.call_args.kwargs["save_owned_source"])

    def test_swift_mode_reuses_guards_and_has_only_one_save_and_open(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools/live_set_lifecycle.swift"
        ).read_text()
        body = source[
            source.index("func runOpenExisting(") : source.index("func openActions(")
        ]
        self.assertIn("if !options.execute", body)
        backup = body.index("backupPath = try checkedSourceBackup")
        saved = body.index("saveAttempted = true")
        waited = body.index("try waitForOwnedSourceSaved")
        opened = body.index("openAttempted = true")
        self.assertLess(backup, saved)
        self.assertLess(saved, waited)
        self.assertLess(waited, opened)
        self.assertEqual(body.count("try press(item)"), 1)
        self.assertEqual(body.count("NSWorkspace.shared.open"), 1)
        self.assertIn("configuration.createsNewApplicationInstance = false", body)
        self.assertIn("opened?.processIdentifier == options.pid", body)
        self.assertIn("sameSetURL(raw, expected: destination)", body)
        self.assertIn("samePin(beforeOpenSource, expectedSource)", body)
        self.assertIn("samePin(openedDestination, destinationPin)", body)
        for forbidden in (
            "postChord",
            "postKeyStroke",
            "process.terminate",
            "Quit Live",
            "Don't Save",
            "title(window)",
        ):
            self.assertNotIn(forbidden, body)
        wait = source[
            source.index("func waitForOwnedSourceSaved(") : source.index(
                "func runOpenExisting("
            )
        ]
        for required in (
            "systemUptime < deadline",
            "requireSameProcess",
            "ensureNoDialogs",
            "exactDocumentWindow",
            "!samePin(pin, before)",
            "stable >= 2",
            "SetObservationChanged",
        ):
            self.assertIn(required, wait)
        self.assertNotIn("press(", wait)
        self.assertIn("unchanged/no-op Save is not inferred complete", wait)

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("swiftc"),
        "Swift macOS filesystem ABI",
    )
    def test_real_swift_file_pin_and_backup_receipt_abi(self):
        # Compile only production filesystem functions, without any AX/AppKit or
        # lifecycle entrypoint. Executing this program can only inspect temp files.
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools/live_set_lifecycle.swift"
        ).read_text()

        def function(name):
            start = source.index(f"func {name}(")
            return source[start : source.index("\nfunc ", start + 1)]

        options = source[
            source.index("struct Options {") : source.index("struct LifecycleFailure:")
        ]
        program = "\n".join(
            [
                "import Foundation\nimport Darwin\nimport CryptoKit",
                options,
                "struct LifecycleFailure: Error { let phase: String; let message: String }",
                'var phase = "filesystem-test"',
                "struct SetObservationChanged: Error {}",
                "let maxPinnedSetBytes: Int64 = 128 * 1024 * 1024",
                *[
                    function(name)
                    for name in (
                        "require",
                        "lexicalPath",
                        "canonicalURL",
                        "requireAbsentLeaf",
                        "ensureNoSymlinkChain",
                        "fileIdentity",
                        "setPin",
                        "samePin",
                        "checkedSourceBackup",
                    )
                ],
                """
do {
    let args = CommandLine.arguments
    let source = URL(fileURLWithPath: args[1]), destination = URL(fileURLWithPath: args[2])
    var options = Options()
    options.pid = pid_t(args[4])!; options.bundleID = args[5]
    options.sourceSHA256 = args[6]; options.destinationSHA256 = args[7]
    let backup = try checkedSourceBackup(args[3], source: source, destination: destination, options: options)
    let result: [String: Any] = ["pin": try setPin(source), "backup": backup]
    print(String(data: try JSONSerialization.data(withJSONObject: result), encoding: .utf8)!)
} catch { fputs(String(describing: error), stderr); exit(1) }
""",
            ]
        )
        swift = self.root / "filesystem.swift"
        binary = self.root / "filesystem-test"
        swift.write_text(program)
        compiled = subprocess.run(
            ["swiftc", str(swift), "-o", str(binary)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        data, pin = _read_set(self.source, self.source_sha)
        backup = _backup(
            self.backup_root,
            data,
            pin,
            self.destination,
            self.destination_sha,
            PID,
            BUNDLE,
        )
        command = [
            str(binary),
            str(self.source),
            str(self.destination),
            backup["receipt_path"],
            str(PID),
            BUNDLE,
            self.source_sha,
            self.destination_sha,
        ]
        checked = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(
            json.loads(checked.stdout), {"pin": pin, "backup": backup["backup"]["path"]}
        )
        Path(backup["backup"]["path"]).write_bytes(b"tampered-backup")
        self.assertEqual(
            subprocess.run(
                command, check=False, capture_output=True, timeout=10
            ).returncode,
            1,
        )
