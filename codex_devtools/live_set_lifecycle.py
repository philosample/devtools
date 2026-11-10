"""Opt-in native AX lifecycle for one explicitly identified Live Set."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import re
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.parsers.expat import ExpatError

FORMAT = "codex-devtools-live-set-lifecycle-v1"
PAUSED_FORMAT = "codex-devtools-live-set-paused-v1"
RESUME_FORMAT = "codex-devtools-live-set-resume-v1"
EVIDENCE_LIMIT = "saved file bytes and exact PID exit only; MIDI settings, M4L recall, wire and hardware require independent verification"
NO_ACTIONS = dict.fromkeys(
    ("focus", "menu_navigation", "save_as_menu", "save", "quit", "open"),
    "not_attempted",
)
PAUSED_ACTIONS = {
    "focus": "verified",
    "menu_navigation": "verified",
    "save_as_menu": "verified_panel",
    "save": "destination_observed",
    "quit": "terminated",
    "open": "not_attempted",
}
PAUSED_KEYS = {
    "format",
    "mode",
    "status",
    "target",
    "source_set_url",
    "destination_set_url",
    "saved_set",
    "app",
    "read_only",
    "actions",
    "evidence_limit",
    "paused_at_utc",
}


def _validate_menu_diagnostics(value: object, expected_pid: int) -> None:
    if not isinstance(value, dict) or set(value) != {
        "phase",
        "observed_at_utc",
        "expected_pid",
        "frontmost_pid",
        "focused_window",
        "owned_window",
        "focused_window_matches_owned",
        "blocking_windows",
        "windows_complete",
        "file_menu",
        "save_as_item",
    }:
        raise ValueError("native Live lifecycle returned invalid menu diagnostics")
    if value.get("phase") not in {"save-as-menu", "quit-menu"}:
        raise ValueError("native Live lifecycle returned invalid menu diagnostic phase")
    timestamp = value.get("observed_at_utc")
    if not isinstance(timestamp, str) or len(timestamp) > 40:
        raise TypeError("native Live lifecycle returned invalid diagnostic timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            "native Live lifecycle returned invalid diagnostic timestamp"
        ) from error
    if parsed.tzinfo is None or value.get("expected_pid") != expected_pid:
        raise ValueError("native Live lifecycle diagnostic target/timestamp mismatch")
    frontmost = value.get("frontmost_pid")
    if frontmost is not None and (type(frontmost) is not int or frontmost <= 0):
        raise ValueError("native Live lifecycle returned invalid frontmost PID")
    if type(value.get("windows_complete")) is not bool:
        raise ValueError("native Live lifecycle returned invalid window completeness")
    focus_match = value.get("focused_window_matches_owned")
    if focus_match is not None and type(focus_match) is not bool:
        raise ValueError("native Live lifecycle returned invalid focus identity")

    element_keys = {
        "role",
        "title",
        "identifier",
        "document_url",
        "modal",
        "enabled",
        "selected",
        "expanded",
        "press_action",
        "actions",
        "blocking",
    }

    def element(item: object, *, blocker: bool = False) -> None:
        if not isinstance(item, dict) or set(item) != element_keys:
            raise ValueError("native Live lifecycle returned malformed AX diagnostic")
        for key, limit in (
            ("role", 128),
            ("title", 256),
            ("identifier", 256),
            ("document_url", 512),
        ):
            field = item[key]
            if field is not None and (not isinstance(field, str) or len(field) > limit):
                raise ValueError(
                    "native Live lifecycle returned oversized AX diagnostic"
                )
        for key in ("modal", "enabled", "selected", "expanded"):
            if item[key] is not None and type(item[key]) is not bool:
                raise ValueError("native Live lifecycle returned invalid AX state")
        if type(item["press_action"]) is not bool or type(item["blocking"]) is not bool:
            raise ValueError("native Live lifecycle returned invalid AX action state")
        actions = item["actions"]
        if (
            not isinstance(actions, list)
            or len(actions) > 16
            or any(
                not isinstance(action, str) or len(action) > 128 for action in actions
            )
        ):
            raise ValueError("native Live lifecycle returned invalid AX action list")
        if blocker and item["blocking"] is not True:
            raise ValueError("native Live lifecycle returned a nonblocking modal entry")

    focused = value.get("focused_window")
    if focused is not None:
        element(focused)
    owned = value.get("owned_window")
    if owned is not None:
        element(owned)
    if focus_match is True and (focused is None or owned is None):
        raise ValueError("native Live lifecycle focus match lacks window identities")
    blockers = value.get("blocking_windows")
    if not isinstance(blockers, list) or len(blockers) > 32:
        raise ValueError("native Live lifecycle returned unbounded modal diagnostics")
    for item in blockers:
        element(item, blocker=True)
    for key in ("file_menu", "save_as_item"):
        item = value.get(key)
        if item is not None:
            element(item)


def _canonical_path(value: Path) -> Path:
    supplied = value.expanduser()
    if not supplied.is_absolute() or supplied.suffix.lower() != ".als":
        raise ValueError("an absolute .als file path is required")
    raw = str(supplied)
    if (
        raw != os.path.normpath(raw)
        or "\\" in raw
        or any(part in (".", "..") for part in supplied.parts)
    ):
        raise ValueError("Set path must be normalized and contain no dot segments")
    try:
        resolved = supplied.resolve(strict=False)
    except OSError as error:
        raise ValueError(f"cannot resolve selected Set path: {error}") from error
    if resolved != supplied:
        raise ValueError("Set path components must not be symbolic links")
    return supplied


def _directory_chain(directory: Path, *, outside_git: bool) -> None:
    for component in (Path("/"), *directory.parents[::-1], directory):
        try:
            component.lstat()
        except OSError as error:
            raise ValueError(
                f"destination directory component is unavailable: {component}"
            ) from error
        if component.is_symlink() or not component.is_dir():
            raise ValueError(
                "destination path must have only real directory components"
            )
        if outside_git and (component / ".git").exists():
            raise ValueError("destination must be outside Git")


def _set_path(value: Path, *, must_exist: bool) -> Path:
    supplied = _canonical_path(value)
    if must_exist:
        try:
            info = supplied.lstat()
        except OSError as error:
            raise ValueError(f"cannot read selected Set file: {error}") from error
        if not stat.S_ISREG(info.st_mode) or supplied.is_symlink():
            raise ValueError("source Set must be a regular .als file")
    else:
        if supplied.exists() or supplied.is_symlink():
            raise ValueError("destination Set must not already exist")
        _directory_chain(supplied.parent, outside_git=True)
    return supplied


def _uncertain_pause(pid: int, bundle: str, error: str) -> dict:
    actions = dict.fromkeys(NO_ACTIONS, "outcome_unknown")
    actions["open"] = "not_attempted"
    return {
        "format": FORMAT,
        "status": "stopped",
        "target": {"pid": pid, "bundle_identifier": bundle},
        "phase": "native-receipt",
        "error": error,
        "actions": actions,
        "manual_inspection_required": True,
    }


def run_lifecycle(
    pid: int,
    *,
    expected_bundle_id: str,
    source_set: Path,
    destination_set: Path | None = None,
    app_bundle: Path | None = None,
    execute: bool = False,
    stop_before_reopen: bool = False,
) -> dict:
    """Inspect by default; invoke native Save/Quit/Open only with ``execute``."""
    if platform.system() != "Darwin":
        raise ValueError("native Live Set lifecycle requires macOS")
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("PID must be a positive 32-bit integer")
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id.strip():
        raise ValueError("expected bundle identifier is required")
    if type(execute) is not bool or type(stop_before_reopen) is not bool:
        raise TypeError("explicit boolean lifecycle flags required")
    if source_set is None:
        raise ValueError("--source-set is required for Save As lifecycle")
    if stop_before_reopen and (destination_set is None or app_bundle is None):
        raise ValueError("--stop-before-reopen requires destination and app")
    source = _set_path(source_set, must_exist=True)
    if execute and destination_set is None:
        raise ValueError(
            "an exact new destination .als path is required with --execute"
        )
    if execute and app_bundle is None:
        raise ValueError("the exact Live.app bundle path is required with --execute")
    destination = None
    if destination_set is not None:
        destination = _set_path(destination_set, must_exist=False)
        if destination == source:
            raise ValueError("source and destination Set paths must differ")
        if not destination.parent.is_dir():
            raise ValueError("destination parent directory must already exist")
    app = None
    if app_bundle is not None:
        app = app_bundle.expanduser()
        if (
            not app.is_absolute()
            or app.suffix.lower() != ".app"
            or not app.is_dir()
            or app.resolve(strict=True) != app
        ):
            raise ValueError("exact existing .app bundle path required")

    script = Path(__file__).with_name("live_set_lifecycle.swift")
    command = [
        "/usr/bin/swift",
        str(script),
        "--pid",
        str(pid),
        "--bundle-id",
        expected_bundle_id,
        "--source-set",
        str(source),
    ]
    if destination is not None:
        command.extend(("--destination-set", str(destination)))
    if app is not None:
        command.extend(("--app", str(app)))
    if stop_before_reopen:
        command.append("--stop-before-reopen")
    if execute:
        command.append("--execute")
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=240
        )
    except subprocess.TimeoutExpired:
        # The native helper's bounded waits total 180 seconds. If its outer
        # deadline still expires, it may have been stopped after any action.
        # Return an explicit uncertain receipt so callers know to inspect Live.
        actions = {
            name: "outcome_unknown" if execute else "not_attempted"
            for name in (
                "focus",
                "menu_navigation",
                "save_as_menu",
                "save",
                "quit",
                "open",
            )
        }
        if stop_before_reopen:
            actions["open"] = "not_attempted"
        return {
            "format": FORMAT,
            "status": "stopped",
            "target": {"pid": pid, "bundle_identifier": expected_bundle_id},
            "phase": "outer-timeout",
            "error": "native helper exceeded its outer deadline; action outcomes are unknown",
            "actions": actions,
            "manual_inspection_required": execute,
            "read_only": not execute,
            "source_set_url": source.as_uri(),
            "destination_set_url": destination.as_uri() if destination else None,
        }
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"native Live lifecycle command failed: {error}") from error
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        if execute and stop_before_reopen:
            return _uncertain_pause(
                pid,
                expected_bundle_id,
                "invalid native pause JSON; inspect Live and destination",
            )
        if result.returncode:
            message = result.stderr.strip()[-2000:]
            raise ValueError(
                message or f"native Live lifecycle exited {result.returncode}"
            ) from error
        raise ValueError("native Live lifecycle returned invalid JSON") from error
    if isinstance(report, dict) and report.get("format") == PAUSED_FORMAT:
        if not execute or not stop_before_reopen or result.returncode != 0:
            raise ValueError("unexpected paused lifecycle receipt for selected mode")
        try:
            _validate_paused(report)
            if (
                report["target"]
                != {"pid": pid, "bundle_identifier": expected_bundle_id}
                or report["source_set_url"] != source.as_uri()
                or report["destination_set_url"] != destination.as_uri()
                or report["app"]["path"] != str(app)
            ):
                raise ValueError(
                    "paused lifecycle receipt differs from caller selection"
                )
        except (ValueError, TypeError, KeyError, OSError) as error:
            return _uncertain_pause(
                pid, expected_bundle_id, f"invalid native pause receipt: {error}"
            )
        return report
    if not isinstance(report, dict) or report.get("format") != FORMAT:
        if execute and stop_before_reopen:
            return _uncertain_pause(
                pid, expected_bundle_id, "unexpected native pause schema"
            )
        raise ValueError("native Live lifecycle returned an unexpected schema")
    if report.get("status") == "stopped":
        actions = report.get("actions")
        allowed_actions = {
            "focus": {"not_attempted", "outcome_unknown", "verified"},
            "menu_navigation": {"not_attempted", "outcome_unknown", "verified"},
            "save_as_menu": {"not_attempted", "outcome_unknown"},
            "save": {"not_attempted", "outcome_unknown"},
            "quit": {"not_attempted", "outcome_unknown"},
            "open": {"not_attempted", "outcome_unknown"},
        }
        target = report.get("target")
        action_attempted = isinstance(actions, dict) and any(
            actions.get(name) != "not_attempted" for name in allowed_actions
        )
        if (
            result.returncode != 1
            or not isinstance(actions, dict)
            or set(actions) != set(allowed_actions)
            or any(
                actions[name] not in allowed
                for name, allowed in allowed_actions.items()
            )
            or not isinstance(report.get("phase"), str)
            or not isinstance(report.get("error"), str)
            or report.get("manual_inspection_required") is not action_attempted
            or (
                action_attempted
                and (
                    not isinstance(target, dict)
                    or target.get("pid") != pid
                    or target.get("bundle_identifier") != expected_bundle_id
                )
            )
        ):
            raise ValueError("native Live lifecycle returned an invalid stop receipt")
        if "diagnostics" in report:
            if (
                report.get("phase") not in {"save-as-menu", "quit-menu"}
                or not action_attempted
            ):
                raise ValueError(
                    "native Live lifecycle returned menu diagnostics outside menu inspection"
                )
            _validate_menu_diagnostics(report["diagnostics"], pid)
            if report["diagnostics"]["phase"] != report["phase"]:
                raise ValueError("native Live lifecycle menu diagnostic phase mismatch")
        return report
    if (
        result.returncode != 0
        or report.get("status") not in {"ready", "reopened"}
        or report.get("target", {}).get("pid") != pid
        or report.get("target", {}).get("bundle_identifier") != expected_bundle_id
    ):
        raise ValueError(
            "native Live lifecycle returned an unexpected schema or target"
        )
    if stop_before_reopen and report.get("status") == "reopened":
        uncertain = _uncertain_pause(
            pid, expected_bundle_id, "pause mode unexpectedly reports reopen"
        )
        uncertain["actions"]["open"] = "outcome_unknown"
        return uncertain
    if report.get("status") == "ready" and execute:
        raise ValueError(
            "native Live lifecycle returned without completing requested actions"
        )
    if report.get("status") == "reopened" and not execute:
        raise ValueError("read-only Live lifecycle unexpectedly reports native actions")
    if report.get("source_set_url") != source.as_uri():
        raise ValueError(
            "native Live lifecycle source URL differs from the selected Set"
        )
    if (
        destination is not None
        and report.get("destination_set_url") != destination.as_uri()
    ):
        raise ValueError(
            "native Live lifecycle destination URL differs from the selected path"
        )
    if report.get("status") == "ready":
        if report.get("read_only") is not True or report.get("actions") != {
            "focus": "not_attempted",
            "menu_navigation": "not_attempted",
            "save_as_menu": "not_attempted",
            "save": "not_attempted",
            "quit": "not_attempted",
            "open": "not_attempted",
        }:
            raise ValueError("native Live lifecycle ready receipt claims an action")
    else:
        reopened = report.get("reopened_target")
        if (
            report.get("read_only") is not False
            or not isinstance(reopened, dict)
            or type(reopened.get("pid")) is not int
            or reopened["pid"] <= 0
            or reopened["pid"] == pid
            or reopened.get("bundle_identifier") != expected_bundle_id
            or report.get("actions")
            != {
                "focus": "verified",
                "menu_navigation": "verified",
                "save_as_menu": "verified_panel",
                "save": "destination_observed",
                "quit": "terminated",
                "open": "exact_set_window_observed",
            }
        ):
            raise ValueError(
                "native Live lifecycle reopened identity or action receipt is invalid"
            )
    return report


def _bounded_bytes(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file exceeded read bound")
    return data


def _pin_file(path: Path, *, max_bytes: int = 128 * 1024 * 1024) -> dict:
    """Hash one bounded, stable regular file without following symlink components."""
    if not path.is_absolute() or str(path) != os.path.normpath(str(path)):
        raise ValueError("absolute normalized file path required")
    _directory_chain(path.parent, outside_git=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= max_bytes:
            raise ValueError("pinned file must be a bounded nonempty regular file")
        digest = hashlib.sha256()
        count = 0
        while chunk := stream.read(65536):
            count += len(chunk)
            if count > max_bytes:
                raise ValueError("pinned file exceeded size bound")
            digest.update(chunk)
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

    if (
        identity(before) != identity(after)
        or identity(after) != identity(current)
        or count != before.st_size
    ):
        raise ValueError("pinned file changed while reading")
    mtime, mtime_ns = divmod(before.st_mtime_ns, 1_000_000_000)
    ctime, ctime_ns = divmod(before.st_ctime_ns, 1_000_000_000)
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "bytes": count,
        "device": str(before.st_dev),
        "inode": str(before.st_ino),
        "mtime_seconds": mtime,
        "mtime_nanoseconds": mtime_ns,
        "ctime_seconds": ctime,
        "ctime_nanoseconds": ctime_ns,
    }


def _app_pin(path: Path, bundle: str) -> dict:
    if path.suffix != ".app" or path.resolve(strict=True) != path or not path.is_dir():
        raise ValueError("exact canonical app directory required")
    info = path / "Contents/Info.plist"
    info_pin = _pin_file(info, max_bytes=1024 * 1024)
    try:
        metadata = plistlib.loads(_bounded_bytes(info, 1024 * 1024))
    except (ValueError, plistlib.InvalidFileException, ExpatError) as error:
        raise ValueError("invalid app metadata") from error
    if not isinstance(metadata, dict):
        raise ValueError("invalid app metadata dictionary")  # noqa: TRY004
    executable = metadata.get("CFBundleExecutable")
    if (
        metadata.get("CFBundleIdentifier") != bundle
        or not isinstance(executable, str)
        or not executable
        or executable in {".", ".."}
        or "/" in executable
        or "\\" in executable
    ):
        raise ValueError("app bundle identifier/executable mismatch")
    executable_pin = _pin_file(
        path / "Contents/MacOS" / executable, max_bytes=512 * 1024 * 1024
    )
    if _pin_file(info, max_bytes=1024 * 1024) != info_pin:
        raise ValueError("app metadata changed while reading")
    return {
        "path": str(path),
        "bundle_identifier": bundle,
        "info_file": info_pin,
        "executable_file": executable_pin,
    }


def _validate_file_pin(pin: object) -> None:
    numeric = {
        "bytes",
        "mtime_seconds",
        "mtime_nanoseconds",
        "ctime_seconds",
        "ctime_nanoseconds",
    }
    if (
        not isinstance(pin, dict)
        or set(pin) != numeric | {"path", "sha256", "device", "inode"}
        or any(type(pin[key]) is not int for key in numeric)
        or pin["bytes"] <= 0
        or any(
            not 0 <= pin[key] < 1_000_000_000
            for key in ("mtime_nanoseconds", "ctime_nanoseconds")
        )
        or not isinstance(pin["path"], str)
        or not isinstance(pin["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])
        or any(
            not isinstance(pin[key], str) or not pin[key].isdigit()
            for key in ("device", "inode")
        )
    ):
        raise ValueError("invalid closed file pin")


def _validate_paused(receipt: object) -> None:
    if (
        not isinstance(receipt, dict)
        or set(receipt) != PAUSED_KEYS
        or receipt.get("format") != PAUSED_FORMAT
        or receipt.get("mode") != "stop-before-reopen"
        or receipt.get("status") != "saved-and-quit"
        or receipt.get("read_only") is not False
        or receipt.get("actions") != PAUSED_ACTIONS
        or receipt.get("evidence_limit") != EVIDENCE_LIMIT
    ):
        raise ValueError("invalid closed saved-and-quit receipt")
    timestamp = receipt["paused_at_utc"]
    if not isinstance(timestamp, str) or len(timestamp) > 40:
        raise ValueError("paused receipt timestamp required")
    try:
        paused_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid paused receipt timestamp") from error
    if paused_at.tzinfo is None:
        raise ValueError("paused receipt timestamp must include a timezone")
    target = receipt["target"]
    if (
        not isinstance(target, dict)
        or set(target) != {"pid", "bundle_identifier"}
        or type(target.get("pid")) is not int
        or not 1 <= target["pid"] <= 2_147_483_647
        or not isinstance(target.get("bundle_identifier"), str)
        or not target["bundle_identifier"].strip()
    ):
        raise ValueError("invalid paused process identity")
    saved, app = receipt["saved_set"], receipt["app"]
    if not isinstance(saved, dict) or not isinstance(app, dict):
        raise ValueError("paused Set/app pins required")  # noqa: TRY004
    if not isinstance(saved.get("path"), str) or not isinstance(app.get("path"), str):
        raise ValueError("paused Set/app paths required")  # noqa: TRY004
    _validate_file_pin(saved)
    if set(app) != {"path", "bundle_identifier", "info_file", "executable_file"}:
        raise ValueError("invalid closed app pin")
    _validate_file_pin(app["info_file"])
    _validate_file_pin(app["executable_file"])
    destination = _set_path(Path(saved["path"]), must_exist=True)
    source_url = receipt["source_set_url"]
    if not isinstance(source_url, str):
        raise ValueError("exact paused source URL required")  # noqa: TRY004
    source = _canonical_path(Path(unquote(urlsplit(source_url).path)))
    if (
        source.as_uri() != source_url
        or destination.as_uri() != receipt["destination_set_url"]
        or source == destination
        or source.parent != destination.parent
    ):
        raise ValueError("paused Set URLs must be exact siblings")
    if saved != _pin_file(destination) or app != _app_pin(
        Path(app["path"]), target["bundle_identifier"]
    ):
        raise ValueError("paused Set or app bytes/identity changed")


def _unique_json(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate receipt JSON key")
        value[key] = item
    return value


def _read_paused(path: Path, expected_sha256: str) -> dict:
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise ValueError("caller-pinned receipt SHA-256 required")
    pin = _pin_file(path, max_bytes=65536)
    if pin["sha256"] != expected_sha256:
        raise ValueError("paused receipt SHA-256 mismatch")
    data = _bounded_bytes(path, 65536)
    if (
        len(data) > 65536
        or hashlib.sha256(data).hexdigest() != expected_sha256
        or _pin_file(path, max_bytes=65536) != pin
    ):
        raise ValueError("paused receipt changed while reading")
    receipt = json.loads(data, object_pairs_hook=_unique_json)
    _validate_paused(receipt)
    return receipt


def run_resume_lifecycle(
    receipt_path: Path, *, receipt_sha256: str, execute: bool = False
) -> dict:
    """Inspect a pinned pause receipt; explicit execution only reopens its saved Set."""
    if platform.system() != "Darwin":
        raise ValueError("native Live Set lifecycle requires macOS")
    if type(execute) is not bool:
        raise TypeError("explicit boolean execute flag required")
    receipt = _read_paused(receipt_path, receipt_sha256)
    command = [
        "/usr/bin/swift",
        str(Path(__file__).with_name("live_set_lifecycle.swift")),
        "--resume-receipt",
        str(receipt_path),
        "--receipt-sha256",
        receipt_sha256,
    ]
    if execute:
        command.append("--execute")
    origin = {"path": str(receipt_path), "sha256": receipt_sha256}

    def uncertain(error: str) -> dict:
        actions = dict(NO_ACTIONS)
        actions["open"] = "outcome_unknown" if execute else "not_attempted"
        return {
            "format": RESUME_FORMAT,
            "mode": "resume",
            "status": "stopped",
            "read_only": not execute,
            "target": receipt["target"],
            "resumed_from": origin,
            "phase": "native-receipt",
            "error": error,
            "actions": actions,
            "manual_inspection_required": execute,
        }

    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=240
        )
    except subprocess.TimeoutExpired:
        report = uncertain(
            "native resume exceeded outer deadline; open outcome unknown"
        )
        report["phase"] = "outer-timeout"
        return report
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"native resume command failed: {error}") from error
    try:
        if len(result.stdout) > 65536:
            raise ValueError("native resume receipt exceeds bounds")
        report = json.loads(result.stdout, object_pairs_hook=_unique_json)
        common = {
            "format",
            "mode",
            "status",
            "read_only",
            "target",
            "resumed_from",
            "actions",
        }
        if (
            not isinstance(report, dict)
            or report.get("format") != RESUME_FORMAT
            or report.get("mode") != "resume"
            or report.get("resumed_from") != origin
        ):
            raise ValueError("native resume schema/origin mismatch")
        actions = dict(NO_ACTIONS)
        if report.get("status") == "stopped":
            if (
                set(report) != common | {"phase", "error", "manual_inspection_required"}
                or result.returncode != 1
                or not isinstance(report["phase"], str)
                or not isinstance(report["error"], str)
                or report["read_only"] is not (not execute)
            ):
                raise ValueError("invalid stopped resume receipt")
            if execute and report["actions"] == {**actions, "open": "outcome_unknown"}:
                actions["open"] = "outcome_unknown"
            attempted = actions["open"] == "outcome_unknown"
            if (
                report["actions"] != actions
                or report["manual_inspection_required"] is not attempted
                or report["target"]
                not in (
                    receipt["target"],
                    {"pid": 0, "bundle_identifier": ""},
                )
                or (attempted and report["target"] != receipt["target"])
            ):
                raise ValueError("resume receipt claims unapproved actions")
            return report
        expected_status = "reopened" if execute else "ready"
        keys = common | {"saved_set", "app", "evidence_limit"}
        if execute:
            keys.add("reopened_target")
            actions["open"] = "exact_set_window_observed"
            target = report.get("reopened_target")
            if (
                not isinstance(target, dict)
                or set(target) != {"pid", "bundle_identifier", "process_name"}
                or type(target.get("pid")) is not int
                or not 1 <= target["pid"] <= 2_147_483_647
                or target["pid"] == receipt["target"]["pid"]
                or target["bundle_identifier"] != receipt["target"]["bundle_identifier"]
                or not isinstance(target["process_name"], str)
            ):
                raise ValueError("resume did not verify a fresh exact process")
        if (
            set(report) != keys
            or result.returncode != 0
            or report["status"] != expected_status
            or report["read_only"] is not (not execute)
            or report["target"] != receipt["target"]
            or report["actions"] != actions
            or report["saved_set"] != receipt["saved_set"]
            or report["app"] != receipt["app"]
            or report["evidence_limit"] != EVIDENCE_LIMIT
        ):
            raise ValueError("native resume mode/identity/actions mismatch")
        current = _pin_file(Path(receipt["saved_set"]["path"]))
        before = receipt["saved_set"]
        if execute:
            for key in ("ctime_seconds", "ctime_nanoseconds"):
                current.pop(key)
            before = {
                key: value
                for key, value in before.items()
                if key not in {"ctime_seconds", "ctime_nanoseconds"}
            }
        if (
            current != before
            or _app_pin(
                Path(receipt["app"]["path"]), receipt["target"]["bundle_identifier"]
            )
            != receipt["app"]
        ):
            raise ValueError("saved Set or app changed during native resume")
        return report
    except (ValueError, TypeError, KeyError, OSError) as error:
        if execute:
            return uncertain(f"native resume receipt invalid: {error}")
        raise ValueError(f"native resume preflight receipt invalid: {error}") from error
