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

    def test_stopped_save_panel_ambiguity_is_reported_without_retry(self):
        receipt = {
            "format": FORMAT,
            "status": "stopped",
            "target": {"pid": PID, "bundle_identifier": BUNDLE},
            "phase": "save-dialog",
            "error": "expected exactly one AXTextField saveAsNameTextField",
            "actions": {
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
