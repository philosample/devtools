"""Pinned Ruff and Lefthook bootstrap for studio repositories."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

RUFF_VERSION = "0.17.3"
LEFTHOOK_VERSION = "2.2.2"
RECEIPT = Path.home() / ".local/state/codex-devtools/quality-tools.json"


def _bin_dir() -> Path:
    value = os.environ.get("PIPX_BIN_DIR")
    return Path(value).expanduser() if value else Path.home() / ".local/bin"


def _version(executable: Path, args: list[str]) -> str:
    result = subprocess.run(
        [str(executable), *args], check=False, text=True, capture_output=True
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"could not run {executable}")
    return result.stdout.strip().splitlines()[0]


def _pipx(*args: str, dry_run: bool = False) -> None:
    command = ["pipx", *args]
    if dry_run:
        print(" ".join(command))
        return
    result = subprocess.run(command, check=False, text=True)
    if result.returncode:
        raise RuntimeError(
            f"pipx command failed with exit code {result.returncode}: {command}"
        )


def _matching_version(path: Path, tool: str, version: str) -> bool:
    if not path.exists():
        return False
    actual = _version(path, ["--version"] if tool == "ruff" else ["version"])
    expected = f"ruff {version}" if tool == "ruff" else version
    return actual == expected


def doctor() -> dict[str, str]:
    bin_dir = _bin_dir()
    ruff = bin_dir / f"ruff-{RUFF_VERSION}"
    lefthook_versioned = bin_dir / f"lefthook-{LEFTHOOK_VERSION}"
    if not _matching_version(ruff, "ruff", RUFF_VERSION):
        raise RuntimeError(f"missing or wrong pinned Ruff executable: {ruff}")
    if not _matching_version(lefthook_versioned, "lefthook", LEFTHOOK_VERSION):
        raise RuntimeError(
            f"missing or wrong pinned Lefthook executable: {lefthook_versioned}"
        )
    alias = shutil.which("lefthook")
    if alias is None or not _matching_version(
        Path(alias), "lefthook", LEFTHOOK_VERSION
    ):
        raise RuntimeError(
            "lefthook must resolve on PATH to version 2.2.2 for installed Git shims"
        )
    return {
        "ruff": str(ruff),
        "ruff_version": RUFF_VERSION,
        "lefthook": str(Path(alias).resolve()),
        "lefthook_version": LEFTHOOK_VERSION,
    }


def bootstrap(*, dry_run: bool = False) -> dict[str, str] | None:
    bin_dir = _bin_dir()
    if not shutil.which("pipx"):
        raise RuntimeError(
            "pipx is required; install pipx before bootstrapping quality tools"
        )
    if not dry_run:
        bin_dir.mkdir(parents=True, exist_ok=True)
    ruff = bin_dir / f"ruff-{RUFF_VERSION}"
    lefthook_versioned = bin_dir / f"lefthook-{LEFTHOOK_VERSION}"
    lefthook_alias = bin_dir / "lefthook"
    resolved_before = shutil.which("lefthook")
    if resolved_before and not _matching_version(
        Path(resolved_before), "lefthook", LEFTHOOK_VERSION
    ):
        raise RuntimeError(
            f"refusing to replace existing Lefthook on PATH: {resolved_before}"
        )
    if (
        not resolved_before
        and lefthook_alias.exists()
        and not _matching_version(lefthook_alias, "lefthook", LEFTHOOK_VERSION)
    ):
        raise RuntimeError(
            f"refusing to replace unexpected executable: {lefthook_alias}"
        )

    if ruff.exists() and not _matching_version(ruff, "ruff", RUFF_VERSION):
        raise RuntimeError(f"refusing to replace unexpected executable: {ruff}")
    if lefthook_versioned.exists() and not _matching_version(
        lefthook_versioned, "lefthook", LEFTHOOK_VERSION
    ):
        raise RuntimeError(
            f"refusing to replace unexpected executable: {lefthook_versioned}"
        )
    ruff_preexisting = _matching_version(ruff, "ruff", RUFF_VERSION)
    lefthook_preexisting = _matching_version(
        lefthook_versioned, "lefthook", LEFTHOOK_VERSION
    )
    alias_preexisting = bool(resolved_before) or lefthook_alias.exists()
    if not ruff_preexisting:
        _pipx(
            "install",
            f"--suffix=-{RUFF_VERSION}",
            f"ruff=={RUFF_VERSION}",
            dry_run=dry_run,
        )
    if not lefthook_preexisting:
        _pipx(
            "install",
            f"--suffix=-{LEFTHOOK_VERSION}",
            f"lefthook=={LEFTHOOK_VERSION}",
            dry_run=dry_run,
        )

    resolved = shutil.which("lefthook")
    if resolved:
        if not _matching_version(Path(resolved), "lefthook", LEFTHOOK_VERSION):
            raise RuntimeError(
                f"refusing to replace existing Lefthook on PATH: {resolved}"
            )
    elif not lefthook_alias.exists():
        if not dry_run:
            lefthook_alias.symlink_to(lefthook_versioned)
        else:
            print(f"symlink {lefthook_alias} -> {lefthook_versioned}")

    if dry_run:
        return None

    result = doctor()
    created = {
        "created_ruff": not ruff_preexisting,
        "created_lefthook": not lefthook_preexisting,
        "created_lefthook_alias": not alias_preexisting,
    }
    if RECEIPT.is_file():
        old_receipt = json.loads(RECEIPT.read_text())
        created = {
            key: value or old_receipt.get(key) == "true"
            for key, value in created.items()
        }
    result.update({key: str(value).lower() for key, value in created.items()})
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"quality tools verified; local receipt: {RECEIPT}")
    return result


def remove() -> None:
    if not RECEIPT.is_file():
        raise RuntimeError(f"no quality-tool installation receipt found at {RECEIPT}")
    receipt = json.loads(RECEIPT.read_text())
    alias = _bin_dir() / "lefthook"
    pinned = _bin_dir() / f"lefthook-{LEFTHOOK_VERSION}"
    if (
        receipt.get("created_lefthook_alias") == "true"
        and alias.is_symlink()
        and alias.resolve() == pinned.resolve()
    ):
        alias.unlink()
    elif receipt.get("created_lefthook_alias") == "true" and alias.exists():
        raise RuntimeError(
            f"refusing to remove or replace existing Lefthook command: {alias}"
        )
    if receipt.get("created_ruff") == "true":
        _pipx("uninstall", f"ruff-{RUFF_VERSION}")
    if receipt.get("created_lefthook") == "true":
        _pipx("uninstall", f"lefthook-{LEFTHOOK_VERSION}")
    RECEIPT.unlink()
    print(f"removed pinned quality tools listed in {RECEIPT}")


def main(action: str, *, dry_run: bool = False) -> int:
    try:
        if action == "bootstrap":
            bootstrap(dry_run=dry_run)
        elif action == "doctor":
            print(json.dumps(doctor(), indent=2, sort_keys=True))
        elif action == "remove":
            remove()
        else:
            raise ValueError(f"unknown quality action: {action}")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"quality: {exc}", file=sys.stderr)
        return 2
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap pinned studio Ruff and Lefthook tools"
    )
    commands = parser.add_subparsers(dest="action", required=True)
    bootstrap_parser = commands.add_parser(
        "bootstrap", help="install exact versions and write a rollback receipt"
    )
    bootstrap_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show operations without changing the machine",
    )
    commands.add_parser("doctor", help="verify installed versions")
    commands.add_parser(
        "remove",
        help="remove only tools and the alias created by the recorded bootstrap",
    )
    args = parser.parse_args()
    return main(args.action, dry_run=getattr(args, "dry_run", False))


if __name__ == "__main__":
    raise SystemExit(_cli())
