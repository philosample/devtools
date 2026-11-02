import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools.cli import main
from codex_devtools.live_set_lifecycle import FORMAT, run_lifecycle

PID = 84769
BUNDLE = "com.ableton.live"


def ready(source: Path, destination: Path | None) -> dict:
    return {
        "format": FORMAT,
        "status": "ready",
        "target": {"pid": PID, "bundle_identifier": BUNDLE, "process_name": "Live"},
        "source_set_url": source.as_uri(),
        "destination_set_url": destination.as_uri() if destination else None,
        "read_only": True,
        "actions": {
            "focus": "not_attempted",
            "menu_navigation": "not_attempted",
            "save_as_menu": "not_attempted",
            "save": "not_attempted",
            "quit": "not_attempted",
            "open": "not_attempted",
        },
    }


def reopened(source: Path, destination: Path) -> dict:
    return {
        "format": FORMAT,
        "status": "reopened",
        "target": {"pid": PID, "bundle_identifier": BUNDLE, "process_name": "Live"},
        "reopened_target": {
            "pid": PID + 1,
            "bundle_identifier": BUNDLE,
            "process_name": "Live",
        },
        "source_set_url": source.as_uri(),
        "destination_set_url": destination.as_uri(),
        "read_only": False,
        "actions": {
            "focus": "verified",
            "menu_navigation": "verified",
            "save_as_menu": "verified_panel",
            "save": "destination_observed",
            "quit": "terminated",
            "open": "exact_set_window_observed",
        },
    }


class LiveSetLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source_dir = self.root / "Set Project"
        self.source_dir.mkdir()
        self.source = self.source_dir / "Source.als"
        self.source.write_bytes(b"disposable-set")
        self.destination = self.source_dir / "Recall-copy.als"
        self.app = self.root / "Ableton Live.app"
        self.app.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def call(self, result: subprocess.CompletedProcess, **kwargs) -> dict:
        with (
            patch(
                "codex_devtools.live_set_lifecycle.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_set_lifecycle.subprocess.run", return_value=result
            ) as run,
        ):
            report = run_lifecycle(
                PID,
                expected_bundle_id=BUNDLE,
                source_set=self.source,
                **kwargs,
            )
        self.native_run = run
        return report

    def test_default_is_read_only_and_execute_is_explicit(self):
        report = self.call(
            subprocess.CompletedProcess(
                [], 0, json.dumps(ready(self.source, self.destination)), ""
            ),
            destination_set=self.destination,
        )
        self.assertEqual(report["status"], "ready")
        args = self.native_run.call_args.args[0]
        self.assertNotIn("--execute", args)
        self.assertIn("--destination-set", args)

    def test_execute_requires_destination_and_exact_app_path(self):
        for kwargs, message in (
            ({"execute": True}, "destination .als path"),
            (
                {"execute": True, "destination_set": self.destination},
                "Live.app bundle path",
            ),
        ):
            with (
                self.subTest(kwargs=kwargs),
                patch(
                    "codex_devtools.live_set_lifecycle.platform.system",
                    return_value="Darwin",
                ),
                self.assertRaisesRegex(ValueError, message),
            ):
                run_lifecycle(
                    PID,
                    expected_bundle_id=BUNDLE,
                    source_set=self.source,
                    **kwargs,
                )

    def test_existing_destination_and_git_ancestor_are_refused_before_native_call(self):
        self.destination.touch()
        with (
            patch(
                "codex_devtools.live_set_lifecycle.platform.system",
                return_value="Darwin",
            ),
            patch("codex_devtools.live_set_lifecycle.subprocess.run") as run,
            self.assertRaisesRegex(ValueError, "must not already exist"),
        ):
            run_lifecycle(
                PID,
                expected_bundle_id=BUNDLE,
                source_set=self.source,
                destination_set=self.destination,
            )
        run.assert_not_called()
        self.destination.unlink()
        git_parent = self.root / "repo"
        git_parent.mkdir()
        (git_parent / ".git").mkdir()
        with (
            patch(
                "codex_devtools.live_set_lifecycle.platform.system",
                return_value="Darwin",
            ),
            patch("codex_devtools.live_set_lifecycle.subprocess.run") as run,
            self.assertRaisesRegex(ValueError, "outside Git"),
        ):
            run_lifecycle(
                PID,
                expected_bundle_id=BUNDLE,
                source_set=self.source,
                destination_set=git_parent / "Copy.als",
            )
        run.assert_not_called()

    def test_dangling_destination_symlink_is_refused_before_native_call(self):
        self.destination.symlink_to(self.source_dir / "missing-target.als")
        with (
            patch(
                "codex_devtools.live_set_lifecycle.platform.system",
                return_value="Darwin",
            ),
            patch("codex_devtools.live_set_lifecycle.subprocess.run") as run,
            self.assertRaisesRegex(ValueError, "symbolic links"),
        ):
            run_lifecycle(
                PID,
                expected_bundle_id=BUNDLE,
                source_set=self.source,
                destination_set=self.destination,
            )
        run.assert_not_called()

    def test_swift_rechecks_destination_leaf_with_lstat_at_save_boundary(self):
        native_source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_set_lifecycle.swift"
        ).read_text()
        self.assertIn("path.withCString { lstat($0, &info) }", native_source)
        self.assertIn("guard errno == ENOENT", native_source)
        save_boundary = native_source.index(
            "try ensureNoSymlinkChain(destination, mustExist: false, outsideGit: true)"
        )
        save_attempt = native_source.index("saveAttempted = true", save_boundary)
        self.assertLess(save_boundary, save_attempt)

    def test_swift_activates_exact_live_and_verifies_focus_before_shortcut(self):
        native_source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_set_lifecycle.swift"
        ).read_text()
        activation = native_source.index("process.activate()")
        menu = native_source.index('findMenuItem(app: appElement, menuTitle: "File"')
        save_window_focus = native_source.index(
            "focusWindow(appElement, pid: options.pid, window: saveWindow)"
        )
        field_focus = native_source.index(
            "try focusSaveNameField(nameField, app: appElement)", save_window_focus
        )
        field_check = native_source.index(
            "focusedIs(appElement, nameField)", field_focus
        )
        shortcut = native_source.index(
            "postChord(pid: options.pid, key: 5)", field_check
        )
        self.assertLess(activation, menu)
        self.assertLess(save_window_focus, field_focus)
        self.assertLess(field_focus, field_check)
        self.assertLess(field_check, shortcut)
        focus_helper = native_source[
            native_source.index("func focusSaveNameField(") : native_source.index(
                "func focusedIs("
            )
        ]
        self.assertIn("AXUIElementIsAttributeSettable", focus_helper)
        self.assertIn(
            "AXUIElementSetAttributeValue(element, kAXFocusedAttribute", focus_helper
        )
        self.assertIn("kCFBooleanTrue", focus_helper)
        self.assertIn("kAXFocusedAttribute as String", focus_helper)
        self.assertNotIn("press(", focus_helper)

        return_key = native_source.index(
            "try postKeyStroke(pid: options.pid, code: 36)"
        )
        path_focus = native_source.rindex(
            "focusedIs(appElement, pathField)", 0, return_key
        )
        path_window = native_source.rindex(
            "focusedWindowIs(appElement, goToSheet)", 0, return_key
        )
        path_frontmost = native_source.rindex(
            "waitForFrontmost(pid: options.pid)", 0, return_key
        )
        self.assertLess(path_frontmost, return_key)
        self.assertLess(path_window, return_key)
        self.assertLess(path_focus, return_key)

    def test_go_to_path_focus_must_be_the_observed_sheet_child(self):
        native_source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_set_lifecycle.swift"
        ).read_text()
        wait_sheet = native_source[
            native_source.index("func waitForGoToPathField(") : native_source.index(
                "func waitForGoToSheetDismissed("
            )
        ]
        self.assertIn("savePanelAttribute(saveWindow, kAXChildrenAttribute", wait_sheet)
        self.assertIn('identifier: "GoToWindow"', wait_sheet)
        self.assertIn("savePanelElements([sheet])", wait_sheet)
        self.assertIn('identifier: "PathTextField"', wait_sheet)

        run_source = native_source[native_source.index("func run(_ options:") :]
        go_to = run_source.index(
            "let (goToSheet, pathField) = try waitForGoToPathField(saveWindow)"
        )
        focus_check = run_source.index("focusedWindowIs(appElement, goToSheet)", go_to)
        return_key = run_source.index(
            "try postKeyStroke(pid: options.pid, code: 36)", focus_check
        )
        dismissed = run_source.index(
            "try waitForGoToSheetDismissed(refreshedWindow)", return_key
        )
        self.assertLess(go_to, focus_check)
        self.assertLess(focus_check, return_key)
        self.assertLess(return_key, dismissed)

    def test_go_to_folder_uses_one_frontmost_session_chord_with_consistent_flags(self):
        native_source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_set_lifecycle.swift"
        ).read_text()
        event_stroke = native_source[
            native_source.index("func postKeyStroke(") : native_source.index(
                "func waitForWindow("
            )
        ]
        self.assertIn("frontmostApplication?.processIdentifier == pid", event_stroke)
        self.assertIn("keyDown.post(tap: .cgSessionEventTap)", event_stroke)
        self.assertIn("defer { keyUp.post(tap: .cgSessionEventTap) }", event_stroke)
        self.assertNotIn("postToPid", event_stroke)
        down = event_stroke.index("keyDown.post(tap: .cgSessionEventTap)")
        release = event_stroke.index("defer { keyUp.post(tap: .cgSessionEventTap) }")
        foreground_check = event_stroke.index(
            'try require(NSWorkspace.shared.frontmostApplication?.processIdentifier == pid, "foreground changed during keyboard stroke'
        )
        self.assertLess(release, down)
        self.assertLess(down, foreground_check)
        chord = event_stroke[event_stroke.index("func postChord(") :]
        self.assertIn("let modifiers: CGEventFlags = [.maskCommand, .maskShift]", chord)
        self.assertIn("postKeyStroke(pid: pid, code: key, flags: modifiers)", chord)

    def test_swift_skips_stale_save_panel_rows_but_keeps_exact_control_uniqueness(self):
        native_source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_set_lifecycle.swift"
        ).read_text()
        self.assertIn("error == .noValue || error == .invalidUIElement", native_source)
        self.assertIn(
            "guard try savePanelAttribute(element, kAXRoleAttribute as String) != nil",
            native_source,
        )
        self.assertIn(
            'try require(matches.count == 1, "expected exactly one Save panel AX',
            native_source,
        )
        self.assertIn(
            "let saveRows = try savePanelElements([saveWindow])", native_source
        )
        self.assertIn(
            'uniqueSavePanelElement(refreshed, role: kAXButtonRole as String, title: "Save"',
            native_source,
        )

    def test_unsupported_optional_save_panel_identity_attributes_are_nonmatches(self):
        # Reduced from the complete r6 Save-panel receipt: real rows omit
        # optional AXTitle or AXIdentifier while required controls are present.
        observed_rows = [
            {
                "role": "AXTextField",
                "identifier": "saveAsNameTextField",
                "value": "TR1000-tooling-recall-r71.als",
                "attribute_errors": {"title": -25205},
            },
            {
                "role": "AXButton",
                "attribute_errors": {"identifier": -25212, "title": -25205},
            },
            {
                "role": "AXButton",
                "identifier": "OKButton",
                "title": "Save",
                "enabled": True,
            },
        ]
        self.assertEqual(observed_rows[0]["attribute_errors"]["title"], -25205)
        self.assertEqual(observed_rows[1]["attribute_errors"]["identifier"], -25212)
        self.assertEqual(observed_rows[2]["title"], "Save")

        native_source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_set_lifecycle.swift"
        ).read_text()
        attribute_reader = native_source[
            native_source.index("func savePanelAttribute(") : native_source.index(
                "func savePanelElements("
            )
        ]
        self.assertIn("error == .attributeUnsupported", attribute_reader)
        self.assertIn("name == (kAXTitleAttribute as String)", attribute_reader)
        self.assertIn('name == "AXIdentifier"', attribute_reader)
        self.assertIn(
            "if error == .attributeUnsupported && optionalIdentityAttribute { return nil }",
            attribute_reader,
        )
        self.assertIn(
            'throw LifecycleFailure(phase: phase, message: "Save panel AX read',
            attribute_reader,
        )

        exact_matcher = native_source[
            native_source.index("func uniqueSavePanelElement(") : native_source.index(
                "func waitForGoToPathField("
            )
        ]
        self.assertIn("try require(matches.count == 1", exact_matcher)
        self.assertIn("is disabled", exact_matcher)

    def test_stale_save_panel_rows_count_toward_element_bound(self):
        native_source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools"
            / "live_set_lifecycle.swift"
        ).read_text()
        scanner = native_source[
            native_source.index("func savePanelElements(") : native_source.index(
                "func savePanelMatches("
            )
        ]
        increment = scanner.index("visitedCount += 1")
        bound = scanner.index("visitedCount <= maxElements")
        stale_skip = scanner.index(
            "guard try savePanelAttribute(element, kAXRoleAttribute as String) != nil"
        )
        self.assertLess(increment, bound)
        self.assertLess(bound, stale_skip)

    def test_outer_timeout_preserves_unknown_execute_outcomes(self):
        with (
            patch(
                "codex_devtools.live_set_lifecycle.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_set_lifecycle.subprocess.run",
                side_effect=subprocess.TimeoutExpired("swift", 240),
            ) as run,
        ):
            report = run_lifecycle(
                PID,
                expected_bundle_id=BUNDLE,
                source_set=self.source,
                destination_set=self.destination,
                app_bundle=self.app,
                execute=True,
            )
        self.assertEqual(run.call_args.kwargs["timeout"], 240)
        self.assertEqual(report["phase"], "outer-timeout")
        self.assertTrue(report["manual_inspection_required"])
        self.assertTrue(report["read_only"] is False)
        self.assertEqual(set(report["actions"].values()), {"outcome_unknown"})

    def test_stopped_save_panel_ambiguity_is_reported_without_retry(self):
        receipt = {
            "format": FORMAT,
            "status": "stopped",
            "target": {"pid": PID, "bundle_identifier": BUNDLE},
            "phase": "save-dialog",
            "error": "expected exactly one AXTextField saveAsNameTextField",
            "actions": {
                "focus": "verified",
                "menu_navigation": "outcome_unknown",
                "save_as_menu": "outcome_unknown",
                "save": "not_attempted",
                "quit": "not_attempted",
                "open": "not_attempted",
            },
            "manual_inspection_required": True,
        }
        report = self.call(
            subprocess.CompletedProcess([], 1, json.dumps(receipt), ""),
            destination_set=self.destination,
            app_bundle=self.app,
            execute=True,
        )
        self.assertEqual(report["phase"], "save-dialog")
        self.assertTrue(report["manual_inspection_required"])
        self.assertEqual(self.native_run.call_count, 1)
        self.assertIn("--execute", self.native_run.call_args.args[0])

    def test_save_attempt_uncertainty_is_preserved_as_stop_receipt(self):
        receipt = {
            "format": FORMAT,
            "status": "stopped",
            "target": {"pid": PID, "bundle_identifier": BUNDLE},
            "phase": "verify-saved-set",
            "error": "Save outcome unknown",
            "actions": {
                "focus": "verified",
                "menu_navigation": "outcome_unknown",
                "save_as_menu": "outcome_unknown",
                "save": "outcome_unknown",
                "quit": "not_attempted",
                "open": "not_attempted",
            },
            "manual_inspection_required": True,
        }
        report = self.call(
            subprocess.CompletedProcess([], 1, json.dumps(receipt), ""),
            destination_set=self.destination,
            app_bundle=self.app,
            execute=True,
        )
        self.assertEqual(report["actions"]["save"], "outcome_unknown")
        self.assertFalse(
            any(value == "terminated" for value in report["actions"].values())
        )

    def test_ready_and_reopened_process_identity_are_bound(self):
        report = self.call(
            subprocess.CompletedProcess(
                [], 0, json.dumps(reopened(self.source, self.destination)), ""
            ),
            destination_set=self.destination,
            app_bundle=self.app,
            execute=True,
        )
        self.assertEqual(report["reopened_target"]["pid"], PID + 1)

        wrong = reopened(self.source, self.destination)
        wrong["reopened_target"]["bundle_identifier"] = "other.app"
        with (
            patch(
                "codex_devtools.live_set_lifecycle.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_set_lifecycle.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, json.dumps(wrong), ""),
            ),
            self.assertRaisesRegex(ValueError, "identity or action receipt"),
        ):
            run_lifecycle(
                PID,
                expected_bundle_id=BUNDLE,
                source_set=self.source,
                destination_set=self.destination,
                app_bundle=self.app,
                execute=True,
            )

    def test_cli_returns_nonzero_for_stopped_receipt(self):
        receipt = {
            "format": FORMAT,
            "status": "stopped",
            "phase": "save-dialog",
            "error": "ambiguous dialog",
            "actions": {
                "focus": "not_attempted",
                "menu_navigation": "not_attempted",
                "save_as_menu": "not_attempted",
                "save": "not_attempted",
                "quit": "not_attempted",
                "open": "not_attempted",
            },
            "manual_inspection_required": False,
        }
        with (
            patch(
                "codex_devtools.live_set_lifecycle.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_set_lifecycle.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 1, json.dumps(receipt), ""
                ),
            ),
            redirect_stdout(io.StringIO()),
        ):
            status = main(
                [
                    "ax",
                    "live-set-lifecycle",
                    "--pid",
                    str(PID),
                    "--expected-bundle-id",
                    BUNDLE,
                    "--source-set",
                    str(self.source),
                ]
            )
        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
