"""Explicit, pinned same-process Set open using the shared native lifecycle helper."""

from __future__ import annotations

import json
import os
import platform
import re
import stat
import subprocess
import uuid
from pathlib import Path

from .live_set_lifecycle import _directory_chain, _set_path
from .maxfiles import sha256

FORMAT = "codex-devtools-live-set-open-v1"
MAX_SET_BYTES = 128 * 1024 * 1024
ACTION_NAMES = ("focus", "menu_navigation", "save_as_menu", "save", "quit", "open")


def _read_set(path: Path, expected: str) -> tuple[bytes, dict]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_SET_BYTES:
            raise ValueError("Set must be a bounded nonempty regular file")
        data = stream.read(MAX_SET_BYTES + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()

    def identity(item):
        return (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )

    digest = sha256(data)
    if (
        identity(before) != identity(after)
        or identity(after) != identity(current)
        or len(data) != before.st_size
        or digest != expected
    ):
        raise ValueError("reviewed Set hash/identity changed while reading")
    mtime, mtime_ns = divmod(before.st_mtime_ns, 1_000_000_000)
    ctime, ctime_ns = divmod(before.st_ctime_ns, 1_000_000_000)
    return data, {
        "path": str(path),
        "sha256": digest,
        "bytes": len(data),
        "device": str(before.st_dev),
        "inode": str(before.st_ino),
        "mtime_seconds": mtime,
        "mtime_nanoseconds": mtime_ns,
        "ctime_seconds": ctime,
        "ctime_nanoseconds": ctime_ns,
    }


def _private_root(root: Path) -> None:
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise ValueError("canonical existing private backup root required")
    _directory_chain(root, outside_git=True)
    info = root.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("backup root must be private and owned")


def _exclusive(path: Path, data: bytes) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _backup(
    root: Path,
    source_data: bytes,
    source: dict,
    destination: Path,
    destination_sha256: str,
    pid: int,
    bundle: str,
) -> dict:
    _private_root(root)
    directory = root / str(uuid.uuid4())
    directory.mkdir(mode=0o700)
    path = directory / "source-before-save.als"
    _exclusive(path, source_data)
    _, pin = _read_set(path, source["sha256"])
    receipt = {
        "format": "codex-devtools-live-set-backup-v1",
        "pid": pid,
        "bundle_identifier": bundle,
        "source": source,
        "backup": pin,
        "destination_set_url": destination.as_uri(),
        "destination_sha256": destination_sha256,
        "evidence_limit": "verified pre-save file bytes only; no native Save claimed",
    }
    receipt_path = directory / "receipt.json"
    encoded = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    _exclusive(receipt_path, encoded)
    if receipt_path.read_bytes() != encoded:
        raise ValueError("source backup receipt readback mismatch")
    return {"receipt_path": str(receipt_path), **receipt}


def run_set_open(
    pid: int,
    *,
    expected_bundle_id: str,
    source_set: Path,
    destination_set: Path,
    source_sha256: str,
    destination_sha256: str,
    app_bundle: Path,
    save_owned_source: bool = False,
    backup_root: Path | None = None,
    execute: bool = False,
) -> dict:
    """Preflight is read-only. Explicit execute opens once and never answers prompts."""
    if platform.system() != "Darwin":
        raise ValueError("native same-process Set open requires macOS")
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("positive 32-bit Live PID required")
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id.strip():
        raise ValueError("expected bundle identifier required")
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (source_sha256, destination_sha256)
    ):
        raise ValueError("reviewed source and destination SHA-256 required")
    if type(save_owned_source) is not bool or type(execute) is not bool:
        raise TypeError("explicit boolean operation flags required")
    source = _set_path(source_set, must_exist=True)
    destination = _set_path(destination_set, must_exist=True)
    for path in (source, destination):
        _directory_chain(path.parent, outside_git=True)
    source_data, source_pin = _read_set(source, source_sha256)
    _, destination_pin = _read_set(destination, destination_sha256)
    if save_owned_source and source.lstat().st_uid != os.getuid():
        raise ValueError("explicitly saved source must be owned by the current user")
    if source == destination or (source_pin["device"], source_pin["inode"]) == (
        destination_pin["device"],
        destination_pin["inode"],
    ):
        raise ValueError("source and destination must not alias")
    app = app_bundle
    if (
        not app.is_absolute()
        or app.suffix != ".app"
        or app.resolve(strict=True) != app
        or not app.is_dir()
    ):
        raise ValueError("exact canonical Live.app directory required")
    if save_owned_source != (backup_root is not None):
        raise ValueError(
            "--save-owned-source requires --backup-root; neither is implicit"
        )
    if backup_root is not None:
        _private_root(backup_root)
    backup = None
    if execute and save_owned_source:
        backup = _backup(
            backup_root,
            source_data,
            source_pin,
            destination,
            destination_sha256,
            pid,
            expected_bundle_id,
        )
    command = [
        "/usr/bin/swift",
        str(Path(__file__).with_name("live_set_lifecycle.swift")),
        "--open-existing",
        "--pid",
        str(pid),
        "--bundle-id",
        expected_bundle_id,
        "--source-set",
        str(source),
        "--destination-set",
        str(destination),
        "--source-sha256",
        source_sha256,
        "--destination-sha256",
        destination_sha256,
        "--app",
        str(app),
    ]
    if save_owned_source:
        command.append("--save-owned-source")
    if backup:
        command.extend(("--backup-receipt", backup["receipt_path"]))
    if execute:
        command.append("--execute")

    def unknown(phase: str, reason: str) -> dict:
        actions = dict.fromkeys(ACTION_NAMES, "not_attempted")
        if execute:
            actions.update(focus="outcome_unknown", open="outcome_unknown")
            if save_owned_source:
                actions.update(
                    menu_navigation="outcome_unknown", save="outcome_unknown"
                )
        return {
            "format": FORMAT,
            "status": "stopped",
            "phase": phase,
            "error": reason,
            "target": {"pid": pid, "bundle_identifier": expected_bundle_id},
            "source_set_url": source.as_uri(),
            "destination_set_url": destination.as_uri(),
            "read_only": not execute,
            "actions": actions,
            "manual_inspection_required": execute,
            "source_backup": backup,
        }

    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=240
        )
    except subprocess.TimeoutExpired:
        return unknown(
            "outer-timeout",
            "Set open outcome unknown after outer deadline; inspect, never retry",
        )
    except (OSError, subprocess.SubprocessError) as error:
        if execute:
            return unknown("native-call", str(error))
        raise ValueError(f"native Set preflight unavailable: {error}") from error
    try:
        if len(result.stdout.encode()) > 1024 * 1024:
            raise ValueError("native Set-open receipt exceeds bounds")
        report = json.loads(result.stdout)
        if (
            not isinstance(report, dict)
            or report.get("format") != FORMAT
            or report.get("target", {}).get("pid") != pid
            or report.get("target", {}).get("bundle_identifier") != expected_bundle_id
        ):
            raise ValueError("native Set-open receipt schema/target mismatch")
        actions = report.get("actions")
        allowed = {
            "focus": {"not_attempted", "outcome_unknown", "verified"},
            "menu_navigation": {"not_attempted", "outcome_unknown", "verified"},
            "save_as_menu": {"not_attempted"},
            "save": {"not_attempted", "outcome_unknown", "source_observed"},
            "quit": {"not_attempted"},
            "open": {"not_attempted", "outcome_unknown", "exact_set_window_observed"},
        }
        if (
            not isinstance(actions, dict)
            or set(actions) != set(ACTION_NAMES)
            or any(value not in allowed[key] for key, value in actions.items())
            or actions["save_as_menu"] != "not_attempted"
            or actions["quit"] != "not_attempted"
            or (not save_owned_source and actions["save"] != "not_attempted")
        ):
            raise ValueError("invalid or unapproved Set-open action receipt")
        if report.get("read_only") is not (not execute):
            raise ValueError("Set-open read-only mode mismatch")
        if report.get("status") == "stopped":
            attempted = any(value != "not_attempted" for value in actions.values())
            if (
                result.returncode != 1
                or actions["open"] == "exact_set_window_observed"
                or not isinstance(report.get("phase"), str)
                or not isinstance(report.get("error"), str)
                or report.get("manual_inspection_required") is not attempted
            ):
                raise ValueError("invalid stopped Set-open receipt")
            if not execute and attempted:
                raise ValueError("read-only Set preflight reports actions")
            report["source_backup"] = backup
            return report
        if (
            result.returncode != 0
            or report.get("status") != ("opened" if execute else "ready")
            or report.get("source_set_url") != source.as_uri()
            or report.get("destination_set_url") != destination.as_uri()
            or report.get("save_owned_source") is not save_owned_source
        ):
            raise ValueError(
                "Set-open completion, selected URL or save permission mismatch"
            )
        expected_actions = dict.fromkeys(ACTION_NAMES, "not_attempted")
        if execute:
            expected_actions.update(focus="verified", open="exact_set_window_observed")
            if save_owned_source:
                expected_actions.update(
                    menu_navigation="verified", save="source_observed"
                )
        if actions != expected_actions or (
            execute and report.get("same_process") is not True
        ):
            raise ValueError("Set-open action completion or same-process proof missing")
        destination_after = _read_set(destination, destination_sha256)[1]
        if report.get("destination_file") != destination_pin:
            raise ValueError("destination pre-open approval pin differs")
        if execute:

            def without_ctime(pin):
                return {
                    key: value
                    for key, value in pin.items()
                    if key not in ("ctime_seconds", "ctime_nanoseconds")
                }

            delta = [] if destination_after == destination_pin else ["ctime"]
            if (
                report.get("destination_file_after_open") != destination_after
                or without_ctime(destination_after) != without_ctime(destination_pin)
                or report.get("destination_metadata_delta") != delta
            ):
                raise ValueError(
                    "destination post-open pin or ctime-only delta differs"
                )
        elif destination_after != destination_pin:
            raise ValueError("destination bytes/identity changed")
        reported_source = report.get("source_file")
        if (
            not isinstance(reported_source, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(reported_source.get("sha256")))
            or _read_set(source, reported_source["sha256"])[1] != reported_source
        ):
            raise ValueError("source bytes/identity differ from native observation")
        if (not execute or not save_owned_source) and reported_source != source_pin:
            raise ValueError("source changed without explicit Save authority")
        if execute and save_owned_source:
            if (
                reported_source == source_pin
                or report.get("backup_receipt") != backup["receipt_path"]
                or report.get("backup_file") != backup["backup"]["path"]
            ):
                raise ValueError("source Save transition or backup proof missing")
            if (
                _read_set(Path(backup["backup"]["path"]), source_sha256)[1]
                != backup["backup"]
            ):
                raise ValueError("source backup identity changed after creation")
        report["source_backup"] = backup
        return report
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        if execute:
            return unknown(
                "native-receipt",
                f"Set-open completion unverified: {error}; inspect, never retry",
            )
        raise ValueError(f"native Set preflight receipt invalid: {error}") from error
