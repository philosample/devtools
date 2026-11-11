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
MAX_OPERATION_RECEIPT_BYTES = 64 * 1024
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


def _read_reviewed_receipt(path: Path, expected_sha256: str) -> tuple[bytes, dict]:
    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        or not path.is_absolute()
        or path.resolve(strict=True) != path
    ):
        raise ValueError("canonical receipt path and reviewed SHA-256 required")
    _directory_chain(path.parent, outside_git=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o077
        ):
            raise ValueError("reviewed receipt must be a current-user regular file")
        if not 0 < before.st_size <= MAX_OPERATION_RECEIPT_BYTES:
            raise ValueError("reviewed receipt exceeds the bounded size")
        data = stream.read(MAX_OPERATION_RECEIPT_BYTES + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if (
        identity(before) != identity(after)
        or identity(after) != identity(current)
        or len(data) != before.st_size
        or sha256(data) != expected_sha256
    ):
        raise ValueError("reviewed receipt changed or SHA-256 mismatch")
    try:
        receipt = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("reviewed receipt is not valid JSON") from error
    if not isinstance(receipt, dict):
        raise TypeError("reviewed receipt must be a JSON object")
    return data, receipt


def _json_value_matches(actual, expected) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _json_value_matches(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _json_value_matches(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _validate_original_unknown_open(receipt: dict, *, pid: int, bundle: str) -> None:
    actions = receipt.get("actions")
    target = receipt.get("target")
    expected_keys = {
        "actions",
        "backup_receipt",
        "error",
        "format",
        "manual_inspection_required",
        "phase",
        "read_only",
        "source_backup",
        "status",
        "target",
    }
    if (
        set(receipt) != expected_keys
        or receipt.get("format") != FORMAT
        or receipt.get("status") != "stopped"
        or receipt.get("phase") != "verify-opened-existing-set"
        or receipt.get("read_only") is not False
        or receipt.get("manual_inspection_required") is not True
        or not isinstance(receipt.get("error"), str)
        or not receipt["error"].strip()
        or receipt.get("backup_receipt") is not None
        or receipt.get("source_backup") is not None
        or not isinstance(target, dict)
        or set(target) != {"pid", "bundle_identifier"}
        or type(target.get("pid")) is not int
        or target.get("pid") != pid
        or target.get("bundle_identifier") != bundle
        or not isinstance(actions, dict)
        or set(actions) != set(ACTION_NAMES)
        or actions.get("focus") != "verified"
        or actions.get("menu_navigation") != "not_attempted"
        or actions.get("open") != "outcome_unknown"
        or actions.get("save") != "not_attempted"
        or actions.get("save_as_menu") != "not_attempted"
        or actions.get("quit") != "not_attempted"
    ):
        raise ValueError(
            "receipt does not bind the exact original unknown Open attempt"
        )


def _validate_cancel_preflight(
    receipt: dict,
    *,
    pid: int,
    bundle: str,
    generation: int,
    source: Path,
    destination: Path,
    source_pin: dict,
    destination_pin: dict,
    original_receipt_sha256: str,
    attempt_marker: Path,
) -> None:
    target = receipt.get("target")
    menu = receipt.get("file_menu")
    actions = receipt.get("actions")
    expected_keys = {
        "actions",
        "attempt_marker",
        "blocking_windows",
        "destination_file",
        "destination_set_url",
        "file_menu",
        "focused_window_matches_source",
        "format",
        "open_initiated_by_helper",
        "original_open_receipt_sha256",
        "pending_destination_binding_pre_action",
        "phase",
        "read_only",
        "selected_menu_count",
        "source_file",
        "source_set_url",
        "source_transport",
        "status",
        "target",
    }
    if (
        set(receipt) != expected_keys
        or receipt.get("format") != FORMAT
        or receipt.get("status") != "ready"
        or receipt.get("phase") != "file-menu-cancel-preflight"
        or receipt.get("read_only") is not True
        or not isinstance(target, dict)
        or set(target) != {"pid", "bundle_identifier", "process_generation"}
        or type(target.get("pid")) is not int
        or type(target.get("process_generation")) is not int
        or target.get("pid") != pid
        or target.get("bundle_identifier") != bundle
        or target.get("process_generation") != generation
        or receipt.get("source_set_url") != source.as_uri()
        or receipt.get("destination_set_url") != destination.as_uri()
        or not _json_value_matches(receipt.get("source_file"), source_pin)
        or not _json_value_matches(receipt.get("destination_file"), destination_pin)
        or receipt.get("original_open_receipt_sha256") != original_receipt_sha256
        or receipt.get("pending_destination_binding_pre_action") != "unverified"
        or receipt.get("attempt_marker") != str(attempt_marker)
        or receipt.get("focused_window_matches_source") is not True
        or receipt.get("open_initiated_by_helper") is not False
        or type(receipt.get("selected_menu_count")) is not int
        or receipt.get("selected_menu_count") != 1
        or receipt.get("blocking_windows") != []
        or not _json_value_matches(
            receipt.get("source_transport"),
            {"play": 0, "link": 0, "arrangement_record": 0, "session_record": 0},
        )
        or not isinstance(menu, dict)
        or menu.get("role") != "AXMenuBarItem"
        or menu.get("title") != "File"
        or menu.get("selected") is not True
        or menu.get("enabled") is not True
        or menu.get("cancel_action") is not True
        or set(menu)
        != {"role", "title", "selected", "enabled", "actions", "cancel_action"}
        or menu.get("actions") != ["AXCancel"]
        or not isinstance(actions, dict)
        or set(actions) != {"focus", "file_menu_cancel", "open"}
        or actions.get("focus") != "verified"
        or actions.get("file_menu_cancel") != "not_attempted"
        or actions.get("open") != "outcome_unknown"
    ):
        raise ValueError(
            "reviewed preflight does not match the exact pending-Open cancellation contract"
        )


def _validate_attempt_marker(path: Path) -> None:
    if not path.is_absolute() or path.resolve(strict=False) != path:
        raise ValueError("canonical absolute one-use attempt marker path required")
    _directory_chain(path.parent, outside_git=True)
    directory = path.parent.lstat()
    if directory.st_uid != os.getuid() or stat.S_IMODE(directory.st_mode) & 0o077:
        raise ValueError("one-use attempt marker directory must be private and owned")
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise ValueError(
        "one-use attempt marker already exists; cancellation cannot be retried"
    )


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
    reconcile_pending_open: bool = False,
    expected_generation: int | None = None,
    cancel_selected_file_menu: bool = False,
    observe_original_pending_open: bool = False,
    original_open_receipt: Path | None = None,
    original_open_receipt_sha256: str | None = None,
    preflight_receipt: Path | None = None,
    preflight_receipt_sha256: str | None = None,
    attempt_marker: Path | None = None,
    execute: bool = False,
) -> dict:
    """Inspect one exact open flow; reconciliation only saves its reviewed dialog."""
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
    if (
        type(save_owned_source) is not bool
        or type(reconcile_pending_open) is not bool
        or type(cancel_selected_file_menu) is not bool
        or type(observe_original_pending_open) is not bool
        or type(execute) is not bool
    ):
        raise TypeError("explicit boolean operation flags required")
    cancel_inputs = (
        observe_original_pending_open,
        original_open_receipt,
        original_open_receipt_sha256,
        preflight_receipt,
        preflight_receipt_sha256,
        attempt_marker,
    )
    if cancel_selected_file_menu or any(
        value is not None and value is not False for value in cancel_inputs
    ):
        if not cancel_selected_file_menu or not observe_original_pending_open:
            raise ValueError(
                "File-menu cancellation requires explicit original-pending-Open observation"
            )
        if save_owned_source or backup_root is not None or reconcile_pending_open:
            raise ValueError(
                "File-menu cancellation cannot Save, use backup, or reconcile a Save dialog"
            )
        if (
            type(expected_generation) is not int
            or not 0 < expected_generation <= 9_223_372_036_854_775_807
            or original_open_receipt is None
            or original_open_receipt_sha256 is None
            or attempt_marker is None
            or (
                execute
                and (preflight_receipt is None or preflight_receipt_sha256 is None)
            )
            or (
                not execute
                and (
                    preflight_receipt is not None
                    or preflight_receipt_sha256 is not None
                )
            )
        ):
            raise ValueError(
                "File-menu cancel/observe requires exact generation, original receipt/hash, one-use marker, and execute-only reviewed preflight receipt/hash"
            )
        return _run_cancel_selected_file_menu(
            pid,
            expected_bundle_id=expected_bundle_id,
            source_set=source_set,
            destination_set=destination_set,
            source_sha256=source_sha256,
            destination_sha256=destination_sha256,
            app_bundle=app_bundle,
            expected_generation=expected_generation,
            original_open_receipt=original_open_receipt,
            original_open_receipt_sha256=original_open_receipt_sha256,
            preflight_receipt=preflight_receipt,
            preflight_receipt_sha256=preflight_receipt_sha256,
            attempt_marker=attempt_marker,
            execute=execute,
        )
    if reconcile_pending_open:
        if not save_owned_source or backup_root is None:
            raise ValueError(
                "--reconcile-pending-open requires --save-owned-source and --backup-root"
            )
        if (
            type(expected_generation) is not int
            or not 0 < expected_generation <= 9_223_372_036_854_775_807
        ):
            raise ValueError(
                "--reconcile-pending-open requires a positive inspected process generation"
            )
    elif expected_generation is not None:
        raise ValueError("--expected-generation requires --reconcile-pending-open")
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
    if reconcile_pending_open:
        command.extend(
            (
                "--reconcile-pending-open",
                "--expected-generation",
                str(expected_generation),
            )
        )
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
            or report.get("status")
            != (
                "reconciled"
                if reconcile_pending_open and execute
                else "ready"
                if not execute
                else "opened"
            )
            or report.get("source_set_url") != source.as_uri()
            or report.get("destination_set_url") != destination.as_uri()
            or report.get("save_owned_source") is not save_owned_source
        ):
            raise ValueError(
                "Set-open completion, selected URL or save permission mismatch"
            )
        if reconcile_pending_open and (
            report.get("reconcile_pending_open") is not True
            or report.get("expected_generation") != expected_generation
            or report.get("dialog_text")
            != f'Save changes to "{source.stem}" before closing?'
            or report.get("pending_destination_binding_pre_action") != "unverified"
            or report.get("open_initiated_by_helper") is not False
        ):
            raise ValueError("exact dialog reconciliation receipt fields differ")
        stopped_transport = {
            "play": 0,
            "link": 0,
            "arrangement_record": 0,
            "session_record": 0,
        }
        if (
            reconcile_pending_open
            and not execute
            and report.get("transport") != stopped_transport
        ):
            raise ValueError("dialog preflight did not prove stopped transport")
        expected_actions = dict.fromkeys(ACTION_NAMES, "not_attempted")
        if execute:
            expected_actions.update(focus="verified", open="exact_set_window_observed")
            if save_owned_source:
                expected_actions.update(save="source_observed")
                if not reconcile_pending_open:
                    expected_actions["menu_navigation"] = "verified"
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
        if (
            reconcile_pending_open
            and execute
            and (
                report.get("reconciled_pending_open") is not True
                or report.get("source_file_before_save") != source_pin
                or report.get("transport_before_save") != stopped_transport
                or report.get("transport_after_open") != stopped_transport
            )
        ):
            raise ValueError(
                "source preimage, stopped transport or pending-open proof missing"
            )
        report["source_backup"] = backup
        return report
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        if execute:
            return unknown(
                "native-receipt",
                f"Set-open completion unverified: {error}; inspect, never retry",
            )
        raise ValueError(f"native Set preflight receipt invalid: {error}") from error


def _run_cancel_selected_file_menu(
    pid: int,
    *,
    expected_bundle_id: str,
    source_set: Path,
    destination_set: Path,
    source_sha256: str,
    destination_sha256: str,
    app_bundle: Path,
    expected_generation: int,
    original_open_receipt: Path,
    original_open_receipt_sha256: str,
    preflight_receipt: Path | None,
    preflight_receipt_sha256: str | None,
    attempt_marker: Path,
    execute: bool,
) -> dict:
    if platform.system() != "Darwin":
        raise ValueError("native Live File-menu cancellation requires macOS")
    if (
        not isinstance(expected_bundle_id, str)
        or not expected_bundle_id.strip()
        or type(expected_generation) is not int
        or not 0 < expected_generation <= 9_223_372_036_854_775_807
    ):
        raise ValueError(
            "exact bundle identifier and inspected process generation required"
        )
    source = _set_path(source_set, must_exist=True)
    destination = _set_path(destination_set, must_exist=True)
    for path in (source, destination):
        _directory_chain(path.parent, outside_git=True)
    _, source_pin = _read_set(source, source_sha256)
    _, destination_pin = _read_set(destination, destination_sha256)
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
    _, original_receipt = _read_reviewed_receipt(
        original_open_receipt, original_open_receipt_sha256
    )
    _validate_original_unknown_open(
        original_receipt, pid=pid, bundle=expected_bundle_id
    )
    _validate_attempt_marker(attempt_marker)
    if execute:
        if preflight_receipt is None or preflight_receipt_sha256 is None:
            raise ValueError(
                "execute requires the exact reviewed cancellation preflight receipt"
            )
        _, preflight = _read_reviewed_receipt(
            preflight_receipt, preflight_receipt_sha256
        )
        _validate_cancel_preflight(
            preflight,
            pid=pid,
            bundle=expected_bundle_id,
            generation=expected_generation,
            source=source,
            destination=destination,
            source_pin=source_pin,
            destination_pin=destination_pin,
            original_receipt_sha256=original_open_receipt_sha256,
            attempt_marker=attempt_marker,
        )
    elif preflight_receipt is not None or preflight_receipt_sha256 is not None:
        raise ValueError("cancellation preflight receipt is accepted only for execute")

    command = [
        "/usr/bin/swift",
        str(Path(__file__).with_name("live_set_lifecycle.swift")),
        "--open-existing",
        "--cancel-selected-file-menu",
        "--observe-original-pending-open",
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
        "--expected-generation",
        str(expected_generation),
        "--original-open-receipt",
        str(original_open_receipt),
        "--original-open-receipt-sha256",
        original_open_receipt_sha256,
        "--attempt-marker",
        str(attempt_marker),
    ]
    if execute:
        command.extend(
            (
                "--preflight-receipt",
                str(preflight_receipt),
                "--preflight-receipt-sha256",
                str(preflight_receipt_sha256),
                "--execute",
            )
        )

    def unknown(phase: str, reason: str) -> dict:
        marker_exists = attempt_marker.exists()
        return {
            "format": FORMAT,
            "status": "stopped",
            "phase": phase,
            "error": reason,
            "read_only": not execute,
            "target": {
                "pid": pid,
                "bundle_identifier": expected_bundle_id,
                "process_generation": expected_generation,
            },
            "source_set_url": source.as_uri(),
            "destination_set_url": destination.as_uri(),
            "source_file": source_pin,
            "destination_file": destination_pin,
            "original_open_receipt_sha256": original_open_receipt_sha256,
            "pending_destination_binding_pre_action": "unverified",
            "open_initiated_by_helper": False,
            "attempt_marker": str(attempt_marker),
            "actions": {
                "focus": "outcome_unknown" if execute else "not_attempted",
                "file_menu_cancel": "outcome_unknown"
                if marker_exists
                else "not_attempted",
                "open": "outcome_unknown",
            },
            "manual_inspection_required": marker_exists,
        }

    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=150
        )
    except subprocess.TimeoutExpired:
        return unknown(
            "outer-timeout",
            "File-menu cancel/original Open outcome unknown; inspect and never retry",
        )
    except (OSError, subprocess.SubprocessError) as error:
        if execute:
            return unknown("native-call", str(error))
        raise ValueError(
            f"native File-menu cancel preflight unavailable: {error}"
        ) from error

    marker_exists = attempt_marker.exists()
    try:
        if len(result.stdout.encode()) > 1024 * 1024:
            raise ValueError("native cancellation receipt exceeds bounds")
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or report.get("format") != FORMAT:
            raise ValueError("native cancellation receipt schema mismatch")
        base_keys = {
            "format",
            "target",
            "source_set_url",
            "destination_set_url",
            "source_file",
            "destination_file",
            "original_open_receipt_sha256",
            "pending_destination_binding_pre_action",
            "open_initiated_by_helper",
            "attempt_marker",
        }
        status = report.get("status")
        expected_report_keys = (
            base_keys
            | {
                "status",
                "phase",
                "error",
                "read_only",
                "actions",
                "manual_inspection_required",
            }
            if status == "stopped"
            else base_keys
            | {
                "status",
                "phase",
                "read_only",
                "actions",
                "manual_inspection_required",
                "same_process",
                "source_file_after",
                "destination_file_before",
                "destination_file_after",
                "destination_metadata_delta",
                "transport_before",
                "transport_after",
                "stable_destination_observations",
                "file_menu_closed",
            }
            if execute
            else base_keys
            | {
                "status",
                "phase",
                "read_only",
                "source_transport",
                "focused_window_matches_source",
                "blocking_windows",
                "selected_menu_count",
                "file_menu",
                "actions",
            }
        )
        if set(report) != expected_report_keys:
            raise ValueError(
                "native cancellation receipt has an unknown or missing field"
            )
        target = report.get("target")
        actions = report.get("actions")
        if (
            not isinstance(target, dict)
            or set(target) != {"pid", "bundle_identifier", "process_generation"}
            or type(target.get("pid")) is not int
            or type(target.get("process_generation")) is not int
            or target.get("pid") != pid
            or target.get("bundle_identifier") != expected_bundle_id
            or target.get("process_generation") != expected_generation
            or not isinstance(actions, dict)
            or set(actions) != {"focus", "file_menu_cancel", "open"}
            or report.get("read_only") is not (not execute)
            or report.get("source_set_url") != source.as_uri()
            or report.get("destination_set_url") != destination.as_uri()
            or not _json_value_matches(report.get("source_file"), source_pin)
            or not _json_value_matches(report.get("destination_file"), destination_pin)
            or report.get("original_open_receipt_sha256")
            != original_open_receipt_sha256
            or report.get("pending_destination_binding_pre_action") != "unverified"
            or report.get("open_initiated_by_helper") is not False
            or report.get("attempt_marker") != str(attempt_marker)
        ):
            raise ValueError("native cancellation receipt target/pin mismatch")
        if report.get("status") == "stopped":
            if (
                result.returncode != 1
                or actions["open"] != "outcome_unknown"
                or actions["focus"] not in {"verified", "not_attempted"}
                or actions["file_menu_cancel"]
                != ("outcome_unknown" if marker_exists else "not_attempted")
                or report.get("manual_inspection_required") is not marker_exists
                or report.get("phase")
                not in {
                    "file-menu-cancel-preflight",
                    "file-menu-cancel-review",
                    "cancel-selected-file-menu",
                    "observe-original-pending-open",
                }
                or not isinstance(report.get("error"), str)
                or not report["error"].strip()
                or len(report["error"]) > 2048
            ):
                raise ValueError("invalid stopped File-menu cancellation receipt")
            return report
        if not execute:
            if (
                result.returncode != 0
                or report.get("status") != "ready"
                or report.get("phase") != "file-menu-cancel-preflight"
                or actions
                != {
                    "focus": "verified",
                    "file_menu_cancel": "not_attempted",
                    "open": "outcome_unknown",
                }
                or marker_exists
            ):
                raise ValueError(
                    "read-only preflight claims an action or has the wrong state"
                )
            _validate_cancel_preflight(
                report,
                pid=pid,
                bundle=expected_bundle_id,
                generation=expected_generation,
                source=source,
                destination=destination,
                source_pin=source_pin,
                destination_pin=destination_pin,
                original_receipt_sha256=original_open_receipt_sha256,
                attempt_marker=attempt_marker,
            )
            return report
        expected_actions = {
            "focus": "verified",
            "file_menu_cancel": "AXCancel_succeeded",
            "open": "exact_destination_observed",
        }
        if (
            result.returncode != 0
            or report.get("status") != "original_open_observed"
            or report.get("phase") != "observe-original-pending-open"
            or actions != expected_actions
            or report.get("same_process") is not True
            or not _json_value_matches(report.get("source_file_after"), source_pin)
            or not isinstance(report.get("destination_file_after"), dict)
            or not _json_value_matches(
                report.get("transport_before"),
                {"play": 0, "link": 0, "arrangement_record": 0, "session_record": 0},
            )
            or not _json_value_matches(
                report.get("transport_after"),
                {"play": 0, "link": 0, "arrangement_record": 0, "session_record": 0},
            )
            or report.get("file_menu_closed") is not True
            or type(report.get("stable_destination_observations")) is not int
            or report["stable_destination_observations"] < 3
            or not marker_exists
            or report.get("manual_inspection_required") is not False
        ):
            raise ValueError(
                "original pending Open completion or post-state proof differs"
            )
        destination_after = _read_set(destination, destination_sha256)[1]
        reported_destination = report["destination_file_after"]
        if (
            not _json_value_matches(reported_destination, destination_after)
            or not isinstance(report.get("destination_file_before"), dict)
            or not _json_value_matches(
                report.get("destination_file_before"), destination_pin
            )
            or report.get("destination_metadata_delta")
            != ([] if destination_after == destination_pin else ["ctime"])
            or {
                key: value
                for key, value in destination_after.items()
                if key not in ("ctime_seconds", "ctime_nanoseconds")
            }
            != {
                key: value
                for key, value in destination_pin.items()
                if key not in ("ctime_seconds", "ctime_nanoseconds")
            }
        ):
            raise ValueError(
                "destination after-open pin does not match its approved preflight pin"
            )
        return report
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        if execute:
            return unknown(
                "native-receipt",
                f"File-menu cancellation/original Open outcome unverified: {error}; inspect, never retry",
            )
        raise ValueError(
            f"native File-menu cancel preflight receipt invalid: {error}"
        ) from error
