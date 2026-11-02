"""Opt-in native AX lifecycle for one explicitly identified Live Set."""

from __future__ import annotations

import json
import os
import platform
import stat
import subprocess
from pathlib import Path

FORMAT = "codex-devtools-live-set-lifecycle-v1"


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


def run_lifecycle(
    pid: int,
    *,
    expected_bundle_id: str,
    source_set: Path,
    destination_set: Path | None = None,
    app_bundle: Path | None = None,
    execute: bool = False,
) -> dict:
    """Inspect by default; invoke native Save/Quit/Open only with ``execute``."""
    if platform.system() != "Darwin":
        raise ValueError("native Live Set lifecycle requires macOS")
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("PID must be a positive 32-bit integer")
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id.strip():
        raise ValueError("expected bundle identifier is required")
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
    if execute:
        command.append("--execute")
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=180
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"native Live lifecycle command failed: {error}") from error
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        if result.returncode:
            message = result.stderr.strip()[-2000:]
            raise ValueError(
                message or f"native Live lifecycle exited {result.returncode}"
            ) from error
        raise ValueError("native Live lifecycle returned invalid JSON") from error
    if not isinstance(report, dict) or report.get("format") != FORMAT:
        raise ValueError("native Live lifecycle returned an unexpected schema")
    if report.get("status") == "stopped":
        actions = report.get("actions")
        allowed_actions = {
            "menu_navigation": {"not_attempted", "outcome_unknown"},
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
