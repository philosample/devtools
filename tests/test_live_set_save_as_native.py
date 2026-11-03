"""Save As filesystem/receipt and actual Swift policy ABI checks; no Live actions."""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codex_devtools.cli import main
from codex_devtools.live_set_open import _read_set
from codex_devtools.live_set_save_as import ACTIONS, FORMAT, run_set_save_as
from codex_devtools.maxfiles import sha256

PID = 84769
BUNDLE = "com.ableton.live"
TEXT = "The Live Set you are about to save is located outside of a Project folder. Live must save the Set into a Project folder. Please choose a Project folder to save the Set into."


class SameProcessSaveAsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "Source.als"
        self.source.write_bytes(b"owned-preimage")
        self.sha = sha256(self.source.read_bytes())
        self.directory = self.root / "preservation"
        self.filename = "Preserved.als"
        self.selected = self.directory / self.filename
        self.final = self.directory / "Preserved Project" / self.filename
        self.backups = self.root / "backups"
        self.backups.mkdir(mode=0o700)
        self.app = self.root / "Live.app"
        self.app.mkdir()

    def options(self, **changes):
        result = {
            "expected_bundle_id": BUNDLE,
            "source_set": self.source,
            "source_sha256": self.sha,
            "destination_directory": self.directory,
            "destination_filename": self.filename,
            "expected_final_set": self.final,
            "app_bundle": self.app,
            "backup_root": self.backups,
        }
        result.update(changes)
        return result

    def native(self, command, *, execute=False, modal=False, changes=None):
        source = _read_set(self.source, self.sha)[1]
        actions = dict.fromkeys(ACTIONS, "not_attempted")
        report = {
            "format": FORMAT,
            "status": "saved-copy" if execute else "ready",
            "read_only": not execute,
            "target": {"pid": PID, "bundle_identifier": BUNDLE},
            "app": str(self.app),
            "source_set_url": self.source.as_uri(),
            "destination_set_url": self.selected.as_uri(),
            "expected_final_set_url": self.final.as_uri(),
            "source_file": source,
            "project_required_dialog": modal,
            "transport": dict.fromkeys(
                ("play", "link", "arrangement_record", "session_record"), 0
            ),
            "actions": actions,
        }
        if execute:
            proof_path = command[command.index("--backup-receipt") + 1]
            proof = json.loads(Path(proof_path).read_text())
            self.assertEqual(
                Path(proof["backup"]["path"]).read_bytes(), b"owned-preimage"
            )
            self.assertEqual(list(self.directory.iterdir()), [])
            self.final.parent.mkdir(parents=True, exist_ok=True)
            self.final.write_bytes(b"new-saved-unsaved-memory-state")
            report.update(
                same_process=True,
                saved_set_url=self.final.as_uri(),
                stable_observations=3,
                destination_file=_read_set(self.final, sha256(self.final.read_bytes()))[
                    1
                ],
                source_file_after_save=source,
                destination_directory=proof["destination_directory"],
                backup_receipt=proof_path,
            )
            actions.update(focus="verified", save="destination_observed")
            if modal:
                actions["project_prompt"] = "verified_save_panel"
            else:
                actions.update(
                    menu_navigation="verified", save_as_menu="verified_panel"
                )
        if changes:
            changes(report)
        return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

    def call(self, *, native=None, **options):
        execute = options.get("execute", False)
        modal = options.get("project_required_dialog", False)
        with (
            patch(
                "codex_devtools.live_set_save_as.platform.system", return_value="Darwin"
            ),
            patch("codex_devtools.live_set_save_as.subprocess.run") as runner,
        ):
            runner.side_effect = native or (
                lambda command, **kwargs: self.native(
                    command, execute=execute, modal=modal
                )
            )
            report = run_set_save_as(PID, **self.options(**options))
        self.runner = runner
        return report

    def test_read_only_ready_checks_exact_plan_without_creating_files(self):
        result = self.call()
        self.assertEqual(result["status"], "ready")
        self.assertIsNone(result["source_backup"])
        self.assertFalse(self.directory.exists())
        self.assertEqual(list(self.backups.iterdir()), [])
        command = self.runner.call_args.args[0]
        self.assertIn("--save-copy-only", command)
        self.assertNotIn("--execute", command)
        self.assertNotIn("--open-existing", command)

    def test_same_process_normal_and_exact_modal_save_preserve_preimage(self):
        for modal in (False, True):
            with self.subTest(modal=modal):
                self.setUp()
                result = self.call(execute=True, project_required_dialog=modal)
                self.assertEqual(result["status"], "saved-copy")
                self.assertTrue(result["same_process"])
                self.assertEqual(self.source.read_bytes(), b"owned-preimage")
                self.assertEqual(result["actions"]["quit"], "not_attempted")
                self.assertEqual(result["actions"]["open"], "not_attempted")
                proof = result["source_backup"]
                self.assertEqual(
                    Path(proof["backup"]["path"]).read_bytes(), self.source.read_bytes()
                )
                self.assertEqual(
                    json.loads(Path(result["receipt_path"]).read_text()), result
                )
                self.assertEqual(self.runner.call_count, 1)

    def test_direct_final_form_is_explicit_not_a_fallback(self):
        self.final = self.selected
        result = self.call(execute=True, expected_final_set=self.final)
        self.assertEqual(result["destination_file"]["path"], str(self.selected))

    def test_invalid_paths_hash_filename_or_existing_target_stop_before_native(self):
        for changes in (
            {"source_sha256": "0" * 64},
            {"destination_filename": "../escape.als"},
            {"destination_filename": ".als"},
            {"destination_filename": " bad.als"},
            {"destination_filename": "bad\n.als"},
            {"expected_final_set": self.directory / "Other.als"},
            {"expected_final_set": self.root / "Elsewhere.als"},
            {"execute": 1},
            {"project_required_dialog": "yes"},
        ):
            with (
                self.subTest(changes=changes),
                patch(
                    "codex_devtools.live_set_save_as.platform.system",
                    return_value="Darwin",
                ),
                patch("codex_devtools.live_set_save_as.subprocess.run") as native,
            ):
                with self.assertRaises((ValueError, TypeError)):
                    run_set_save_as(PID, **self.options(**changes))
                native.assert_not_called()
        self.directory.mkdir()
        with (
            patch(
                "codex_devtools.live_set_save_as.platform.system", return_value="Darwin"
            ),
            patch("codex_devtools.live_set_save_as.subprocess.run") as native,
            self.assertRaisesRegex(ValueError, "already exist"),
        ):
            run_set_save_as(PID, **self.options(execute=True))
        native.assert_not_called()
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_symlink_git_and_public_backup_roots_are_refused(self):
        for mode in ("symlink", "git", "backup-mode"):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == "symlink":
                    self.directory.symlink_to(self.backups)
                if mode == "git":
                    (self.root / ".git").mkdir()
                if mode == "backup-mode":
                    self.backups.chmod(0o755)
                with (
                    patch(
                        "codex_devtools.live_set_save_as.platform.system",
                        return_value="Darwin",
                    ),
                    patch("codex_devtools.live_set_save_as.subprocess.run") as native,
                    self.assertRaises(ValueError),
                ):
                    run_set_save_as(PID, **self.options(execute=True))
                native.assert_not_called()

    def test_wrong_pid_url_filename_flags_or_forbidden_actions_are_unknown(self):
        for mode in (
            "pid",
            "url",
            "filename",
            "flags",
            "same-pid",
            "quit",
            "open",
            "observations",
            "hash",
        ):
            with self.subTest(mode=mode):
                self.setUp()

                def change(report, mode=mode):
                    if mode == "pid":
                        report["target"]["pid"] += 1
                    if mode == "url":
                        report["saved_set_url"] = self.source.as_uri()
                    if mode == "filename":
                        report["destination_set_url"] = (
                            self.directory / "Other.als"
                        ).as_uri()
                    if mode == "flags":
                        report["transport"]["play"] = 1
                    if mode == "same-pid":
                        report["same_process"] = False
                    if mode in ("quit", "open"):
                        report["actions"][mode] = "outcome_unknown"
                    if mode == "observations":
                        report["stable_observations"] = 2
                    if mode == "hash":
                        report["destination_file"]["sha256"] = "0" * 64

                result = self.call(
                    execute=True,
                    native=lambda command, **kw: self.native(
                        command, execute=True, changes=change
                    ),
                )
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["actions"]["save"], "outcome_unknown")
                self.assertTrue(result["manual_inspection_required"])
                self.assertEqual(self.runner.call_count, 1)
                self.assertTrue(
                    Path(result["source_backup"]["backup"]["path"]).is_file()
                )

    def test_outer_deadline_keeps_unknown_save_and_backup_without_followup(self):
        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        result = self.call(execute=True, project_required_dialog=True, native=timeout)
        self.assertEqual(result["phase"], "outer-timeout")
        self.assertEqual(result["actions"]["save"], "outcome_unknown")
        self.assertEqual(result["actions"]["project_prompt"], "outcome_unknown")
        self.assertEqual(result["actions"]["quit"], "not_attempted")
        self.assertTrue(Path(result["receipt_path"]).is_file())
        self.assertEqual(self.runner.call_count, 1)

    def test_native_wrong_dialog_or_unknown_save_stops_without_another_action(self):
        for mid_save in (False, True):
            with self.subTest(mid_save=mid_save):
                self.setUp()
                actions = dict.fromkeys(ACTIONS, "not_attempted")
                if mid_save:
                    actions.update(
                        focus="verified",
                        project_prompt="verified_save_panel",
                        save="outcome_unknown",
                    )
                native_report = {
                    "format": FORMAT,
                    "status": "stopped",
                    "read_only": False,
                    "target": {"pid": PID, "bundle_identifier": BUNDLE},
                    "phase": "verify-saved-set" if mid_save else "preflight",
                    "error": "unknown Save effect"
                    if mid_save
                    else "wrong exact Project dialog",
                    "actions": actions,
                    "manual_inspection_required": mid_save,
                }
                result = self.call(
                    execute=True,
                    project_required_dialog=True,
                    native=lambda command, report=native_report, **kw: (
                        subprocess.CompletedProcess(command, 1, json.dumps(report), "")
                    ),
                )
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["actions"], actions)
                self.assertEqual(result["manual_inspection_required"], mid_save)
                self.assertEqual(self.runner.call_count, 1)
                self.assertTrue(Path(result["source_backup"]["receipt_path"]).is_file())

    def test_changed_source_or_backup_cannot_produce_saved_copy(self):
        for mode in ("source", "backup"):
            with self.subTest(mode=mode):
                self.setUp()

                def changed(command, mode=mode, **kwargs):
                    report = self.native(command, execute=True)
                    if mode == "source":
                        self.source.write_bytes(b"changed-source")
                    else:
                        proof = json.loads(
                            Path(
                                command[command.index("--backup-receipt") + 1]
                            ).read_text()
                        )
                        Path(proof["backup"]["path"]).write_bytes(b"changed-backup")
                    return report

                self.assertEqual(
                    self.call(execute=True, native=changed)["status"], "stopped"
                )

    def test_cli_exposes_only_bounded_mode_and_explicit_execution(self):
        args = [
            "ax",
            "live-set-save-as",
            "--pid",
            str(PID),
            "--expected-bundle-id",
            BUNDLE,
            "--source-set",
            str(self.source),
            "--source-sha256",
            self.sha,
            "--destination-directory",
            str(self.directory),
            "--destination-filename",
            self.filename,
            "--expected-final-set",
            str(self.final),
            "--app",
            str(self.app),
            "--backup-root",
            str(self.backups),
        ]
        with (
            patch(
                "codex_devtools.live_set_save_as.run_set_save_as",
                return_value={"status": "ready"},
            ) as call,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(args), 0)
        self.assertFalse(call.call_args.kwargs["execute"])
        self.assertFalse(call.call_args.kwargs["project_required_dialog"])

    @unittest.skipUnless(
        shutil.which("swiftc") and os.uname().sysname == "Darwin",
        "Swift macOS filesystem/policy ABI",
    )
    def test_actual_swift_prompt_backup_and_deadline_policy_without_ax_entrypoint(self):
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
        text = source[
            source.index("let projectRequiredText =") : source.index(
                "func projectRequiredSignature("
            )
        ]
        program = "\n".join(
            [
                "import Foundation\nimport Darwin\nimport CryptoKit",
                options,
                "struct LifecycleFailure: Error { let phase: String; let message: String }\nstruct SetObservationChanged: Error {}",
                'var phase = "test"\nvar saveCopyDeadline: TimeInterval?\nlet maxPinnedSetBytes: Int64 = 128 * 1024 * 1024',
                text,
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
                        "saveCopyDirectoryPin",
                        "checkedSaveCopyBackup",
                        "projectRequiredSignature",
                        "checkSaveCopyDeadline",
                    )
                ],
                """
do {
    let args = CommandLine.arguments
    if args[1] == "signature" {
        let rows = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[2]))) as! [[String: Any]]
        print(projectRequiredSignature(rows))
    } else if args[1] == "deadline" {
        saveCopyDeadline = ProcessInfo.processInfo.systemUptime - 1
        try checkSaveCopyDeadline(); print("unexpected")
    } else {
        var options = Options(); options.pid = pid_t(args[7])!; options.bundleID = args[8]; options.sourceSHA256 = args[9]; options.backupReceipt = args[2]
        let pin = try checkedSaveCopyBackup(options, source: canonicalURL(args[3]), selected: canonicalURL(args[4]), final: canonicalURL(args[5]), directory: URL(fileURLWithPath: args[6]))
        print(String(data: try JSONSerialization.data(withJSONObject: pin), encoding: .utf8)!)
    }
} catch { fputs(String(describing: error), stderr); exit(1) }
""",
            ]
        )
        swift = self.root / "policy.swift"
        binary = self.root / "policy"
        swift.write_text(program)
        compiled = subprocess.run(
            ["swiftc", str(swift), "-o", str(binary)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        rows = [
            {"role": "AXWindow", "subrole": "AXDialog", "title": ""},
            {"role": "AXGroup"},
            {"role": "AXStaticText", "value": ""},
            {"role": "AXStaticText", "identifier": "DialogText", "value": TEXT},
            {
                "role": "AXButton",
                "identifier": "Cancel",
                "description": "Cancel",
                "enabled": True,
                "press": True,
            },
            {
                "role": "AXButton",
                "identifier": "SaveAs...",
                "description": "Save As...",
                "enabled": True,
                "press": True,
            },
        ]
        fixture = self.root / "dialog.json"
        for mode in ("valid", "text", "button", "disabled", "extra", "role"):
            changed = json.loads(json.dumps(rows))
            if mode == "text":
                changed[3]["value"] = "Another prompt"
            if mode == "button":
                changed[5]["identifier"] = "Replace"
            if mode == "disabled":
                changed[5]["enabled"] = False
            if mode == "extra":
                changed.append({"role": "AXButton", "identifier": "Allow"})
            if mode == "role":
                changed[0]["subrole"] = "AXStandardWindow"
            fixture.write_text(json.dumps(changed))
            checked = subprocess.run(
                [str(binary), "signature", str(fixture)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(
                checked.stdout.strip(), "true" if mode == "valid" else "false"
            )
        self.assertEqual(
            subprocess.run(
                [str(binary), "deadline"], check=False, capture_output=True, timeout=10
            ).returncode,
            1,
        )
        # Reuse the real Python backup writer, while native UI remains mocked.
        self.directory.mkdir(mode=0o700)
        from codex_devtools.live_set_save_as import _backup

        proof = _backup(
            self.backups,
            self.source.read_bytes(),
            _read_set(self.source, self.sha)[1],
            self.selected,
            self.final,
            self.directory,
            PID,
            BUNDLE,
            False,
        )
        command = [
            str(binary),
            "proof",
            proof["receipt_path"],
            str(self.source),
            str(self.selected),
            str(self.final),
            str(self.directory),
            str(PID),
            BUNDLE,
            self.sha,
        ]
        checked = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout), proof["destination_directory"])
        Path(proof["backup"]["path"]).write_bytes(b"tampered")
        self.assertEqual(
            subprocess.run(
                command, check=False, capture_output=True, timeout=10
            ).returncode,
            1,
        )

    def test_swift_reuses_save_sequence_returns_before_quit_and_never_adopts_other_prompts(
        self,
    ):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools/live_set_lifecycle.swift"
        ).read_text()
        run = source[
            source.index("func run(_ options:") : source.index("// Existing-Set mode")
        ]
        self.assertEqual(run.count("try press(saveButton)"), 1)
        self.assertEqual(run.count("try press(projectButton)"), 1)
        self.assertLess(
            run.index('"status": "saved-copy"'), run.index('phase = "quit-menu"')
        )
        self.assertIn("if let initialProjectDialog", run)
        self.assertIn("projectRequiredSignature(rows)", source)
        self.assertLess(
            run.index('identifier(saveWindow) == "save-panel"'),
            run.index("try setString(nameField"),
        )
        self.assertIn("stable >= 2", source)
        self.assertIn("try saveCopyTransport(sourceWindow)", run)
        self.assertIn("try checkedSaveCopyBackup", run)
        self.assertIn("try checkSaveCopyDeadline()", run)
