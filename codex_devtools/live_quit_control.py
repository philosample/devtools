"""Guarded normal termination of one explicitly identified Live process."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path

FORMAT = "codex-devtools-live-quit-v1"
LIVE_BUNDLE_ID = "com.ableton.live"
DEADLINE_SECONDS = 25.0
NANOSECONDS_PER_SECOND = 1_000_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GENERATION = re.compile(r"^[1-9][0-9]{5,24}$")
_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")


def _normalized_absolute(path: Path, *, suffix: str) -> Path:
    supplied = Path(os.path.expanduser(os.fspath(path)))
    raw = str(supplied)
    if (
        not supplied.is_absolute()
        or supplied.suffix.lower() != suffix
        or raw != os.path.normpath(raw)
        or "\\" in raw
        or any(part in (".", "..") for part in supplied.parts)
    ):
        raise ValueError(f"an exact normalized absolute {suffix} path is required")
    return supplied


def _unknown_receipt(
    *,
    pid: int,
    bundle_id: str,
    app: Path,
    generation: str,
    source: Path,
    execute: bool,
    phase: str,
    error: str,
    expected_pin: dict[str, str],
) -> dict:
    return {
        "format": FORMAT,
        "status": "stopped",
        "phase": phase,
        "read_only": not execute,
        "target": {
            "pid": pid,
            "bundle_identifier": bundle_id,
            "app_path": str(app),
            "executable_path": str(app / "Contents/MacOS/Live"),
            "process_generation": generation,
        },
        "source_set_url": source.as_uri(),
        "expected_set_pin": expected_pin,
        "source_file": None,
        "quit": {
            "requested": None if execute else False,
            "accepted": None,
            "terminated": False,
            "outcome": "unknown" if execute else "not_attempted",
            "force_quit": False,
        },
        "manual_inspection_required": execute,
        "error": error,
        "evidence_limit": "disk Set pin and AX transport state do not prove unsaved in-memory content matches disk",
    }


def _not_attempted_deadline_receipt(
    *,
    pid: int,
    bundle_id: str,
    app: Path,
    generation: str,
    source: Path,
    expected_pin: dict[str, str],
    execute: bool,
    phase: str = "admission-deadline",
    error: str = "the original monotonic deadline expired before native preflight",
) -> dict:
    report = _unknown_receipt(
        pid=pid,
        bundle_id=bundle_id,
        app=app,
        generation=generation,
        source=source,
        execute=execute,
        phase=phase,
        error=error,
        expected_pin=expected_pin,
    )
    report["quit"].update(requested=False, outcome="not_attempted")
    report["manual_inspection_required"] = False
    return report


def _valid_report(
    value: object,
    *,
    pid: int,
    bundle_id: str,
    app: Path,
    generation: str,
    source: Path,
    expected_pin: dict[str, str],
    execute: bool,
    returncode: int,
) -> bool:
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        return False
    status = value.get("status")
    if not isinstance(status, str):
        return False
    quit_state = value.get("quit")
    if not isinstance(quit_state, dict) or quit_state.get("force_quit") is not False:
        return False
    if value.get("read_only") is not (not execute):
        return False
    target = value.get("target")
    if (
        not isinstance(target, dict)
        or target.get("pid") != pid
        or value.get("source_set_url") != source.as_uri()
    ):
        return False
    source_file = value.get("source_file")
    source_pin_matches = (
        isinstance(source_file, dict)
        and source_file.get("path") == str(source)
        and source_file.get("sha256") == expected_pin["sha256"]
        and source_file.get("device") == expected_pin["device"]
        and source_file.get("inode") == expected_pin["inode"]
        and type(source_file.get("bytes")) is int
        and source_file["bytes"] > 0
    )
    exact_target = (
        target.get("bundle_identifier") == bundle_id
        and target.get("app_path") == str(app)
        and target.get("executable_path") == str(app / "Contents/MacOS/Live")
        and target.get("process_generation") == generation
        and source_pin_matches
    )
    if status in {"ready", "terminated"} and not exact_target:
        return False
    if execute:
        if status == "ready":
            return False
        if status == "terminated":
            return (
                returncode == 0
                and quit_state.get("outcome") == "terminated"
                and quit_state.get("requested") is True
                and type(quit_state.get("accepted")) is bool
                and quit_state.get("terminated") is True
                and value.get("manual_inspection_required") is False
            )
        pre_request_stop = (
            quit_state.get("requested") is False
            and quit_state.get("accepted") is None
            and quit_state.get("terminated") is False
            and quit_state.get("outcome") == "not_attempted"
            and value.get("manual_inspection_required") is False
        )
        post_request_stop = (
            quit_state.get("requested") is True
            and type(quit_state.get("accepted")) is bool
            and quit_state.get("terminated") is False
            and quit_state.get("outcome") == "unknown"
            and value.get("manual_inspection_required") is True
        )
        return (
            status == "stopped"
            and returncode == 1
            and (pre_request_stop or post_request_stop)
        )
    return (
        value.get("status") in {"ready", "stopped"}
        and returncode == (0 if value.get("status") == "ready" else 1)
        and quit_state.get("requested") is False
        and quit_state.get("accepted") is None
        and quit_state.get("terminated") is False
        and quit_state.get("outcome") == "not_attempted"
    )


def run_live_quit(
    pid: int,
    *,
    expected_bundle_id: str,
    app_bundle: Path,
    expected_generation: str,
    source_set: Path,
    source_sha256: str,
    source_device: str,
    source_inode: str,
    execute: bool = False,
) -> dict:
    """Inspect by default; issue exactly one normal quit only with ``execute``."""
    deadline_ns = time.monotonic_ns() + int(DEADLINE_SECONDS * NANOSECONDS_PER_SECOND)
    if platform.system() != "Darwin":
        raise ValueError("native Live normal quit requires macOS")
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("PID must be a positive 32-bit integer")
    if expected_bundle_id != LIVE_BUNDLE_ID:
        raise ValueError("expected bundle identifier must be com.ableton.live")
    if not isinstance(expected_generation, str) or not _GENERATION.fullmatch(
        expected_generation
    ):
        raise ValueError(
            "expected process generation must be a prior Live inspection token"
        )
    if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
        raise ValueError(
            "source Set SHA-256 must be 64 lowercase hexadecimal characters"
        )
    if not isinstance(source_device, str) or not _INTEGER.fullmatch(source_device):
        raise ValueError("source device must be the decimal device pin from inspection")
    if not isinstance(source_inode, str) or not _INTEGER.fullmatch(source_inode):
        raise ValueError("source inode must be the decimal inode pin from inspection")

    app = _normalized_absolute(app_bundle, suffix=".app")
    source = _normalized_absolute(source_set, suffix=".als")
    expected_pin = {
        "path": str(source),
        "sha256": source_sha256,
        "device": source_device,
        "inode": source_inode,
    }
    remaining_ns = deadline_ns - time.monotonic_ns()
    if remaining_ns <= 0:
        return _not_attempted_deadline_receipt(
            pid=pid,
            bundle_id=expected_bundle_id,
            app=app,
            generation=expected_generation,
            source=source,
            expected_pin=expected_pin,
            execute=execute,
        )
    script = Path(__file__).with_name("live_quit.swift")
    command = [
        "/usr/bin/swift",
        str(script),
        "--pid",
        str(pid),
        "--bundle-id",
        expected_bundle_id,
        "--app",
        str(app),
        "--generation",
        expected_generation,
        "--set",
        str(source),
        "--sha256",
        source_sha256,
        "--device",
        source_device,
        "--inode",
        source_inode,
        "--deadline-monotonic-ns",
        str(deadline_ns),
    ]
    if execute:
        command.append("--execute")
    remaining_ns = deadline_ns - time.monotonic_ns()
    if remaining_ns <= 0:
        return _not_attempted_deadline_receipt(
            pid=pid,
            bundle_id=expected_bundle_id,
            app=app,
            generation=expected_generation,
            source=source,
            expected_pin=expected_pin,
            execute=execute,
        )
    timeout_seconds = remaining_ns / NANOSECONDS_PER_SECOND
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return _unknown_receipt(
            pid=pid,
            bundle_id=expected_bundle_id,
            app=app,
            generation=expected_generation,
            source=source,
            expected_pin=expected_pin,
            execute=execute,
            phase="outer-timeout",
            error="native helper exceeded its outer bound; quit outcome is unknown",
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"native Live quit helper failed: {error}") from error
    if time.monotonic_ns() >= deadline_ns:
        if execute:
            return _unknown_receipt(
                pid=pid,
                bundle_id=expected_bundle_id,
                app=app,
                generation=expected_generation,
                source=source,
                expected_pin=expected_pin,
                execute=True,
                phase="outer-deadline",
                error="the original monotonic deadline expired before receipt delivery",
            )
        return _not_attempted_deadline_receipt(
            pid=pid,
            bundle_id=expected_bundle_id,
            app=app,
            generation=expected_generation,
            source=source,
            expected_pin=expected_pin,
            execute=False,
            phase="outer-deadline",
            error="the original monotonic deadline expired before receipt delivery",
        )
    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        message = (
            result.stderr.strip()[-2000:] if isinstance(result.stderr, str) else ""
        )
        if execute:
            return _unknown_receipt(
                pid=pid,
                bundle_id=expected_bundle_id,
                app=app,
                generation=expected_generation,
                source=source,
                expected_pin=expected_pin,
                execute=True,
                phase="native-invalid-receipt",
                error=message
                or "native helper returned invalid JSON; quit outcome is unknown",
            )
        raise ValueError(
            message or "native Live quit helper returned invalid JSON"
        ) from error
    try:
        valid_report = _valid_report(
            report,
            pid=pid,
            bundle_id=expected_bundle_id,
            app=app,
            generation=expected_generation,
            source=source,
            expected_pin=expected_pin,
            execute=execute,
            returncode=result.returncode,
        )
    except (KeyError, TypeError, ValueError):
        valid_report = False
    if not valid_report:
        if execute:
            return _unknown_receipt(
                pid=pid,
                bundle_id=expected_bundle_id,
                app=app,
                generation=expected_generation,
                source=source,
                expected_pin=expected_pin,
                execute=True,
                phase="native-invalid-receipt",
                error="native helper receipt did not prove the quit outcome; inspect Live before continuing",
            )
        raise ValueError(
            "native Live quit helper returned an invalid or inconsistent receipt"
        )
    if time.monotonic_ns() >= deadline_ns:
        if execute:
            return _unknown_receipt(
                pid=pid,
                bundle_id=expected_bundle_id,
                app=app,
                generation=expected_generation,
                source=source,
                expected_pin=expected_pin,
                execute=True,
                phase="outer-deadline",
                error="the original monotonic deadline expired before receipt validation completed",
            )
        return _not_attempted_deadline_receipt(
            pid=pid,
            bundle_id=expected_bundle_id,
            app=app,
            generation=expected_generation,
            source=source,
            expected_pin=expected_pin,
            execute=False,
            phase="outer-deadline",
            error="the original monotonic deadline expired before receipt validation completed",
        )
    return report
