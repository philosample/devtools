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
            report["destination_file_after_open"] = dict(report["destination_file"])
            report["destination_metadata_delta"] = []
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

    def test_cli_routes_reconciliation_as_separate_explicit_mode(self):
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
            "--save-owned-source",
            "--backup-root",
            str(self.backup_root),
            "--reconcile-pending-open",
            "--expected-generation",
            "123456",
        ]
        with (
            patch(
                "codex_devtools.live_set_open.run_set_open",
                return_value=self.report(),
            ) as call,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(args), 0)
        self.assertTrue(call.call_args.kwargs["reconcile_pending_open"])
        self.assertEqual(call.call_args.kwargs["expected_generation"], 123456)
        self.assertFalse(call.call_args.kwargs["execute"])

    def test_reconcile_preflight_requires_generation_and_never_creates_backup(self):
        before = self.source.read_bytes()
        report = self.report()
        report.update(
            save_owned_source=True,
            reconcile_pending_open=True,
            expected_generation=123456,
            dialog_text=f'Save changes to "{self.source.stem}" before closing?',
            transport={
                "play": 0,
                "link": 0,
                "arrangement_record": 0,
                "session_record": 0,
            },
            pending_destination_binding_pre_action="unverified",
            open_initiated_by_helper=False,
        )
        result = self.call(
            report,
            save_owned_source=True,
            backup_root=self.backup_root,
            reconcile_pending_open=True,
            expected_generation=123456,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(self.backup_root.iterdir()), [])
        command = self.runner.call_args.args[0]
        self.assertIn("--reconcile-pending-open", command)
        self.assertEqual(command[command.index("--expected-generation") + 1], "123456")
        self.assertNotIn("--execute", command)
        self.assertNotIn("--backup-receipt", command)

    def test_reconcile_execute_validates_original_pending_destination_without_second_open(
        self,
    ):
        def native(command):
            receipt_path = Path(command[command.index("--backup-receipt") + 1])
            backup = json.loads(receipt_path.read_text())
            self.source.write_bytes(b"saved-after-exact-dialog")
            report = self.report(execute=True, save=False, command=command)
            report["status"] = "reconciled"
            report["save_owned_source"] = True
            report["actions"]["save"] = "source_observed"
            report["actions"]["menu_navigation"] = "not_attempted"
            report.update(
                reconcile_pending_open=True,
                reconciled_pending_open=True,
                expected_generation=123456,
                dialog_text=f'Save changes to "{self.source.stem}" before closing?',
                pending_destination_binding_pre_action="unverified",
                open_initiated_by_helper=False,
                transport_before_save={
                    "play": 0,
                    "link": 0,
                    "arrangement_record": 0,
                    "session_record": 0,
                },
                transport_after_open={
                    "play": 0,
                    "link": 0,
                    "arrangement_record": 0,
                    "session_record": 0,
                },
            )
            report["source_file"] = _read_set(
                self.source, sha256(self.source.read_bytes())
            )[1]
            report["source_file_before_save"] = backup["source"]
            report["backup_receipt"] = str(receipt_path)
            report["backup_file"] = backup["backup"]["path"]
            return report

        result = self.call(
            native=native,
            execute=True,
            save_owned_source=True,
            backup_root=self.backup_root,
            reconcile_pending_open=True,
            expected_generation=123456,
        )
        self.assertEqual(result["status"], "reconciled", result)
        self.assertIs(result["open_initiated_by_helper"], False)
        command = self.runner.call_args.args[0]
        self.assertIn("--reconcile-pending-open", command)
        self.assertEqual(self.runner.call_count, 1)

    def test_reconcile_requires_exact_permission_generation_and_backup_root(self):
        for changes in (
            {"reconcile_pending_open": True},
            {
                "reconcile_pending_open": True,
                "save_owned_source": True,
                "expected_generation": 123,
            },
            {
                "reconcile_pending_open": True,
                "save_owned_source": True,
                "backup_root": self.backup_root,
            },
        ):
            with (
                self.subTest(changes=changes),
                patch(
                    "codex_devtools.live_set_open.platform.system",
                    return_value="Darwin",
                ),
                patch("codex_devtools.live_set_open.subprocess.run") as native,
                self.assertRaises(ValueError),
            ):
                run_set_open(PID, **self.options(**changes))
                native.assert_not_called()

    def test_swift_reconcile_is_one_save_no_second_open_and_waits_for_same_dialog_close(
        self,
    ):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools/live_set_lifecycle.swift"
        ).read_text()
        body = source[
            source.index("func runReconcilePendingOpen(") : source.index(
                "func checkedSourceBackup("
            )
        ]
        press = body.index("try press(saveButton)")
        wait = body.index('phase = "observe-original-pending-open"')
        self.assertLess(press, wait)
        self.assertEqual(body.count("try press(saveButton)"), 1)
        self.assertNotIn("NSWorkspace.shared.open", body)
        self.assertIn("CFEqual(blockers[0], dialog)", body)
        self.assertIn("continue", body)
        self.assertIn("stable >= 3", body)
        self.assertIn("pending_destination_binding_pre_action", body)
        self.assertIn("open_initiated_by_helper", body)
        self.assertIn("try saveCopyTransport(sourceWindow)", body)
        self.assertIn("try exactDocumentWindow(app, url: source)", body)

    @unittest.skipUnless(shutil.which("swiftc"), "Swift compiler")
    def test_swift_reconciliation_wait_policy_accepts_only_same_dialog(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools/live_set_lifecycle.swift"
        ).read_text()
        start = source.index("func canWaitForReconciliationDialog(")
        end = source.index("\nfunc isReconciliationBlockingWindow(", start)
        function = source[start:end]
        with tempfile.TemporaryDirectory() as temp:
            program = Path(temp) / "dialog-policy.swift"
            executable = Path(temp) / "dialog-policy"
            program.write_text(
                function
                + """
                precondition(canWaitForReconciliationDialog(blockerCount: 1, exactSameDialog: true))
                precondition(!canWaitForReconciliationDialog(blockerCount: 1, exactSameDialog: false))
                precondition(!canWaitForReconciliationDialog(blockerCount: 2, exactSameDialog: true))
                print("same-dialog wait policy passed")
                """
            )
            compiled = subprocess.run(
                ["swiftc", str(program), "-o", str(executable)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable)], check=False, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("same-dialog wait policy passed", result.stdout)

    @unittest.skipUnless(shutil.which("swiftc"), "Swift compiler")
    def test_swift_save_changes_signature_requires_exact_text_and_three_controls(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools/live_set_lifecycle.swift"
        ).read_text()
        start = source.index("func saveChangesDialogSignature(")
        end = source.index("\nfunc canWaitForReconciliationDialog(", start)
        function = source[start:end]
        with tempfile.TemporaryDirectory() as temp:
            program = Path(temp) / "dialog-signature.swift"
            executable = Path(temp) / "dialog-signature"
            program.write_text(
                "import Foundation\n"
                + function
                + """
                let prompt = "Save changes to \\"Owned\\" before closing?"
                var rows: [[String: Any]] = [
                    ["role": "AXWindow", "subrole": "AXDialog", "title": ""],
                    ["role": "AXGroup"],
                    ["role": "AXStaticText", "identifier": "Other", "value": ""],
                    ["role": "AXStaticText", "identifier": "DialogText", "value": prompt],
                    ["role": "AXButton", "identifier": "Save", "description": "Save", "enabled": true, "press": true],
                    ["role": "AXButton", "identifier": "Cancel", "description": "Cancel", "enabled": true, "press": true],
                    ["role": "AXButton", "identifier": "Don’tSave", "description": "Don’t Save", "enabled": true, "press": true]
                ]
                precondition(saveChangesDialogSignature(rows, expectedText: prompt))
                precondition(!saveChangesDialogSignature(rows, expectedText: "Save changes to \\"Other\\" before closing?"))
                rows[4]["enabled"] = false
                precondition(!saveChangesDialogSignature(rows, expectedText: prompt))
                rows[4]["enabled"] = true
                rows[6]["description"] = "Discard"
                precondition(!saveChangesDialogSignature(rows, expectedText: prompt))
                rows[6]["description"] = "Don’t Save"
                rows.append(["role": "AXButton", "identifier": "Extra", "description": "Extra", "enabled": true, "press": true])
                precondition(!saveChangesDialogSignature(rows, expectedText: prompt))
                print("exact Save Changes signature passed")
                """
            )
            compiled = subprocess.run(
                ["swiftc", str(program), "-o", str(executable)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(
                [str(executable)], check=False, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("exact Save Changes signature passed", result.stdout)

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
        self.assertIn("sameOpenedDestination(destinationPin, openedDestination)", body)
        self.assertIn("samePin(beforeOpenDestination, destinationPin)", body)
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

    def test_only_destination_ctime_may_change_after_open(self):
        def changed(command):
            before = _read_set(self.destination, self.destination_sha)[1]
            os.chmod(self.destination, self.destination.stat().st_mode & 0o777)
            after = _read_set(self.destination, self.destination_sha)[1]
            self.assertNotEqual(before, after)
            report = self.report(execute=True)
            report["destination_file"] = before
            report["destination_metadata_delta"] = ["ctime"]
            return report

        result = self.call(native=changed, execute=True)
        self.assertEqual(result["status"], "opened")
        self.assertNotEqual(
            result["destination_file"], result["destination_file_after_open"]
        )
        for field in (
            "path",
            "sha256",
            "bytes",
            "device",
            "inode",
            "mtime_seconds",
            "mtime_nanoseconds",
        ):
            self.assertEqual(
                result["destination_file"][field],
                result["destination_file_after_open"][field],
            )

    def test_post_open_receipt_tampering_or_other_file_changes_stop(self):
        for field in (
            "path",
            "sha256",
            "bytes",
            "device",
            "inode",
            "mtime_seconds",
            "mtime_nanoseconds",
            "ctime_seconds",
            "ctime_nanoseconds",
            "delta",
            "missing-after",
            "before-ctime",
        ):
            with self.subTest(field=field):
                report = self.report(execute=True)
                if field == "delta":
                    report["destination_metadata_delta"] = ["ctime"]
                elif field == "missing-after":
                    del report["destination_file_after_open"]
                elif field == "before-ctime":
                    report["destination_file"]["ctime_seconds"] += 1
                else:
                    report["destination_file_after_open"][field] = "tampered"
                self.assertEqual(self.call(report, execute=True)["status"], "stopped")

        for change in ("mtime", "inode", "bytes"):
            with self.subTest(actual_change=change):

                def changed(command, change=change):
                    before = _read_set(self.destination, self.destination_sha)[1]
                    if change == "mtime":
                        info = self.destination.stat()
                        os.utime(
                            self.destination,
                            ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000),
                        )
                    elif change == "inode":
                        replacement = self.root / "replacement.als"
                        replacement.write_bytes(self.destination.read_bytes())
                        os.replace(replacement, self.destination)
                    else:
                        report = self.report(execute=True)
                        self.destination.write_bytes(b"tampered")
                        return report
                    report = self.report(execute=True)
                    report["destination_file"] = before
                    report["destination_metadata_delta"] = ["ctime"]
                    return report

                self.assertEqual(
                    self.call(native=changed, execute=True)["status"], "stopped"
                )
                self.destination.write_bytes(b"reviewed-destination")

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
                        "sameOpenedDestination",
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
    let before = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[8]))) as! [String: Any]
    let after = try setPin(destination)
    let result: [String: Any] = ["pin": try setPin(source), "backup": backup,
                               "destination": after, "matches": sameOpenedDestination(before, after)]
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
        before = _read_set(self.destination, self.destination_sha)[1]
        before_path = self.root / "destination-before.json"
        before_path.write_text(json.dumps(before))
        command = [
            str(binary),
            str(self.source),
            str(self.destination),
            backup["receipt_path"],
            str(PID),
            BUNDLE,
            self.source_sha,
            self.destination_sha,
            str(before_path),
        ]
        checked = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(
            json.loads(checked.stdout),
            {
                "pin": pin,
                "backup": backup["backup"]["path"],
                "destination": before,
                "matches": True,
            },
        )
        os.chmod(self.destination, self.destination.stat().st_mode & 0o777)
        after = _read_set(self.destination, self.destination_sha)[1]
        self.assertNotEqual(before, after)
        checked = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["destination"], after)
        self.assertTrue(json.loads(checked.stdout)["matches"])
        for key in (
            "path",
            "sha256",
            "bytes",
            "device",
            "inode",
            "mtime_seconds",
            "mtime_nanoseconds",
        ):
            altered = dict(before)
            altered[key] = "tampered"
            before_path.write_text(json.dumps(altered))
            checked = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertFalse(json.loads(checked.stdout)["matches"], key)
        Path(backup["backup"]["path"]).write_bytes(b"tampered-backup")
        self.assertEqual(
            subprocess.run(
                command, check=False, capture_output=True, timeout=10
            ).returncode,
            1,
        )
