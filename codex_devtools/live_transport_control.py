"""Inspect and explicitly bound the transport of one exact Live Set."""

from __future__ import annotations

import hashlib
import json
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
OUTPUT_COUNT_FOR_TEST01 = 21
MIN_PLAY_SECONDS = 1
MAX_PLAY_SECONDS = 15
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
    return {
        "format": FORMAT,
        "status": "stopped",
        "phase": "preflight",
        "action": action,
        "read_only": action == "inspect",
        "target": {"pid": pid, "bundle_identifier": bundle_id},
        "set_url": set_path.as_uri(),
        "play_for_seconds": play_for_seconds,
        "actions": {}
        if action == "inspect"
        else {
            "settings_close": {"attempted": False, "outcome": "not_attempted"},
            "play": {"attempted": False, "outcome": "not_attempted"},
            "stop": {"attempted": False, "outcome": "not_attempted"},
        },
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
    transport_state_needs_review = (
        action == "play"
        and isinstance(transport, dict)
        and any(
            transport.get(name) == 1
            for name in ("link", "arrangement_record", "session_record")
        )
    )
    if report["manual_inspection_required"] is not (
        unknown or play_attempted_without_verified_stop or transport_state_needs_review
    ):
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
    if action != "inspect":
        command.append("--execute")
    if play_for_seconds is not None:
        command.extend(("--play-for-seconds", str(play_for_seconds)))
    if expected_count is not None:
        command.extend(("--expected-count", str(expected_count)))
    if baseline_path is not None and baseline_hash is not None:
        command.extend(
            ("--baseline", str(baseline_path), "--baseline-sha256", baseline_hash)
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
) -> dict:
    """Inspect by default; explicitly play briefly or stop one exact Set."""
    if platform.system() != "Darwin":
        raise ValueError("native Live transport control requires macOS")
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("PID must be a positive 32-bit integer")
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id.strip():
        raise ValueError("expected bundle identifier is required")
    source_set = _set_file(set_path)
    if not execute:
        if (
            action is not None
            or play_for_seconds is not None
            or expected_count is not None
            or receipt_path is not None
        ):
            raise ValueError("action options and --receipt require explicit --execute")
        selected_action = "inspect"
    else:
        if action not in {"play", "stop"}:
            raise ValueError("--execute requires --action play or stop")
        if receipt_path is None:
            raise ValueError("--execute requires a new --receipt path")
        if action == "play":
            if (
                type(play_for_seconds) is not int
                or not MIN_PLAY_SECONDS <= play_for_seconds <= MAX_PLAY_SECONDS
            ):
                raise ValueError(
                    "play duration must be an integer from 1 to 15 seconds"
                )
            if (
                type(expected_count) is not int
                or expected_count != OUTPUT_COUNT_FOR_TEST01
            ):
                raise ValueError(
                    "Play requires the independently supplied expected output count 21"
                )
        elif play_for_seconds is not None or expected_count is not None:
            raise ValueError(
                "--play-for-seconds and --expected-count are valid only for play"
            )
        selected_action = action

    initial = _initial_receipt(
        selected_action, pid, expected_bundle_id, source_set, play_for_seconds
    )
    identity = None
    if execute:
        receipt_path = _safe_new_path(receipt_path, "receipt")
        identity = _write_new_json(receipt_path, initial)

    def save(report: dict) -> dict:
        nonlocal identity
        if receipt_path is not None and identity is not None:
            identity = _replace_owned_json(receipt_path, identity, report)
        return report

    if selected_action != "inspect" and receipt_path is None:
        raise ValueError("mutating action requires a receipt path")
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
                return save(initial) if execute else initial
            if compilation.returncode:
                initial["phase"] = "helper-compile"
                initial["error"] = (
                    compilation.stderr.strip()[-2000:]
                    or "native helper compilation failed"
                )
                return save(initial) if execute else initial

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
                        expected_count=OUTPUT_COUNT_FOR_TEST01,
                    )
                    if not all(row.get("value") == 0 for row in baseline["outputs"]):
                        raise ValueError(
                            "all 21 output Sync controls must be off before Play"
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
                if selected_action == "play":
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
                uncertain["manual_inspection_required"] = True
                return save(uncertain) if execute else uncertain
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError:
                report = None
            if not _valid_report(
                report,
                action=selected_action,
                pid=pid,
                bundle_id=expected_bundle_id,
                set_path=source_set,
                play_for_seconds=play_for_seconds,
                returncode=result.returncode,
            ):
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
                uncertain["manual_inspection_required"] = selected_action != "inspect"
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
                return save(uncertain)
            return save(report) if execute else report
    except OSError as error:
        initial["phase"] = "native-helper-launch"
        initial["error"] = f"native helper launch failed: {error}"
        return save(initial) if execute else initial
