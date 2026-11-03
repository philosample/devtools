"""Policy and native-command boundary tests; no Live or AX actions are run."""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from codex_devtools.cli import main
from codex_devtools.live_settings_tab import (
    FORMAT,
    LIVE_BUNDLE_ID,
    run_live_settings_tab,
)

PID = 12345
WINDOW_TITLE = "Owned Test Set"
GENERATION = "1700000000123456"


def report(*, execute=False, status=None):
    actions = {
        "activate": "not_attempted",
        "raise_source_window": "not_attempted",
        "settings_menu": "not_attempted",
        "tempo_midi_tab": "not_attempted",
    }
    if execute:
        actions.update(
            activate="frontmost_verified",
            settings_menu="AXPress_succeeded",
            tempo_midi_tab="AXPress_succeeded",
        )
    state_read = execute
    selected = True if execute else None
    if not state_read:
        selection_evidence = "Tempo & MIDI radio-button selection state was not read"
    elif selected:
        selection_evidence = "Tempo & MIDI radio-button AXValue was read back selected"
    else:
        selection_evidence = (
            "Tempo & MIDI radio-button AXValue was read back not selected"
        )
    return {
        "format": FORMAT,
        "status": status or ("selected" if execute else "ready"),
        "phase": "complete" if execute else "preflight",
        "read_only": not execute,
        "target": {
            "pid": PID,
            "bundle_identifier": LIVE_BUNDLE_ID,
            "process_generation": GENERATION,
        },
        "expected_generation": GENERATION if execute else None,
        "source_window": {
            "title": WINDOW_TITLE,
            "on_current_desktop": True,
            "window_number": 381,
        },
        "settings": {
            "window_title": "Settings",
            "window_observed": execute,
            "tab_identifier": "midiTabButton",
            "tab_control_observed": execute,
            "tab_selected_state_read": state_read,
            "tab_selected": selected,
        },
        "actions": actions,
        "midi_preference_values_read": False,
        "midi_preference_values_changed": False,
        "manual_inspection_required": execute,
        "error": None,
        "evidence_limit": f"{selection_evidence}; MIDI preference checkbox values were neither read nor changed",
    }


class LiveSettingsTabPolicyTests(unittest.TestCase):
    def call_native(self, *, execute=False, native_report=None, **changes):
        result_report = native_report or report(execute=execute)

        def completed(command, **kwargs):
            return subprocess.CompletedProcess(
                command,
                0 if result_report["status"] != "stopped" else 2,
                json.dumps(result_report),
                "",
            )

        with (
            patch(
                "codex_devtools.live_settings_tab.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_settings_tab.subprocess.run", side_effect=completed
            ) as native,
        ):
            result = run_live_settings_tab(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                window_title=WINDOW_TITLE,
                expected_generation=GENERATION if execute else None,
                execute=execute,
                **changes,
            )
        self.native = native
        return result

    def test_default_inspection_is_read_only_and_does_not_pin_or_execute(self):
        result = self.call_native()
        command = self.native.call_args.args[0]
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["target"]["process_generation"], GENERATION)
        self.assertNotIn("--execute", command)
        self.assertNotIn("--expected-generation", command)
        self.assertEqual(self.native.call_args.kwargs["timeout"], 30)

    def test_execute_requires_and_passes_the_read_only_generation_token(self):
        result = self.call_native(execute=True)
        command = self.native.call_args.args[0]
        self.assertEqual(result["status"], "selected")
        self.assertEqual(
            command[command.index("--expected-generation") + 1], GENERATION
        )
        self.assertIn("--execute", command)
        self.assertEqual(result["midi_preference_values_read"], False)
        self.assertEqual(result["midi_preference_values_changed"], False)
        self.assertTrue(result["manual_inspection_required"])

    def test_execute_without_generation_and_non_live_bundle_fail_before_native(self):
        with (
            patch(
                "codex_devtools.live_settings_tab.platform.system",
                return_value="Darwin",
            ),
            patch("codex_devtools.live_settings_tab.subprocess.run") as native,
        ):
            with self.assertRaisesRegex(ValueError, "requires --expected-generation"):
                run_live_settings_tab(
                    PID,
                    expected_bundle_id=LIVE_BUNDLE_ID,
                    window_title=WINDOW_TITLE,
                    execute=True,
                )
            with self.assertRaisesRegex(ValueError, "must be com.ableton.live"):
                run_live_settings_tab(
                    PID,
                    expected_bundle_id="other.bundle",
                    window_title=WINDOW_TITLE,
                )
            native.assert_not_called()

    def test_wrong_generation_or_target_receipt_is_never_accepted(self):
        stale = report(execute=True)
        stale["target"]["process_generation"] = "1700000000123457"
        result = self.call_native(execute=True, native_report=stale)
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(result["manual_inspection_required"])
        self.assertIn("inspect Live before any retry", result["error"])

    def test_stale_generation_rejection_before_actions_is_not_misreported_unknown(self):
        stale = report(execute=True, status="stopped")
        stale["target"]["process_generation"] = None
        stale["actions"] = {
            "activate": "not_attempted",
            "raise_source_window": "not_attempted",
            "settings_menu": "not_attempted",
            "tempo_midi_tab": "not_attempted",
        }
        stale["manual_inspection_required"] = False
        stale["error"] = (
            "target process generation differs from the read-only inspection"
        )
        result = self.call_native(execute=True, native_report=stale)
        self.assertEqual(result["status"], "stopped")
        self.assertFalse(result["manual_inspection_required"])
        self.assertIsNone(result["target"]["process_generation"])

    def test_receipt_rejects_selected_claim_when_state_is_unobserved_or_false(self):
        cases = (
            (False, None, "Tempo & MIDI radio-button AXValue was read back selected"),
            (True, False, "Tempo & MIDI radio-button AXValue was read back selected"),
        )
        for state_read, selected, inaccurate_claim in cases:
            with self.subTest(state_read=state_read, selected=selected):
                stopped = report(execute=True, status="stopped")
                stopped["settings"]["tab_selected_state_read"] = state_read
                stopped["settings"]["tab_selected"] = selected
                stopped["error"] = "helper stopped after a partial observation"
                stopped["evidence_limit"] = (
                    inaccurate_claim
                    + "; MIDI preference checkbox values were neither read nor changed"
                )
                result = self.call_native(execute=True, native_report=stopped)
                self.assertEqual(result["phase"], "native-helper-invalid-receipt")
                self.assertIn("selection state was not read", result["evidence_limit"])
                self.assertTrue(result["manual_inspection_required"])

                if state_read and selected is False:
                    stopped["evidence_limit"] = (
                        "Tempo & MIDI radio-button AXValue was read back not selected"
                        "; MIDI preference checkbox values were neither read nor changed"
                    )
                    result = self.call_native(execute=True, native_report=stopped)
                    self.assertEqual(result["status"], "stopped")
                    self.assertIn("not selected", result["evidence_limit"])

    def test_existing_settings_off_desktop_stops_before_any_action(self):
        stopped = report(execute=True, status="stopped")
        stopped["settings"].update(
            window_observed=True,
            tab_control_observed=False,
            tab_selected_state_read=False,
            tab_selected=None,
        )
        stopped["evidence_limit"] = (
            "Tempo & MIDI radio-button selection state was not read; "
            "MIDI preference checkbox values were neither read nor changed"
        )
        stopped["actions"] = {
            "activate": "not_attempted",
            "raise_source_window": "not_attempted",
            "settings_menu": "not_attempted",
            "tempo_midi_tab": "not_attempted",
        }
        stopped["manual_inspection_required"] = False
        stopped["error"] = (
            "exact PID/window title is not uniquely visible on the current Desktop"
        )
        result = self.call_native(execute=True, native_report=stopped)
        self.assertEqual(result["status"], "stopped")
        self.assertTrue(result["settings"]["window_observed"])
        self.assertEqual(set(result["actions"].values()), {"not_attempted"})
        self.assertFalse(result["manual_inspection_required"])

    def test_timeout_is_outcome_unknown_and_never_retried(self):
        with (
            patch(
                "codex_devtools.live_settings_tab.platform.system",
                return_value="Darwin",
            ),
            patch(
                "codex_devtools.live_settings_tab.subprocess.run",
                side_effect=subprocess.TimeoutExpired("swift", 30),
            ) as native,
        ):
            result = run_live_settings_tab(
                PID,
                expected_bundle_id=LIVE_BUNDLE_ID,
                window_title=WINDOW_TITLE,
                expected_generation=GENERATION,
                execute=True,
            )
        self.assertEqual(result["phase"], "native-helper-timeout")
        self.assertTrue(result["manual_inspection_required"])
        self.assertIn("selection state was not read", result["evidence_limit"])
        self.assertEqual(set(result["actions"].values()), {"outcome_unknown"})
        self.assertEqual(native.call_count, 1)

    def test_cli_routes_only_the_explicit_execute_flag(self):
        output = io.StringIO()
        with (
            patch(
                "codex_devtools.live_settings_tab.platform.system",
                return_value="Darwin",
            ),
            patch("codex_devtools.live_settings_tab.subprocess.run") as native,
            redirect_stdout(output),
        ):
            native.return_value = subprocess.CompletedProcess(
                [], 0, json.dumps(report()), ""
            )
            status = main(
                [
                    "ax",
                    "live-settings-tab",
                    "--pid",
                    str(PID),
                    "--expected-bundle-id",
                    LIVE_BUNDLE_ID,
                    "--window-title",
                    WINDOW_TITLE,
                ]
            )
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "ready")
        self.assertNotIn("--execute", native.call_args.args[0])

    def test_native_adapter_uses_current_desktop_and_exact_ax_targets_only(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools/live_settings_tab.swift"
        )
        text = source.read_text(encoding="utf-8")
        self.assertIn("CGWindowListCopyWindowInfo", text)
        self.assertIn(".optionOnScreenOnly, .excludeDesktopElements", text)
        self.assertIn('let settingsMenuTitle = "Settings..."', text)
        self.assertIn('let tempoMidiTabIdentifier = "midiTabButton"', text)
        self.assertIn("kAXRadioButtonRole", text)
        self.assertIn("Tempo & MIDI radio-button selection state was not read", text)
        self.assertIn(
            "Tempo & MIDI radio-button AXValue was read back not selected", text
        )
        self.assertLess(
            text.index("let selectedValue = try radioValue(matches[0])"),
            text.index(
                "tabSelectedStateRead = true",
                text.index("let selectedValue = try radioValue(matches[0])"),
            ),
        )
        self.assertIn(
            "currentDesktopWindow(pid: options.pid, expectedTitle: options.windowTitle)",
            text,
        )
        self.assertIn("currentProcess.activate()", text)
        preflight = text.index('phase = "preflight-existing-settings-window"')
        self.assertLess(preflight, text.index("currentProcess.activate()"))
        menu_phase = text.index('phase = "open-settings-menu"')
        menu_recheck = text.index(
            "existingSettingsWindow(app, pid: options.pid)", menu_phase
        )
        menu_press = text.index('action: "settings_menu"', menu_phase)
        self.assertLess(menu_recheck, menu_press)
        self.assertLess(
            text.index(
                "currentDesktopWindow(pid: options.pid, expectedTitle: options.windowTitle)\n    let currentProcess"
            ),
            text.index("currentProcess.activate()"),
        )
        self.assertNotIn("AXUIElementSetAttributeValue", text)
        self.assertNotIn("CGEvent", text)
        self.assertNotIn("NSAppleScript", text)
        self.assertNotIn("osascript", text)

    @unittest.skipUnless(
        sys.platform == "darwin" and shutil.which("swiftc"),
        "macOS Swift compiler required",
    )
    def test_swift_adapter_compiles_without_running_live(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "codex_devtools/live_settings_tab.swift"
        )
        with tempfile.TemporaryDirectory(prefix="live-settings-tab-build-") as temp:
            binary = Path(temp) / "live-settings-tab-check"
            result = subprocess.run(
                [
                    "swiftc",
                    "-framework",
                    "ApplicationServices",
                    "-framework",
                    "AppKit",
                    "-framework",
                    "CoreGraphics",
                    str(source),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(binary.is_file())


if __name__ == "__main__":
    unittest.main()
