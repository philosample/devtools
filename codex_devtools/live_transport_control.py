"""Inspect and explicitly bound the transport of one exact Live Set."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import platform
import shutil
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
_INSPECTION_CACHE_FORMAT = "codex-devtools-live-transport-helper-cache-v1"
_INSPECTION_CACHE_MAX_MANIFEST_BYTES = 32_768


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
    elif action == "position-window":
        actions = {"position": {"attempted": False, "outcome": "not_attempted"}}
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
    candidates = window.get("cg_candidates") if isinstance(window, dict) else None
    valid_candidates = (
        isinstance(candidates, list)
        and 0 < len(candidates) <= 64
        and all(
            isinstance(candidate, dict)
            and set(candidate) == {"number", "layer"}
            and type(candidate.get("number")) is int
            and candidate["number"] >= 0
            and type(candidate.get("layer")) is int
            and candidate["layer"] in {0, 3}
            for candidate in candidates
        )
        and len({candidate["number"] for candidate in candidates}) == len(candidates)
        and candidates
        == sorted(
            candidates, key=lambda candidate: (candidate["number"], candidate["layer"])
        )
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
        and valid_candidates
        and (
            window.get("window_number") is None
            or (
                type(window.get("window_number")) is int
                and any(
                    candidate["number"] == window["window_number"]
                    for candidate in candidates
                )
            )
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


def _native_focus_diagnostic(report: object, returncode: int) -> dict:
    diagnostic = {"returncode": returncode, "json_object": isinstance(report, dict)}
    if isinstance(report, dict):
        for field, maximum in (("status", 32), ("phase", 128), ("error", 512)):
            value = report.get(field)
            if isinstance(value, str):
                diagnostic[field] = value[:maximum]
    return diagnostic


def _valid_position_report(
    report: object,
    *,
    execute: bool,
    pid: int,
    bundle_id: str,
    set_path: Path,
    identity: str,
    generation: int,
    position: tuple[float, float] | None,
    preflight_hash: str | None,
    returncode: int,
) -> bool:
    if not isinstance(report, dict):
        return False
    expected_format = (
        "codex-devtools-live-position-window-result-v1"
        if execute
        else "codex-devtools-live-position-window-preflight-v1"
    )
    expected_status = "complete" if execute else "preflight_ready"
    target = report.get("target")
    window = report.get("window")
    geometry = window.get("geometry") if isinstance(window, dict) else None
    requested = report.get("requested_position")
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
    valid_position = (
        isinstance(requested, dict)
        and set(requested) == {"x", "y"}
        and all(
            type(value) in (int, float) and math.isfinite(value)
            for value in requested.values()
        )
        and position is not None
        and math.isclose(requested["x"], position[0], abs_tol=0.001)
        and math.isclose(requested["y"], position[1], abs_tol=0.001)
    )
    candidates = window.get("cg_candidates") if isinstance(window, dict) else None
    valid_candidates = (
        isinstance(candidates, list)
        and 0 < len(candidates) <= 64
        and all(
            isinstance(candidate, dict)
            and set(candidate) == {"number", "layer"}
            and type(candidate.get("number")) is int
            and candidate["number"] >= 0
            and type(candidate.get("layer")) is int
            and candidate["layer"] in {0, 3}
            for candidate in candidates
        )
        and len({candidate["number"] for candidate in candidates}) == len(candidates)
        and candidates
        == sorted(
            candidates, key=lambda candidate: (candidate["number"], candidate["layer"])
        )
    )
    base_valid = (
        report.get("format") == expected_format
        and report.get("status") == expected_status
        and report.get("read_only") is (not execute)
        and isinstance(target, dict)
        and target.get("pid") == pid
        and target.get("bundle_identifier") == bundle_id
        and target.get("process_generation") == generation
        and report.get("set_url") == set_path.as_uri()
        and isinstance(report.get("set_window_identity"), str)
        and bool(report["set_window_identity"])
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
        and valid_candidates
        and (
            window.get("window_number") is None
            or (
                type(window.get("window_number")) is int
                and any(
                    candidate["number"] == window["window_number"]
                    for candidate in candidates
                )
            )
        )
        and report.get("position_settable") is True
        and valid_position
        and report.get("actions") == ({"position": "known"} if execute else {})
    )
    if not base_valid:
        return False
    if not execute:
        return returncode == 0 and report.get("manual_inspection_required") is False
    after = report.get("window_after")
    after_geometry = after.get("geometry") if isinstance(after, dict) else None
    before_geometry = geometry
    after_candidates = after.get("cg_candidates") if isinstance(after, dict) else None
    valid_after_candidates = (
        isinstance(after_candidates, list)
        and 0 < len(after_candidates) <= 64
        and all(
            isinstance(candidate, dict)
            and set(candidate) == {"number", "layer"}
            and type(candidate.get("number")) is int
            and candidate["number"] >= 0
            and type(candidate.get("layer")) is int
            and candidate["layer"] in {0, 3}
            for candidate in after_candidates
        )
        and len({candidate["number"] for candidate in after_candidates})
        == len(after_candidates)
        and after_candidates
        == sorted(
            after_candidates,
            key=lambda candidate: (candidate["number"], candidate["layer"]),
        )
    )
    return (
        returncode == 0
        and report.get("preflight_sha256") == preflight_hash
        and report.get("manual_inspection_required") is False
        and isinstance(after, dict)
        and after.get("identity") == identity
        and after.get("role") == window.get("role")
        and after.get("subrole") == window.get("subrole")
        and after.get("title") == window.get("title")
        and after.get("identifier") == window.get("identifier")
        and after.get("document") == window.get("document")
        and valid_after_candidates
        and isinstance(after_geometry, dict)
        and set(after_geometry) == {"x", "y", "width", "height"}
        and all(
            type(value) in (int, float) and math.isfinite(value)
            for value in after_geometry.values()
        )
        and abs(after_geometry.get("x", math.inf) - requested["x"]) < 0.5
        and abs(after_geometry.get("y", math.inf) - requested["y"]) < 0.5
        and abs(after_geometry.get("width", math.inf) - before_geometry["width"])
        < 0.001
        and abs(after_geometry.get("height", math.inf) - before_geometry["height"])
        < 0.001
    )


def _helper_compile_command(source: Path, binary: Path) -> list[str]:
    return [
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
    ]


def _compile_helper(source: Path, binary: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _helper_compile_command(source, binary),
        check=False,
        capture_output=True,
        text=True,
        timeout=COMPILE_TIMEOUT_SECONDS,
    )


def _toolchain_identity() -> dict[str, object]:
    """Describe the exact local Swift compiler and SDK used for this helper."""
    commands = (
        ["/usr/bin/xcrun", "--find", "swiftc"],
        ["/usr/bin/xcrun", "swiftc", "--version"],
        ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"],
        ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"],
        ["/usr/bin/xcode-select", "-p"],
    )
    outputs: list[str] = []
    for command in commands:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode:
            raise RuntimeError(
                f"cannot identify Swift toolchain ({command[-1]}): "
                f"{result.stderr.strip()[-1000:]}"
            )
        value = result.stdout.strip()
        if not value or len(value) > 4096:
            raise RuntimeError("Swift toolchain returned an invalid identity value")
        outputs.append(value)
    compiler = Path(outputs[0]).resolve(strict=True)
    compiler_stat = compiler.stat()
    return {
        "compiler_path": str(compiler),
        "compiler_size": compiler_stat.st_size,
        "compiler_mtime_ns": compiler_stat.st_mtime_ns,
        "compiler_sha256": _hash_file(compiler),
        "compiler_version": outputs[1],
        "sdk_path": str(Path(outputs[2]).resolve(strict=True)),
        "sdk_version": outputs[3],
        "selected_developer_directory": str(Path(outputs[4]).resolve(strict=True)),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "os_version": f"{platform.system()}-{platform.release()}",
    }


def _inspection_cache_root() -> Path:
    return Path.home() / "Library" / "Caches" / "codex-devtools" / "live-transport"


def _check_cache_directory(path: Path, *, create: bool = False) -> None:
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    try:
        info = path.lstat()
    except OSError as error:
        raise RuntimeError(
            f"inspection helper cache directory is unavailable: {path}"
        ) from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise RuntimeError(f"inspection helper cache directory is unsafe: {path}")


def _prepare_inspection_cache(root: Path) -> None:
    home = Path.home()
    if root == _inspection_cache_root():
        for directory in (home / "Library", home / "Library" / "Caches"):
            try:
                info = directory.lstat()
            except OSError as error:
                raise RuntimeError(
                    f"user cache parent is unavailable: {directory}"
                ) from error
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError(f"user cache parent is unsafe: {directory}")
    _check_cache_directory(root.parent, create=True)
    _check_cache_directory(root, create=True)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_entry_binary(entry: Path, expected_identity: dict[str, object]) -> Path:
    try:
        directory = entry.lstat()
        manifest_path = entry / "manifest.json"
        binary = entry / "live_transport"
        manifest_info = manifest_path.lstat()
        binary_info = binary.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
            or not stat.S_ISREG(manifest_info.st_mode)
            or manifest_info.st_uid != os.getuid()
            or stat.S_IMODE(manifest_info.st_mode) != 0o600
            or manifest_info.st_size > _INSPECTION_CACHE_MAX_MANIFEST_BYTES
            or not stat.S_ISREG(binary_info.st_mode)
            or binary_info.st_uid != os.getuid()
            or stat.S_IMODE(binary_info.st_mode) != 0o700
        ):
            raise RuntimeError("inspection helper cache entry has unsafe file metadata")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "inspection helper cache entry is unreadable or corrupt"
        ) from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"format", "identity", "binary_sha256"}
        or manifest.get("format") != _INSPECTION_CACHE_FORMAT
        or manifest.get("identity") != expected_identity
        or not isinstance(manifest.get("binary_sha256"), str)
        or len(manifest["binary_sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in manifest["binary_sha256"]
        )
    ):
        raise RuntimeError(
            "inspection helper cache manifest does not match its identity"
        )
    if _hash_file(binary) != manifest["binary_sha256"]:
        raise RuntimeError("inspection helper cache binary hash mismatch")
    return binary


def _cached_inspection_helper(source: Path, *, cache_root: Path | None = None) -> Path:
    """Compile once per exact source/toolchain identity for read-only inspect."""
    root = cache_root or _inspection_cache_root()
    _prepare_inspection_cache(root)
    source_bytes = source.read_bytes()
    identity: dict[str, object] = {
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "compile_argv": _helper_compile_command(Path("<SOURCE>"), Path("<BINARY>")),
        "toolchain": _toolchain_identity(),
    }
    identity_bytes = json.dumps(
        identity, sort_keys=True, separators=(",", ":")
    ).encode()
    key = hashlib.sha256(identity_bytes).hexdigest()
    entry = root / key
    if entry.exists() or entry.is_symlink():
        return _cache_entry_binary(entry, identity)

    staging = Path(tempfile.mkdtemp(prefix=".building-", dir=root))
    try:
        if (
            source.read_bytes() != source_bytes
            or _toolchain_identity() != identity["toolchain"]
        ):
            raise RuntimeError(
                "Swift source or toolchain changed before helper compilation"
            )
        binary = staging / "live_transport"
        compilation = _compile_helper(source, binary)
        if compilation.returncode:
            raise RuntimeError(
                compilation.stderr.strip()[-2000:] or "native helper compilation failed"
            )
        os.chmod(binary, 0o700)
        if (
            source.read_bytes() != source_bytes
            or _toolchain_identity() != identity["toolchain"]
        ):
            raise RuntimeError(
                "Swift source or toolchain changed during helper compilation"
            )
        manifest = {
            "format": _INSPECTION_CACHE_FORMAT,
            "identity": identity,
            "binary_sha256": _hash_file(binary),
        }
        manifest_bytes = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode()
        if len(manifest_bytes) > _INSPECTION_CACHE_MAX_MANIFEST_BYTES:
            raise RuntimeError(
                "inspection helper cache manifest exceeds its size bound"
            )
        manifest_path = staging / "manifest.json"
        manifest_path.write_bytes(manifest_bytes)
        os.chmod(manifest_path, 0o600)
        try:
            os.rename(staging, entry)
        except OSError as error:
            if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            return _cache_entry_binary(entry, identity)
        return _cache_entry_binary(entry, identity)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


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
    position: tuple[float, float] | None = None,
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
    if (
        action in {"play", "stop"}
        or (action == "focus-window" and preflight_path is not None)
        or (action == "position-window" and preflight_path is not None)
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
    if position is not None:
        command.extend(
            ("--position-x", repr(position[0]), "--position-y", repr(position[1]))
        )
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
    position_x: float | None = None,
    position_y: float | None = None,
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
    if action not in {"focus-window", "position-window"} and any(
        value is not None for value in focus_options
    ):
        raise ValueError(
            "window options require --action focus-window or position-window"
        )
    if action != "position-window" and (
        position_x is not None or position_y is not None
    ):
        raise ValueError("position coordinates require --action position-window")
    if (position_x is None) != (position_y is None):
        raise ValueError("position-window requires both --position-x and --position-y")
    if position_x is not None and (
        type(position_x) not in (int, float)
        or type(position_y) not in (int, float)
        or not math.isfinite(position_x)
        or not math.isfinite(position_y)
    ):
        raise ValueError("position coordinates must be finite numbers")
    requested_position = (
        (float(position_x), float(position_y)) if position_x is not None else None
    )
    source_set = _set_file(set_path)
    if not execute:
        if action in {"focus-window", "position-window"}:
            if play_for_seconds is not None or expected_count is not None:
                raise ValueError(f"transport options are invalid for {action}")
            if preflight_receipt_path is not None or preflight_sha256 is not None:
                raise ValueError(
                    f"preflight receipt options are valid only for {action} execution"
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
        if action not in {"play", "stop", "focus-window", "position-window"}:
            raise ValueError(
                "--execute requires --action play, stop, focus-window, or position-window"
            )
        if receipt_path is None:
            raise ValueError("--execute requires a new --receipt path")
        if action in {"focus-window", "position-window"}:
            if play_for_seconds is not None or expected_count is not None:
                raise ValueError("transport options are invalid for focus-window")
            if type(expected_generation) is not int or expected_generation <= 0:
                raise ValueError(f"{action} requires the inspected process generation")
            if (
                preflight_receipt_path is None
                or not isinstance(preflight_sha256, str)
                or len(preflight_sha256) != 64
            ):
                raise ValueError(
                    f"{action} execution requires a preflight receipt and its reviewed SHA-256"
                )
            if window_identity is not None:
                raise ValueError(
                    f"{action} execution uses the identity pinned in its preflight receipt"
                )
            if requested_position is not None:
                raise ValueError(
                    "position coordinates are valid only during position-window preflight"
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
    if selected_action in {"focus-window", "position-window"} and not execute:
        initial["read_only"] = True
        initial["actions"] = {}
        if not isinstance(window_identity, str) or not window_identity.isdecimal():
            raise ValueError(
                f"{selected_action} preflight requires a CFHash window identity from a fresh AX snapshot"
            )
        if type(expected_generation) is not int or expected_generation <= 0:
            raise ValueError(
                f"{selected_action} preflight requires the inspected process generation"
            )
        if (
            not isinstance(snapshot_observed_at_utc, str)
            or not snapshot_observed_at_utc
        ):
            raise ValueError(
                f"{selected_action} preflight requires the fresh AX snapshot observation time"
            )
        if not isinstance(snapshot_launch_date, str) or not snapshot_launch_date:
            raise ValueError(
                f"{selected_action} preflight requires the AX snapshot process launch date"
            )
        if receipt_path is None:
            raise ValueError(f"{selected_action} preflight requires a new receipt path")
        if selected_action == "position-window" and requested_position is None:
            raise ValueError(
                "position-window preflight requires finite --position-x and --position-y"
            )
    if selected_action in {"focus-window", "position-window"} and execute:
        preflight_path = preflight_receipt_path
        if (
            preflight_path is None
            or not preflight_path.is_file()
            or preflight_path.is_symlink()
        ):
            raise ValueError(
                f"{selected_action} preflight receipt must be an existing regular file"
            )
        actual_hash = hashlib.sha256(preflight_path.read_bytes()).hexdigest()
        if actual_hash != preflight_sha256:
            raise ValueError(
                f"{selected_action} preflight receipt hash does not match the reviewed SHA-256"
            )
        if selected_action == "position-window":
            try:
                pinned = json.loads(preflight_path.read_text(encoding="utf-8"))
                pinned_position = pinned["requested_position"]
                requested_position = (
                    float(pinned_position["x"]),
                    float(pinned_position["y"]),
                )
                if not all(math.isfinite(value) for value in requested_position):
                    raise ValueError
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
            ) as error:
                raise ValueError(
                    "position-window preflight has no valid pinned destination"
                ) from error
    identity = None
    if execute or selected_action in {"focus-window", "position-window"}:
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
            compilation = None
            try:
                if selected_action == "inspect":
                    binary = _cached_inspection_helper(source)
                else:
                    compilation = _compile_helper(source, binary)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                initial["phase"] = "helper-compile"
                initial["error"] = f"native helper compile failed: {error}"
                return (
                    save(initial)
                    if execute or selected_action in {"focus-window", "position-window"}
                    else initial
                )
            if compilation is not None and compilation.returncode:
                initial["phase"] = "helper-compile"
                initial["error"] = (
                    compilation.stderr.strip()[-2000:]
                    or "native helper compilation failed"
                )
                return (
                    save(initial)
                    if execute or selected_action in {"focus-window", "position-window"}
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
                position=requested_position
                if selected_action == "position-window" and not execute
                else None,
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
                elif selected_action in {"focus-window", "position-window"}:
                    if execute:
                        names = (
                            ("position",)
                            if selected_action == "position-window"
                            else ("activate", "raise")
                        )
                        for name in names:
                            uncertain["actions"][name] = {
                                "attempted": True,
                                "outcome": "unknown",
                            }
                        uncertain["error"] = (
                            f"{selected_action} helper timed out; inspect the Live window before retry"
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
                if selected_action not in {"focus-window", "position-window"}:
                    uncertain["manual_inspection_required"] = (
                        selected_action != "inspect"
                    )
                return (
                    save(uncertain)
                    if execute or selected_action in {"focus-window", "position-window"}
                    else uncertain
                )
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError:
                report = None
            if selected_action in {"focus-window", "position-window"}:
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
                            f"{selected_action} preflight receipt has no valid selected identity"
                        ) from error
                valid = (
                    _valid_position_report(
                        report,
                        execute=execute,
                        pid=pid,
                        bundle_id=expected_bundle_id,
                        set_path=source_set,
                        identity=focus_identity or "",
                        generation=expected_generation or 0,
                        position=requested_position,
                        preflight_hash=preflight_sha256,
                        returncode=result.returncode,
                    )
                    if selected_action == "position-window"
                    else _valid_focus_report(
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
                if selected_action in {"focus-window", "position-window"}:
                    uncertain["native_helper"] = _native_focus_diagnostic(
                        report, result.returncode
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
                if selected_action in {"focus-window", "position-window"}:
                    if execute:
                        names = (
                            ("position",)
                            if selected_action == "position-window"
                            else ("activate", "raise")
                        )
                        for name in names:
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
                    if execute or selected_action in {"focus-window", "position-window"}
                    else uncertain
                )
            return (
                save(report)
                if execute or selected_action in {"focus-window", "position-window"}
                else report
            )
    except OSError as error:
        initial["phase"] = "native-helper-launch"
        initial["error"] = f"native helper launch failed: {error}"
        return (
            save(initial)
            if execute or selected_action in {"focus-window", "position-window"}
            else initial
        )
