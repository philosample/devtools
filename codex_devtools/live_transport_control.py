"""Inspect and explicitly bound the transport of one exact Live Set."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import stat
import subprocess
import tempfile
from pathlib import Path

from .live_midi_sync_control import (
    _replace_owned_json,
    _safe_new_path,
    _write_new_json,
    run_live_midi_sync,
)

FORMAT = "codex-devtools-live-transport-receipt-v1"
MIN_PLAY_SECONDS = 1
MAX_PLAY_SECONDS = 15
MAX_EXPECTED_OUTPUT_COUNT = 128
COMPILE_TIMEOUT_SECONDS = 60
NATIVE_TIMEOUT_SECONDS = 240


def _set_file(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TypeError("exact Set path must be a Path")
    raw = str(path.expanduser())
    if (
        not path.expanduser().is_absolute()
        or os.path.normpath(raw) != raw
        or path.expanduser().suffix.lower() != ".als"
        or "\\" in raw
        or any(ord(character) < 32 for character in raw)
    ):
        raise ValueError("exact Set path must be normalized, absolute, and end in .als")
    resolved = path.expanduser()
    cursor = Path("/")
    for part in resolved.parts[1:-1]:
        cursor /= part
        try:
            mode = cursor.lstat().st_mode
        except OSError as error:
            raise ValueError(f"Set directory is unavailable: {cursor}") from error
        if not stat.S_ISDIR(mode):
            raise ValueError("Set path components must be real directories")
    try:
        mode = resolved.lstat().st_mode
    except OSError as error:
        raise ValueError(f"selected Set is unavailable: {resolved}") from error
    if not stat.S_ISREG(mode):
        raise ValueError("selected Set must be a regular file, not a symlink")
    return resolved


def _initial_receipt(
    action: str, pid: int, bundle_id: str, set_path: Path, play_for_seconds: int | None
) -> dict:
    if action == "focus-window":
        actions = {
            "activate": {"attempted": False, "outcome": "not_attempted"},
            "raise": {"attempted": False, "outcome": "not_attempted"},
        }
    elif action == "inspect":
        actions = {}
    else:
        actions = {
            "settings_close": {"attempted": False, "outcome": "not_attempted"},
            "play": {"attempted": False, "outcome": "not_attempted"},
            "stop": {"attempted": False, "outcome": "not_attempted"},
        }
    return {
        "format": FORMAT,
        "status": "stopped",
        "phase": "preflight",
        "action": action,
        "read_only": action == "inspect",
        "target": {"pid": pid, "bundle_identifier": bundle_id},
        "set_url": set_path.as_uri(),
        "play_for_seconds": play_for_seconds,
        "actions": actions,
        "manual_inspection_required": False,
        "error": "native helper has not run; no action was attempted",
    }


def _valid_report(
    report: object,
    *,
    action: str,
    pid: int,
    bundle_id: str,
    set_path: Path,
    play_for_seconds: int | None,
    returncode: int,
) -> bool:
    if not isinstance(report, dict) or report.get("format") != FORMAT:
        return False
    target = report.get("target")
    if (
        not isinstance(target, dict)
        or target.get("pid") != pid
        or target.get("bundle_identifier") != bundle_id
        or report.get("set_url") != set_path.as_uri()
        or report.get("action") != action
        or report.get("read_only") is not (action == "inspect")
        or type(report.get("manual_inspection_required")) is not bool
    ):
        return False
    if action == "inspect":
        if returncode == 1:
            phase = report.get("phase")
            error = report.get("error")
            return (
                report.get("status") == "stopped"
                and not report["manual_inspection_required"]
                and isinstance(phase, str)
                and bool(phase.strip())
                and isinstance(error, str)
                and bool(error.strip())
                and report.get("actions") == {}
                and "transport" not in report
            )
        transport = report.get("transport")
        return (
            returncode == 0
            and report.get("status") == "observed"
            and not report["manual_inspection_required"]
            and isinstance(transport, dict)
            and all(
                type(transport.get(name)) is int and transport[name] in (0, 1)
                for name in ("play", "link", "arrangement_record", "session_record")
            )
            and report.get("actions") == {}
        )
    rows = report.get("actions")
    if not isinstance(rows, dict) or set(rows) != {"settings_close", "play", "stop"}:
        return False
    allowed = {"not_attempted", "known", "unknown", "verified_noop"}
    for row in rows.values():
        if (
            not isinstance(row, dict)
            or type(row.get("attempted")) is not bool
            or row.get("outcome") not in allowed
            or row["attempted"]
            is (row["outcome"] in {"not_attempted", "verified_noop"})
        ):
            return False
    unknown = any(row["outcome"] == "unknown" for row in rows.values())
    play_attempted_without_verified_stop = (
        action == "play"
        and rows["play"]["outcome"] in {"known", "unknown"}
        and rows["stop"]["outcome"] != "known"
    )
    transport = report.get("transport")
    transport_before_stop = report.get("transport_before_stop")
    monitored_states = ("link", "arrangement_record", "session_record")
    if transport_before_stop is not None and (
        action != "play"
        or not isinstance(transport_before_stop, dict)
        or any(
            type(transport_before_stop.get(name)) is not int
            or transport_before_stop[name] not in (0, 1)
            for name in ("play", *monitored_states)
        )
    ):
        return False
    transport_state_needs_review = action == "play" and any(
        isinstance(state, dict)
        and any(state.get(name) == 1 for name in monitored_states)
        for state in (transport_before_stop, transport)
    )
    if report["manual_inspection_required"] is not (
        unknown or play_attempted_without_verified_stop or transport_state_needs_review
    ):
        return False
    if action == "play" and rows["play"]["attempted"]:
        if report.get("play_readback") not in {
            "verified",
            "not_observed_before_bound",
            "unknown",
        }:
            return False
        if rows["play"]["outcome"] == "known" and report["play_readback"] != "verified":
            return False
    if rows["stop"]["attempted"]:
        if report.get("stop_play_readback") not in {
            "verified",
            "not_observed_before_bound",
            "unknown",
        }:
            return False
        if (
            rows["stop"]["outcome"] == "known"
            and report["stop_play_readback"] != "verified"
        ):
            return False
    if action == "play" and rows["stop"]["attempted"] and transport_before_stop is None:
        return False
    if report.get("play_for_seconds") != play_for_seconds:
        return False
    if report.get("status") == "complete":
        transport = report.get("transport")
        if (
            returncode != 0
            or not isinstance(transport, dict)
            or any(
                type(transport.get(name)) is not int or transport[name] not in (0, 1)
                for name in ("play", "link", "arrangement_record", "session_record")
            )
            or transport.get("play") != 0
        ):
            return False
        if action == "play":
            return (
                rows["settings_close"]["outcome"] == "known"
                and rows["play"]["outcome"] == "known"
                and rows["stop"]["outcome"] == "known"
            )
        return (
            rows["play"]["outcome"] == "not_attempted"
            and rows["settings_close"]["outcome"] == "not_attempted"
            and rows["stop"]["outcome"] in {"known", "verified_noop"}
        )
    return (
        returncode == 1
        and report.get("status") == "stopped"
        and isinstance(report.get("phase"), str)
        and isinstance(report.get("error"), str)
    )


def _valid_focus_report(
    report: object,
    *,
    execute: bool,
    pid: int,
    bundle_id: str,
    set_path: Path,
    identity: str,
    generation: int,
    preflight_hash: str | None,
    returncode: int,
) -> bool:
    expected_format = (
        "codex-devtools-live-focus-window-result-v1"
        if execute
        else "codex-devtools-live-focus-window-preflight-v1"
    )
    expected_status = "complete" if execute else "preflight_ready"
    if (
        not isinstance(report, dict)
        or report.get("format") != expected_format
        or report.get("status") != expected_status
    ):
        return False
    target = report.get("target")
    window = report.get("window")
    geometry = window.get("geometry") if isinstance(window, dict) else None
    valid_geometry = (
        isinstance(geometry, dict)
        and set(geometry) == {"x", "y", "width", "height"}
        and all(
            type(value) in (int, float) and math.isfinite(value)
            for value in geometry.values()
        )
        and geometry["width"] > 0
        and geometry["height"] > 0
    )
    return (
        returncode == 0
        and report.get("read_only") is (not execute)
        and isinstance(target, dict)
        and target.get("pid") == pid
        and target.get("bundle_identifier") == bundle_id
        and target.get("process_generation") == generation
        and report.get("set_url") == set_path.as_uri()
        and isinstance(window, dict)
        and window.get("identity") == identity
        and window.get("role") == "AXWindow"
        and isinstance(window.get("subrole"), str)
        and isinstance(window.get("title"), str)
        and bool(window["title"])
        and (
            window.get("identifier") is None
            or isinstance(window.get("identifier"), str)
        )
        and (window.get("document") is None or isinstance(window.get("document"), str))
        and valid_geometry
        and type(window.get("cg_window_number")) is int
        and window["cg_window_number"] >= 0
        and (
            window.get("window_number") is None
            or type(window.get("window_number")) is int
        )
        and isinstance(report.get("set_window_identity"), str)
        and (
            report.get("actions") == {"activate": "known", "raise": "known"}
            if execute
            else report.get("actions") == {}
        )
        and (
            execute
            or (
                isinstance(report.get("snapshot_observed_at_utc"), str)
                and isinstance(report.get("snapshot_launch_date"), str)
            )
        )
        and (not execute or report.get("preflight_sha256") == preflight_hash)
    )


def _compile_helper(source: Path, binary: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "/usr/bin/xcrun",
            "swiftc",
            "-framework",
            "ApplicationServices",
            "-framework",
            "AppKit",
            "-framework",
            "CoreGraphics",
            "-framework",
            "CryptoKit",
            str(source),
            "-o",
            str(binary),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=COMPILE_TIMEOUT_SECONDS,
    )


def _native_command(
    binary: Path,
    *,
    pid: int,
    bundle_id: str,
    set_path: Path,
    action: str,
    play_for_seconds: int | None,
    expected_count: int | None,
    baseline_path: Path | None,
    baseline_hash: str | None,
    window_identity: str | None = None,
    expected_generation: int | None = None,
    snapshot_observed_at_utc: str | None = None,
    snapshot_launch_date: str | None = None,
    preflight_path: Path | None = None,
    preflight_hash: str | None = None,
) -> list[str]:
    command = [
        str(binary),
        "--pid",
        str(pid),
        "--bundle-id",
        bundle_id,
        "--set",
        str(set_path),
        "--action",
        action,
    ]
    if action in {"play", "stop"} or (
        action == "focus-window" and preflight_path is not None
    ):
        command.append("--execute")
    if play_for_seconds is not None:
        command.extend(("--play-for-seconds", str(play_for_seconds)))
    if expected_count is not None:
        command.extend(("--expected-count", str(expected_count)))
    if baseline_path is not None and baseline_hash is not None:
        command.extend(
            ("--baseline", str(baseline_path), "--baseline-sha256", baseline_hash)
        )
    if window_identity is not None:
        command.extend(("--window-identity", window_identity))
    if expected_generation is not None:
        command.extend(("--expected-generation", str(expected_generation)))
    if snapshot_observed_at_utc is not None:
        command.extend(("--snapshot-observed-at-utc", snapshot_observed_at_utc))
    if snapshot_launch_date is not None:
        command.extend(("--snapshot-launch-date", snapshot_launch_date))
    if preflight_path is not None and preflight_hash is not None:
        command.extend(
            (
                "--preflight-receipt",
                str(preflight_path),
                "--preflight-sha256",
                preflight_hash,
            )
        )
    return command


def run_live_transport(
    pid: int,
    *,
    expected_bundle_id: str,
    set_path: Path,
    action: str | None = None,
    play_for_seconds: int | None = None,
    expected_count: int | None = None,
    receipt_path: Path | None = None,
    execute: bool = False,
    window_identity: str | None = None,
    expected_generation: int | None = None,
    snapshot_observed_at_utc: str | None = None,
    snapshot_launch_date: str | None = None,
    preflight_receipt_path: Path | None = None,
    preflight_sha256: str | None = None,
) -> dict:
    """Inspect by default; explicitly play briefly or stop one exact Set."""
    if platform.system() != "Darwin":
        raise ValueError("native Live transport control requires macOS")
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("PID must be a positive 32-bit integer")
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id.strip():
        raise ValueError("expected bundle identifier is required")
    focus_options = (
        window_identity,
        expected_generation,
        snapshot_observed_at_utc,
        snapshot_launch_date,
        preflight_receipt_path,
        preflight_sha256,
    )
    if action != "focus-window" and any(value is not None for value in focus_options):
        raise ValueError("focus-window options require --action focus-window")
    source_set = _set_file(set_path)
    if not execute:
        if action == "focus-window":
            if play_for_seconds is not None or expected_count is not None:
                raise ValueError("transport options are invalid for focus-window")
            if preflight_receipt_path is not None or preflight_sha256 is not None:
                raise ValueError(
                    "preflight receipt options are valid only for focus-window execution"
                )
            selected_action = action
        elif (
            action is not None
            or play_for_seconds is not None
            or expected_count is not None
            or receipt_path is not None
        ):
            raise ValueError("action options and --receipt require explicit --execute")
        else:
            selected_action = "inspect"
    else:
        if action not in {"play", "stop", "focus-window"}:
            raise ValueError("--execute requires --action play, stop, or focus-window")
        if receipt_path is None:
            raise ValueError("--execute requires a new --receipt path")
        if action == "focus-window":
            if play_for_seconds is not None or expected_count is not None:
                raise ValueError("transport options are invalid for focus-window")
            if type(expected_generation) is not int or expected_generation <= 0:
                raise ValueError(
                    "focus-window requires the inspected process generation"
                )
            if (
                preflight_receipt_path is None
                or not isinstance(preflight_sha256, str)
                or len(preflight_sha256) != 64
            ):
                raise ValueError(
                    "focus-window execution requires a preflight receipt and its reviewed SHA-256"
                )
            if window_identity is not None:
                raise ValueError(
                    "focus-window execution uses the identity pinned in its preflight receipt"
                )
        elif action == "play":
            if (
                type(play_for_seconds) is not int
                or not MIN_PLAY_SECONDS <= play_for_seconds <= MAX_PLAY_SECONDS
            ):
                raise ValueError(
                    "play duration must be an integer from 1 to 15 seconds"
                )
            if (
                type(expected_count) is not int
                or not 1 <= expected_count <= MAX_EXPECTED_OUTPUT_COUNT
            ):
                raise ValueError(
                    "Play requires an independently supplied expected output count from 1 to 128"
                )
        elif play_for_seconds is not None or expected_count is not None:
            raise ValueError(
                "--play-for-seconds and --expected-count are valid only for play"
            )
        selected_action = action

    initial = _initial_receipt(
        selected_action, pid, expected_bundle_id, source_set, play_for_seconds
    )
    if selected_action == "focus-window" and not execute:
        initial["read_only"] = True
        initial["actions"] = {}
        if not isinstance(window_identity, str) or not window_identity.isdecimal():
            raise ValueError(
                "focus-window preflight requires a CFHash window identity from a fresh AX snapshot"
            )
        if type(expected_generation) is not int or expected_generation <= 0:
            raise ValueError(
                "focus-window preflight requires the inspected process generation"
            )
        if (
            not isinstance(snapshot_observed_at_utc, str)
            or not snapshot_observed_at_utc
        ):
            raise ValueError(
                "focus-window preflight requires the fresh AX snapshot observation time"
            )
        if not isinstance(snapshot_launch_date, str) or not snapshot_launch_date:
            raise ValueError(
                "focus-window preflight requires the AX snapshot process launch date"
            )
        if receipt_path is None:
            raise ValueError("focus-window preflight requires a new receipt path")
    if selected_action == "focus-window" and execute:
        preflight_path = preflight_receipt_path
        if (
            preflight_path is None
            or not preflight_path.is_file()
            or preflight_path.is_symlink()
        ):
            raise ValueError(
                "focus-window preflight receipt must be an existing regular file"
            )
        actual_hash = hashlib.sha256(preflight_path.read_bytes()).hexdigest()
        if actual_hash != preflight_sha256:
            raise ValueError(
                "focus-window preflight receipt hash does not match the reviewed SHA-256"
            )
    identity = None
    if execute or selected_action == "focus-window":
        receipt_path = _safe_new_path(receipt_path, "receipt")
        identity = _write_new_json(receipt_path, initial)

    def save(report: dict) -> dict:
        nonlocal identity
        if receipt_path is not None and identity is not None:
            identity = _replace_owned_json(receipt_path, identity, report)
        return report

    if selected_action != "inspect" and receipt_path is None:
        raise ValueError("action requires a receipt path")
    try:
        with tempfile.TemporaryDirectory(
            prefix="live-transport-", dir="/private/tmp"
        ) as temporary:
            temporary_dir = Path(temporary)
            binary = temporary_dir / "live_transport"
            source = Path(__file__).with_name("live_transport.swift")
            try:
                compilation = _compile_helper(source, binary)
            except (OSError, subprocess.SubprocessError) as error:
                initial["phase"] = "helper-compile"
                initial["error"] = f"native helper compile failed: {error}"
                return (
                    save(initial)
                    if execute or selected_action == "focus-window"
                    else initial
                )
            if compilation.returncode:
                initial["phase"] = "helper-compile"
                initial["error"] = (
                    compilation.stderr.strip()[-2000:]
                    or "native helper compilation failed"
                )
                return (
                    save(initial)
                    if execute or selected_action == "focus-window"
                    else initial
                )

            baseline_path = None
            baseline_hash = None
            if selected_action == "play":
                initial["phase"] = "output-isolation-preflight"
                initial["error"] = "complete output Sync inspection has not passed"
                save(initial)
                try:
                    baseline = run_live_midi_sync(
                        pid,
                        expected_bundle_id=expected_bundle_id,
                        expected_count=expected_count,
                    )
                    outputs = baseline.get("outputs")
                    if (
                        baseline.get("expected_output_count") != expected_count
                        or not isinstance(outputs, list)
                        or len(outputs) != expected_count
                    ):
                        raise ValueError(
                            f"output Sync row count does not match expected {expected_count}"
                        )
                    if not all(row.get("value") == 0 for row in outputs):
                        raise ValueError(
                            f"all {expected_count} output Sync controls must be off before Play"
                        )
                    baseline_path = temporary_dir / "baseline.json"
                    _write_new_json(baseline_path, baseline)
                    baseline_hash = hashlib.sha256(
                        baseline_path.read_bytes()
                    ).hexdigest()
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    initial["phase"] = "output-isolation-preflight"
                    initial["error"] = str(error)
                    return save(initial)

            command = _native_command(
                binary,
                pid=pid,
                bundle_id=expected_bundle_id,
                set_path=source_set,
                action=selected_action,
                play_for_seconds=play_for_seconds,
                expected_count=expected_count,
                baseline_path=baseline_path,
                baseline_hash=baseline_hash,
                window_identity=window_identity,
                expected_generation=expected_generation,
                snapshot_observed_at_utc=snapshot_observed_at_utc,
                snapshot_launch_date=snapshot_launch_date,
                preflight_path=preflight_receipt_path,
                preflight_hash=preflight_sha256,
            )
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=NATIVE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                uncertain = _initial_receipt(
                    selected_action,
                    pid,
                    expected_bundle_id,
                    source_set,
                    play_for_seconds,
                )
                uncertain["phase"] = "native-helper-timeout"
                uncertain["error"] = (
                    "native helper timed out; exact Live transport state requires inspection"
                )
                if selected_action == "inspect":
                    uncertain["error"] = (
                        "native inspection timed out; transport observation is unavailable"
                    )
                elif selected_action == "focus-window":
                    if execute:
                        for name in ("activate", "raise"):
                            uncertain["actions"][name] = {
                                "attempted": True,
                                "outcome": "unknown",
                            }
                        uncertain["error"] = (
                            "focus helper timed out; inspect the Live window before retry"
                        )
                        uncertain["manual_inspection_required"] = True
                    else:
                        uncertain["read_only"] = True
                        uncertain["actions"] = {}
                        uncertain["error"] = (
                            "read-only window preflight timed out; no focus action was attempted"
                        )
                elif selected_action == "play":
                    for name in ("settings_close", "play", "stop"):
                        uncertain["actions"][name] = {
                            "attempted": True,
                            "outcome": "unknown",
                        }
                else:
                    uncertain["actions"]["stop"] = {
                        "attempted": True,
                        "outcome": "unknown",
                    }
                if selected_action != "focus-window":
                    uncertain["manual_inspection_required"] = (
                        selected_action != "inspect"
                    )
                return (
                    save(uncertain)
                    if execute or selected_action == "focus-window"
                    else uncertain
                )
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError:
                report = None
            if selected_action == "focus-window":
                focus_identity = window_identity
                if execute:
                    try:
                        preflight_data = json.loads(
                            preflight_receipt_path.read_text(encoding="utf-8")
                        )
                        focus_identity = preflight_data["window"]["identity"]
                    except (
                        OSError,
                        UnicodeError,
                        json.JSONDecodeError,
                        KeyError,
                        TypeError,
                    ) as error:
                        raise ValueError(
                            "focus-window preflight receipt has no valid selected identity"
                        ) from error
                valid = _valid_focus_report(
                    report,
                    execute=execute,
                    pid=pid,
                    bundle_id=expected_bundle_id,
                    set_path=source_set,
                    identity=focus_identity or "",
                    generation=expected_generation or 0,
                    preflight_hash=preflight_sha256,
                    returncode=result.returncode,
                )
            else:
                valid = _valid_report(
                    report,
                    action=selected_action,
                    pid=pid,
                    bundle_id=expected_bundle_id,
                    set_path=source_set,
                    play_for_seconds=play_for_seconds,
                    returncode=result.returncode,
                )
            if not valid:
                uncertain = _initial_receipt(
                    selected_action,
                    pid,
                    expected_bundle_id,
                    source_set,
                    play_for_seconds,
                )
                uncertain["phase"] = "native-helper-invalid-receipt"
                uncertain["error"] = (
                    "native helper returned an invalid or incomplete receipt; inspect Live before retry"
                )
                if selected_action == "play":
                    for name in ("settings_close", "play", "stop"):
                        uncertain["actions"][name] = {
                            "attempted": True,
                            "outcome": "unknown",
                        }
                elif selected_action == "stop":
                    uncertain["actions"]["stop"] = {
                        "attempted": True,
                        "outcome": "unknown",
                    }
                if selected_action == "focus-window":
                    if execute:
                        for name in ("activate", "raise"):
                            uncertain["actions"][name] = {
                                "attempted": True,
                                "outcome": "unknown",
                            }
                        uncertain["manual_inspection_required"] = True
                    else:
                        uncertain["read_only"] = True
                        uncertain["actions"] = {}
                        uncertain["manual_inspection_required"] = False
                else:
                    uncertain["manual_inspection_required"] = (
                        selected_action != "inspect"
                    )
                if selected_action == "inspect":
                    uncertain.update(
                        format=FORMAT,
                        status="stopped",
                        read_only=True,
                        target={"pid": pid, "bundle_identifier": expected_bundle_id},
                        set_url=source_set.as_uri(),
                        actions={},
                    )
                    return uncertain
                return (
                    save(uncertain)
                    if execute or selected_action == "focus-window"
                    else uncertain
                )
            return (
                save(report) if execute or selected_action == "focus-window" else report
            )
    except OSError as error:
        initial["phase"] = "native-helper-launch"
        initial["error"] = f"native helper launch failed: {error}"
        return (
            save(initial) if execute or selected_action == "focus-window" else initial
        )
