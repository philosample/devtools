"""Owned same-process Live Save As; no quit, reopen or generic prompt acceptance."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import uuid
from pathlib import Path

from .live_set_lifecycle import _canonical_path, _directory_chain, _set_path
from .live_set_open import _exclusive, _private_root, _read_set

FORMAT = "codex-devtools-live-set-save-as-v1"
ACTIONS = (
    "focus",
    "menu_navigation",
    "save_as_menu",
    "project_prompt",
    "save",
    "quit",
    "open",
)


def _directory_pin(path: Path) -> dict:
    _private_root(path)
    info = path.lstat()
    return {"path": str(path), "device": str(info.st_dev), "inode": str(info.st_ino)}


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _backup(
    root, source_data, source_pin, selected, final, directory, pid, bundle, modal
):
    run = root / str(uuid.uuid4())
    run.mkdir(mode=0o700)
    path = run / "source-before-save.als"
    _exclusive(path, source_data)
    pin = _read_set(path, source_pin["sha256"])[1]
    proof = {
        "format": "codex-devtools-live-set-save-as-backup-v1",
        "pid": pid,
        "bundle_identifier": bundle,
        "source": source_pin,
        "backup": pin,
        "destination_set_url": selected.as_uri(),
        "expected_final_set_url": final.as_uri(),
        "destination_directory": _directory_pin(directory),
        "project_required_dialog": modal,
        "evidence_limit": "pre-save file bytes only; unsaved in-memory state unproven",
    }
    receipt = run / "receipt.json"
    data = (json.dumps(proof, sort_keys=True, indent=2) + "\n").encode()
    _exclusive(receipt, data)
    if receipt.read_bytes() != data:
        raise ValueError("Save As backup receipt readback differs")
    for parent in (run, root, directory.parent):
        _sync_directory(parent)
    return {"receipt_path": str(receipt), **proof}


def run_set_save_as(
    pid: int,
    *,
    expected_bundle_id: str,
    source_set: Path,
    source_sha256: str,
    destination_directory: Path,
    destination_filename: str,
    expected_final_set: Path,
    app_bundle: Path,
    backup_root: Path,
    project_required_dialog: bool = False,
    execute: bool = False,
) -> dict:
    """Review read-only ready first; execute reserves a new directory and saves once."""
    if platform.system() != "Darwin":
        raise ValueError("same-process Live Save As requires macOS")
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("positive 32-bit Live PID required")
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id.strip():
        raise ValueError("expected bundle identifier required")
    if not isinstance(source_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", source_sha256
    ):
        raise ValueError("reviewed source SHA-256 required")
    if type(execute) is not bool or type(project_required_dialog) is not bool:
        raise TypeError("explicit boolean mode flags required")
    source = _set_path(source_set, must_exist=True)
    _directory_chain(source.parent, outside_git=True)
    source_data, source_pin = _read_set(source, source_sha256)
    if source.lstat().st_uid != os.getuid():
        raise ValueError("Save As source must be owned by the current user")
    if (
        not isinstance(destination_filename, str)
        or Path(destination_filename).name != destination_filename
        or not destination_filename.endswith(".als")
        or destination_filename == ".als"
        or len(destination_filename.encode()) > 255
        or destination_filename.strip() != destination_filename
        or any(ord(char) < 32 or char in "\\/" for char in destination_filename)
    ):
        raise ValueError("plain explicit .als filename required")
    directory = destination_directory
    if (
        not directory.is_absolute()
        or directory.resolve(strict=False) != directory
        or str(directory) != os.path.normpath(directory)
        or any(ord(char) < 32 for char in str(directory))
        or "\\" in str(directory)
    ):
        raise ValueError("canonical new destination directory required")
    _directory_chain(directory.parent, outside_git=True)
    if directory.exists() or directory.is_symlink():
        raise ValueError("destination directory must not already exist")
    selected = _canonical_path(directory / destination_filename)
    final = _canonical_path(expected_final_set)
    project = (
        directory / (destination_filename[:-4] + " Project") / destination_filename
    )
    if final not in (selected, project) or final == source:
        raise ValueError(
            "expected final Set must be the exact direct or Live Project form"
        )
    app = app_bundle
    if (
        not app.is_absolute()
        or app.suffix != ".app"
        or app.resolve(strict=True) != app
        or not app.is_dir()
    ):
        raise ValueError("exact canonical Live.app directory required")
    _private_root(backup_root)
    backup = None
    if execute:
        directory.mkdir(mode=0o700)
        backup = _backup(
            backup_root,
            source_data,
            source_pin,
            selected,
            final,
            directory,
            pid,
            expected_bundle_id,
            project_required_dialog,
        )
    command = [
        "/usr/bin/swift",
        str(Path(__file__).with_name("live_set_lifecycle.swift")),
        "--save-copy-only",
        "--pid",
        str(pid),
        "--bundle-id",
        expected_bundle_id,
        "--source-set",
        str(source),
        "--source-sha256",
        source_sha256,
        "--destination-set",
        str(selected),
        "--expected-final-set",
        str(final),
        "--app",
        str(app),
    ]
    if project_required_dialog:
        command.append("--project-required-dialog")
    if execute:
        command.extend(("--execute", "--backup-receipt", backup["receipt_path"]))

    def unknown(phase, error):
        actions = dict.fromkeys(ACTIONS, "not_attempted")
        if execute:
            actions.update(focus="outcome_unknown", save="outcome_unknown")
            if project_required_dialog:
                actions["project_prompt"] = "outcome_unknown"
            else:
                actions.update(
                    menu_navigation="outcome_unknown", save_as_menu="outcome_unknown"
                )
        return {
            "format": FORMAT,
            "status": "stopped",
            "phase": phase,
            "error": error,
            "target": {"pid": pid, "bundle_identifier": expected_bundle_id},
            "source_set_url": source.as_uri(),
            "app": str(app),
            "project_required_dialog": project_required_dialog,
            "read_only": not execute,
            "actions": actions,
            "manual_inspection_required": execute,
        }

    def finish(report):
        report["source_backup"] = backup
        report["destination_set_url"] = selected.as_uri()
        report["expected_final_set_url"] = final.as_uri()
        if backup:
            receipt = Path(backup["receipt_path"]).parent / "result.json"
            report["receipt_path"] = str(receipt)
            try:
                _exclusive(
                    receipt,
                    (json.dumps(report, sort_keys=True, indent=2) + "\n").encode(),
                )
                _sync_directory(receipt.parent)
            except OSError as error:
                stopped = unknown(
                    "result-receipt",
                    f"Save As receipt persistence failed: {error}; inspect, never retry",
                )
                stopped.update(source_backup=backup, receipt_path=str(receipt))
                return stopped
        return report

    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=240
        )
    except subprocess.TimeoutExpired:
        return finish(
            unknown(
                "outer-timeout",
                "Save As completion unknown after deadline; inspect, never retry",
            )
        )
    except (OSError, subprocess.SubprocessError) as error:
        if execute:
            return finish(unknown("native-call", str(error)))
        raise ValueError(f"Save As preflight unavailable: {error}") from error
    try:
        if len(result.stdout.encode()) > 1024 * 1024:
            raise ValueError("native Save As receipt exceeds bounds")
        report = json.loads(result.stdout)
        if (
            not isinstance(report, dict)
            or report.get("format") != FORMAT
            or report.get("target", {}).get("pid") != pid
            or report.get("target", {}).get("bundle_identifier") != expected_bundle_id
            or report.get("read_only") is not (not execute)
        ):
            raise ValueError("native Save As schema/target/mode mismatch")
        actions = report.get("actions")
        allowed = {
            "focus": {"not_attempted", "verified", "outcome_unknown"},
            "menu_navigation": {"not_attempted", "verified", "outcome_unknown"},
            "save_as_menu": {"not_attempted", "verified_panel", "outcome_unknown"},
            "project_prompt": {
                "not_attempted",
                "verified_save_panel",
                "outcome_unknown",
            },
            "save": {"not_attempted", "destination_observed", "outcome_unknown"},
            "quit": {"not_attempted"},
            "open": {"not_attempted"},
        }
        if (
            not isinstance(actions, dict)
            or set(actions) != set(ACTIONS)
            or any(value not in allowed[key] for key, value in actions.items())
            or (
                not project_required_dialog
                and actions["project_prompt"] != "not_attempted"
            )
            or (
                project_required_dialog
                and any(
                    actions[key] != "not_attempted"
                    for key in ("menu_navigation", "save_as_menu")
                )
            )
        ):
            raise ValueError("invalid or unauthorized Save As action receipt")
        if report.get("status") == "stopped":
            attempted = any(value != "not_attempted" for value in actions.values())
            if (
                result.returncode != 1
                or report.get("manual_inspection_required") is not attempted
                or (not execute and attempted)
            ):
                raise ValueError("invalid stopped Save As receipt")
            if not isinstance(report.get("phase"), str) or not isinstance(
                report.get("error"), str
            ):
                raise ValueError("stopped Save As phase/error missing")
            return finish(report)
        expected_actions = dict.fromkeys(ACTIONS, "not_attempted")
        if execute:
            expected_actions.update(focus="verified", save="destination_observed")
            if project_required_dialog:
                expected_actions["project_prompt"] = "verified_save_panel"
            else:
                expected_actions.update(
                    menu_navigation="verified", save_as_menu="verified_panel"
                )
        if (
            result.returncode != 0
            or report.get("status") != ("saved-copy" if execute else "ready")
            or actions != expected_actions
            or report.get("source_set_url") != source.as_uri()
            or report.get("destination_set_url") != selected.as_uri()
            or report.get("expected_final_set_url") != final.as_uri()
            or report.get("project_required_dialog") is not project_required_dialog
            or report.get("app") != str(app)
            or report.get("transport")
            != dict.fromkeys(
                ("play", "link", "arrangement_record", "session_record"), 0
            )
            or report.get("source_file") != source_pin
            or _read_set(source, source_sha256)[1] != source_pin
        ):
            raise ValueError(
                "Save As identity, source pin, flags or completion mismatch"
            )
        if execute:
            pin = report.get("destination_file")
            if (
                report.get("same_process") is not True
                or report.get("saved_set_url") != final.as_uri()
                or report.get("stable_observations") != 3
                or report.get("source_file_after_save") != source_pin
                or report.get("backup_receipt") != backup["receipt_path"]
                or report.get("destination_directory")
                != backup["destination_directory"]
                or _directory_pin(directory) != backup["destination_directory"]
                or not isinstance(pin, dict)
                or not re.fullmatch(r"[0-9a-f]{64}", str(pin.get("sha256")))
                or _read_set(final, pin["sha256"])[1] != pin
                or _read_set(Path(backup["backup"]["path"]), source_sha256)[1]
                != backup["backup"]
            ):
                raise ValueError(
                    "same-process saved destination/backup/stable identity proof missing"
                )
        elif directory.exists() or directory.is_symlink():
            raise ValueError(
                "read-only preflight unexpectedly created a destination directory"
            )
        return finish(report)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        if execute:
            return finish(
                unknown(
                    "native-receipt",
                    f"Save As completion unverified: {error}; inspect, never retry",
                )
            )
        raise ValueError(f"invalid Save As preflight receipt: {error}") from error
