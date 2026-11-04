"""Guarded normal termination of one explicitly identified Live process."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
from pathlib import Path

FORMAT = "codex-devtools-live-quit-v1"
LIVE_BUNDLE_ID = "com.ableton.live"
MAX_SET_BYTES = 128 * 1024 * 1024
NATIVE_TIMEOUT_SECONDS = 35
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


def _real_directory_chain(path: Path) -> None:
    for component in (Path("/"), *path.parents[::-1], path):
        try:
            info = component.lstat()
        except OSError as error:
            raise ValueError(f"path component is unavailable: {component}") from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(
                "path components must be real directories, without symlinks"
            )


def _stable_source_pin(
    path: Path, expected_sha256: str, expected_device: str, expected_inode: str
) -> dict[str, str | int]:
    path = _normalized_absolute(path, suffix=".als")
    _real_directory_chain(path.parent)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("source Set must be a regular file, not a symlink")
        if before.st_size <= 0 or before.st_size > MAX_SET_BYTES:
            raise ValueError("source Set must contain 1 byte to 128 MiB")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ValueError(f"cannot open exact source Set: {error}") from error

    digest = hashlib.sha256()
    count = 0
    try:
        opened_before = os.fstat(fd)
        if not stat.S_ISREG(opened_before.st_mode):
            raise ValueError("opened source Set is not a regular file")
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            digest.update(chunk)
            count += len(chunk)
            if count > MAX_SET_BYTES:
                raise ValueError("source Set exceeds 128 MiB")
        opened_after = os.fstat(fd)
        current = path.lstat()
    finally:
        os.close(fd)

    def identity(item: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
            item.st_mode,
        )

    if identity(opened_before) != identity(opened_after) or identity(
        opened_before
    ) != identity(current):
        raise ValueError("source Set changed while its stable pin was read")
    actual_hash = digest.hexdigest()
    if actual_hash != expected_sha256:
        raise ValueError("source Set SHA-256 differs from the reviewed pin")
    if (
        str(opened_before.st_dev) != expected_device
        or str(opened_before.st_ino) != expected_inode
    ):
        raise ValueError("source Set device/inode differs from the reviewed pin")
    return {
        "path": str(path),
        "sha256": actual_hash,
        "bytes": count,
        "device": str(opened_before.st_dev),
        "inode": str(opened_before.st_ino),
    }


def _validate_app(app_bundle: Path) -> Path:
    app = _normalized_absolute(app_bundle, suffix=".app")
    try:
        info = app.lstat()
    except OSError as error:
        raise ValueError(f"Live.app bundle is unavailable: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("Live.app must be an exact real .app directory")
    if app.resolve(strict=True) != app:
        raise ValueError("Live.app path components must not be symbolic links")
    return app


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
    source_pin: dict[str, str | int],
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
        "source_file": source_pin,
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


def _valid_report(
    value: object,
    *,
    pid: int,
    bundle_id: str,
    app: Path,
    generation: str,
    source: Path,
    source_pin: dict[str, str | int],
    execute: bool,
    returncode: int,
) -> bool:
    if not isinstance(value, dict) or value.get("format") != FORMAT:
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
    exact_target = (
        target.get("bundle_identifier") == bundle_id
        and target.get("app_path") == str(app)
        and target.get("executable_path") == str(app / "Contents/MacOS/Live")
        and target.get("process_generation") == generation
        and value.get("source_file") == source_pin
    )
    if value.get("status") in {"ready", "terminated"} and not exact_target:
        return False
    if execute:
        status = value.get("status")
        if status == "ready":
            return (
                returncode == 0
                and quit_state.get("outcome") == "not_attempted"
                and quit_state.get("requested") is False
                and quit_state.get("accepted") is None
                and quit_state.get("terminated") is False
                and value.get("manual_inspection_required") is False
            )
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

    app = _validate_app(app_bundle)
    source = _normalized_absolute(source_set, suffix=".als")
    source_pin = _stable_source_pin(source, source_sha256, source_device, source_inode)
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
        str(source_pin["sha256"]),
        "--device",
        str(source_pin["device"]),
        "--inode",
        str(source_pin["inode"]),
    ]
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
            app=app,
            generation=expected_generation,
            source=source,
            source_pin=source_pin,
            execute=execute,
            phase="outer-timeout",
            error="native helper exceeded its outer bound; quit outcome is unknown",
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"native Live quit helper failed: {error}") from error
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        message = result.stderr.strip()[-2000:]
        if execute:
            return _unknown_receipt(
                pid=pid,
                bundle_id=expected_bundle_id,
                app=app,
                generation=expected_generation,
                source=source,
                source_pin=source_pin,
                execute=True,
                phase="native-invalid-receipt",
                error=message
                or "native helper returned invalid JSON; quit outcome is unknown",
            )
        raise ValueError(
            message or "native Live quit helper returned invalid JSON"
        ) from error
    if not _valid_report(
        report,
        pid=pid,
        bundle_id=expected_bundle_id,
        app=app,
        generation=expected_generation,
        source=source,
        source_pin=source_pin,
        execute=execute,
        returncode=result.returncode,
    ):
        if execute:
            return _unknown_receipt(
                pid=pid,
                bundle_id=expected_bundle_id,
                app=app,
                generation=expected_generation,
                source=source,
                source_pin=source_pin,
                execute=True,
                phase="native-invalid-receipt",
                error="native helper receipt did not prove the quit outcome; inspect Live before continuing",
            )
        raise ValueError(
            "native Live quit helper returned an invalid or inconsistent receipt"
        )
    return report
