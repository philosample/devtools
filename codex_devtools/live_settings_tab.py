"""Inspect or open Live Settings on the Tempo & MIDI tab through bounded AX."""

from __future__ import annotations

import json
import platform
import re
import subprocess
from pathlib import Path

FORMAT = "codex-devtools-live-settings-tab-v1"
LIVE_BUNDLE_ID = "com.ableton.live"
MAX_WINDOW_TITLE_BYTES = 256
NATIVE_TIMEOUT_SECONDS = 30
_GENERATION = re.compile(r"^[1-9][0-9]{5,24}$")
_ACTION_VALUES = {
    "activate": {"not_attempted", "outcome_unknown", "frontmost_verified"},
    "raise_source_window": {"not_attempted", "outcome_unknown", "AXRaise_succeeded"},
    "settings_menu": {"not_attempted", "outcome_unknown", "AXPress_succeeded"},
    "tempo_midi_tab": {"not_attempted", "outcome_unknown", "AXPress_succeeded"},
}


def _validate_inputs(
    pid: int,
    expected_bundle_id: str,
    window_title: str,
    expected_generation: str | None,
    execute: bool,
) -> None:
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("PID must be a positive 32-bit integer")
    if expected_bundle_id != LIVE_BUNDLE_ID:
        raise ValueError("expected bundle identifier must be com.ableton.live")
    if (
        not isinstance(window_title, str)
        or not window_title
        or len(window_title.encode("utf-8")) > MAX_WINDOW_TITLE_BYTES
        or any(ord(character) < 32 for character in window_title)
    ):
        raise ValueError(
            "exact Live window title must contain 1 to 256 printable UTF-8 bytes"
        )
    if expected_generation is not None and (
        not isinstance(expected_generation, str)
        or _GENERATION.fullmatch(expected_generation) is None
    ):
        raise ValueError(
            "expected process generation must be the integer token from inspection"
        )
    if execute and expected_generation is None:
        raise ValueError(
            "--execute requires --expected-generation from a prior inspection"
        )


def _unknown_receipt(
    *,
    pid: int,
    bundle_id: str,
    window_title: str,
    generation: str | None,
    execute: bool,
    phase: str,
    error: str,
) -> dict:
    actions = {
        name: ("outcome_unknown" if execute else "not_attempted")
        for name in _ACTION_VALUES
    }
    return {
        "format": FORMAT,
        "status": "stopped",
        "phase": phase,
        "read_only": not execute,
        "target": {
            "pid": pid,
            "bundle_identifier": bundle_id,
            "process_generation": None,
        },
        "expected_generation": generation,
        "source_window": {
            "title": window_title,
            "on_current_desktop": False,
            "window_number": None,
        },
        "settings": {
            "window_title": "Settings",
            "window_observed": False,
            "tab_identifier": "midiTabButton",
            "tab_control_observed": False,
            "tab_selected_state_read": False,
            "tab_selected": None,
        },
        "actions": actions,
        "midi_preference_values_read": False,
        "midi_preference_values_changed": False,
        "manual_inspection_required": execute,
        "error": error,
        "evidence_limit": _selection_evidence_limit(False, None),
    }


def _selection_evidence_limit(state_read: bool, selected: bool | None) -> str:
    if not state_read:
        selection = "Tempo & MIDI radio-button selection state was not read"
    elif selected:
        selection = "Tempo & MIDI radio-button AXValue was read back selected"
    else:
        selection = "Tempo & MIDI radio-button AXValue was read back not selected"
    return f"{selection}; MIDI preference checkbox values were neither read nor changed"


def _valid_report(
    value: object,
    *,
    pid: int,
    bundle_id: str,
    window_title: str,
    generation: str | None,
    execute: bool,
    returncode: int,
) -> bool:
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        return False
    expected_top_level = {
        "format",
        "status",
        "phase",
        "read_only",
        "target",
        "expected_generation",
        "source_window",
        "settings",
        "actions",
        "midi_preference_values_read",
        "midi_preference_values_changed",
        "manual_inspection_required",
        "error",
        "evidence_limit",
    }
    if set(value) != expected_top_level:
        return False
    target = value.get("target")
    if (
        not isinstance(target, dict)
        or target.get("pid") != pid
        or target.get("bundle_identifier") != bundle_id
    ):
        return False
    target_generation = target.get("process_generation")
    if target_generation is not None and (
        not isinstance(target_generation, str)
        or _GENERATION.fullmatch(target_generation) is None
    ):
        return False
    if value.get("status") != "stopped" and (
        target_generation is None
        or (generation is not None and target_generation != generation)
    ):
        return False
    if value.get("expected_generation") != generation:
        return False
    window = value.get("source_window")
    if (
        not isinstance(window, dict)
        or window.get("title") != window_title
        or type(window.get("on_current_desktop")) is not bool
        or (
            window.get("window_number") is not None
            and type(window.get("window_number")) is not int
        )
    ):
        return False
    settings = value.get("settings")
    if (
        not isinstance(settings, dict)
        or settings.get("window_title") != "Settings"
        or type(settings.get("window_observed")) is not bool
        or settings.get("tab_identifier") != "midiTabButton"
        or type(settings.get("tab_control_observed")) is not bool
        or type(settings.get("tab_selected_state_read")) is not bool
        or (
            settings.get("tab_selected") is not None
            and type(settings.get("tab_selected")) is not bool
        )
        or (
            settings["tab_selected_state_read"]
            is not (settings["tab_selected"] is not None)
        )
    ):
        return False
    actions = value.get("actions")
    if not isinstance(actions, dict) or set(actions) != set(_ACTION_VALUES):
        return False
    if any(
        actions.get(name) not in allowed for name, allowed in _ACTION_VALUES.items()
    ):
        return False
    if (
        value.get("read_only") is not (not execute)
        or value.get("midi_preference_values_read") is not False
        or value.get("midi_preference_values_changed") is not False
        or value.get("manual_inspection_required")
        is not any(action != "not_attempted" for action in actions.values())
        or not isinstance(value.get("phase"), str)
        or not value["phase"]
        or value.get("evidence_limit")
        != _selection_evidence_limit(
            settings["tab_selected_state_read"], settings["tab_selected"]
        )
    ):
        return False
    if value.get("error") is not None and not isinstance(value.get("error"), str):
        return False
    if value["status"] == "ready":
        return (
            not execute
            and returncode == 0
            and window["on_current_desktop"] is True
            and actions == dict.fromkeys(_ACTION_VALUES, "not_attempted")
        )
    if value["status"] == "selected":
        return (
            execute
            and returncode == 0
            and window["on_current_desktop"] is True
            and settings["window_observed"] is True
            and settings["tab_control_observed"] is True
            and settings["tab_selected_state_read"] is True
            and settings["tab_selected"] is True
            and actions["activate"] == "frontmost_verified"
            and actions["settings_menu"] == "AXPress_succeeded"
            and actions["tempo_midi_tab"] == "AXPress_succeeded"
            and value["manual_inspection_required"] is True
        )
    if value["status"] == "stopped":
        return (
            returncode != 0
            and isinstance(value.get("error"), str)
            and bool(value["error"])
        )
    return False


def run_live_settings_tab(
    pid: int,
    *,
    expected_bundle_id: str,
    window_title: str,
    expected_generation: str | None = None,
    execute: bool = False,
) -> dict:
    """Inspect or open Settings on Tempo & MIDI for one exact Live process/window."""
    _validate_inputs(
        pid, expected_bundle_id, window_title, expected_generation, execute
    )
    if platform.system() != "Darwin":
        raise ValueError("Live Settings AX control requires macOS")

    helper = Path(__file__).with_name("live_settings_tab.swift")
    command = [
        "/usr/bin/swift",
        str(helper),
        "--pid",
        str(pid),
        "--bundle-id",
        expected_bundle_id,
        "--window-title",
        window_title,
    ]
    if expected_generation is not None:
        command.extend(("--expected-generation", expected_generation))
    if execute:
        command.append("--execute")

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=NATIVE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _unknown_receipt(
            pid=pid,
            bundle_id=expected_bundle_id,
            window_title=window_title,
            generation=expected_generation,
            execute=execute,
            phase="native-helper-timeout",
            error="native AX helper timed out; inspect Live before any retry",
        )
    except (OSError, subprocess.SubprocessError) as error:
        return _unknown_receipt(
            pid=pid,
            bundle_id=expected_bundle_id,
            window_title=window_title,
            generation=expected_generation,
            execute=False,
            phase="native-helper-launch",
            error=f"native AX helper could not launch: {error}",
        )

    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        if execute:
            return _unknown_receipt(
                pid=pid,
                bundle_id=expected_bundle_id,
                window_title=window_title,
                generation=expected_generation,
                execute=True,
                phase="native-helper-invalid-output",
                error=result.stderr.strip()[-2000:]
                or "native AX helper returned invalid JSON; inspect Live before any retry",
            )
        raise ValueError(
            result.stderr.strip()[-2000:] or "native AX helper returned invalid JSON"
        )

    if not _valid_report(
        report,
        pid=pid,
        bundle_id=expected_bundle_id,
        window_title=window_title,
        generation=expected_generation,
        execute=execute,
        returncode=result.returncode,
    ):
        if execute:
            return _unknown_receipt(
                pid=pid,
                bundle_id=expected_bundle_id,
                window_title=window_title,
                generation=expected_generation,
                execute=True,
                phase="native-helper-invalid-receipt",
                error="native AX helper receipt failed target/action validation; inspect Live before any retry",
            )
        raise ValueError("native AX helper returned an invalid read-only receipt")
    return report
