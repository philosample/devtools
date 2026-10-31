"""Pinned Ruff and Lefthook bootstrap for studio repositories."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def _pipx(*args: str, dry_run: bool = False, env: dict[str, str] | None = None) -> None:
    command = ["pipx", *args]
    if dry_run:
        print(" ".join(command))
        return
    result = subprocess.run(command, check=False, text=True, env=env)
    if result.returncode:
        raise RuntimeError(
            f"pipx command failed with exit code {result.returncode}: {command}"
        )


def _pipx_home() -> Path:
    result = subprocess.run(
        ["pipx", "environment", "--value", "PIPX_HOME"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("could not determine pipx PIPX_HOME for a reversible setup")
    return Path(result.stdout.strip()).expanduser().resolve()


def _pipx_env(bin_dir: Path, pipx_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PIPX_BIN_DIR"] = str(bin_dir)
    env["PIPX_HOME"] = str(pipx_home)
    return env


def _pipx_has_venv(package: str, *, env: dict[str, str] | None = None) -> bool:
    result = subprocess.run(
        ["pipx", "list", "--json"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    if result.returncode:
        raise RuntimeError("could not inspect pipx environments before removal")
    try:
        return package in json.loads(result.stdout).get("venvs", {})
    except json.JSONDecodeError as exc:
        raise RuntimeError("pipx returned invalid environment inventory JSON") from exc


def _write_receipt(receipt: dict[str, str]) -> None:
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=RECEIPT.parent, delete=False
    ) as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, RECEIPT)


def _receipt_context(
    bin_dir: Path, pipx_home: Path, prior: dict[str, str] | None = None
) -> dict[str, str]:
    prior = prior or {}
    ruff = bin_dir / f"ruff-{RUFF_VERSION}"
    lefthook = bin_dir / f"lefthook-{LEFTHOOK_VERSION}"
    return {
        "ruff": str(ruff),
        "ruff_version": RUFF_VERSION,
        "lefthook_pinned_path": str(lefthook),
        "lefthook_version": LEFTHOOK_VERSION,
        "lefthook_alias": str(bin_dir / "lefthook"),
        "pipx_bin_dir": str(bin_dir),
        "pipx_home": str(pipx_home),
        "created_ruff": prior.get("created_ruff", "false"),
        "created_lefthook": prior.get("created_lefthook", "false"),
        "created_lefthook_alias": prior.get("created_lefthook_alias", "false"),
    }


def _package_key(package: str) -> str:
    return package.replace(".", "-")


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
    resolved_before = shutil.which("lefthook")
    path_dirs = {
        Path(entry or os.curdir).expanduser().resolve()
        for entry in os.environ.get("PATH", "").split(os.pathsep)
    }
    if resolved_before and not _matching_version(
        Path(resolved_before), "lefthook", LEFTHOOK_VERSION
    ):
        raise RuntimeError(
            f"refusing to replace existing Lefthook on PATH: {resolved_before}"
        )
    if resolved_before is None and bin_dir.resolve() not in path_dirs:
        raise RuntimeError(
            f"pipx bin directory must be on PATH before setup: {bin_dir}"
        )
    if not shutil.which("pipx"):
        raise RuntimeError(
            "pipx is required; install pipx before bootstrapping quality tools"
        )
    pipx_home = _pipx_home()
    prior = json.loads(RECEIPT.read_text()) if RECEIPT.is_file() else None
    prior_bin_dir = None
    if prior:
        prior_bin_dir = (
            Path(prior.get("pipx_bin_dir", Path(prior["ruff"]).parent))
            .expanduser()
            .resolve()
        )
        prior_home = Path(prior.get("pipx_home", pipx_home)).expanduser().resolve()
        if prior_bin_dir != bin_dir.resolve() or prior_home != pipx_home:
            raise RuntimeError(
                "an existing quality-tool receipt belongs to a different pipx context; "
                "remove it in that context before bootstrapping here"
            )
    if not dry_run:
        bin_dir.mkdir(parents=True, exist_ok=True)
    ruff = bin_dir / f"ruff-{RUFF_VERSION}"
    lefthook_versioned = bin_dir / f"lefthook-{LEFTHOOK_VERSION}"
    lefthook_alias = bin_dir / "lefthook"
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
    env = _pipx_env(bin_dir.resolve(), pipx_home)
    for package, installed in (
        (_package_key(f"ruff-{RUFF_VERSION}"), ruff_preexisting),
        (_package_key(f"lefthook-{LEFTHOOK_VERSION}"), lefthook_preexisting),
    ):
        if not dry_run and not installed and _pipx_has_venv(package, env=env):
            raise RuntimeError(
                f"pipx environment {package} exists without its pinned executable; "
                "remove or repair it explicitly before bootstrapping"
            )
    receipt = _receipt_context(bin_dir.resolve(), pipx_home, prior)
    if not dry_run:
        _write_receipt(receipt)

    if not ruff_preexisting:
        try:
            _pipx(
                "install",
                f"--suffix=-{RUFF_VERSION}",
                f"ruff=={RUFF_VERSION}",
                dry_run=dry_run,
                env=env,
            )
        except RuntimeError:
            if not dry_run:
                receipt["created_ruff"] = "true"
                _write_receipt(receipt)
            raise
        receipt["created_ruff"] = "true"
        if not dry_run:
            _write_receipt(receipt)
    if not lefthook_preexisting:
        try:
            _pipx(
                "install",
                f"--suffix=-{LEFTHOOK_VERSION}",
                f"lefthook=={LEFTHOOK_VERSION}",
                dry_run=dry_run,
                env=env,
            )
        except RuntimeError:
            if not dry_run:
                receipt["created_lefthook"] = "true"
                _write_receipt(receipt)
            raise
        receipt["created_lefthook"] = "true"
        if not dry_run:
            _write_receipt(receipt)

    resolved = shutil.which("lefthook")
    if resolved:
        if not _matching_version(Path(resolved), "lefthook", LEFTHOOK_VERSION):
            raise RuntimeError(
                f"refusing to replace existing Lefthook on PATH: {resolved}"
            )
    elif not lefthook_alias.exists():
        if not dry_run:
            lefthook_alias.symlink_to(lefthook_versioned)
            receipt["created_lefthook_alias"] = "true"
            _write_receipt(receipt)
        else:
            print(f"symlink {lefthook_alias} -> {lefthook_versioned}")

    if dry_run:
        return None

    result = doctor()
    receipt["ruff_verified_path"] = result["ruff"]
    receipt["lefthook_resolved_path"] = result["lefthook"]
    _write_receipt(receipt)
    print(f"quality tools verified; local receipt: {RECEIPT}")
    return receipt


def remove() -> None:
    if not RECEIPT.is_file():
        raise RuntimeError(f"no quality-tool installation receipt found at {RECEIPT}")
    receipt = json.loads(RECEIPT.read_text())
    bin_dir = (
        Path(receipt.get("pipx_bin_dir", Path(receipt["ruff"]).parent))
        .expanduser()
        .resolve()
    )
    recorded_home = receipt.get("pipx_home")
    pipx_home = (
        Path(recorded_home).expanduser().resolve() if recorded_home else _pipx_home()
    )
    current_bin_dir = _bin_dir().resolve()
    current_pipx_home = _pipx_home()
    if current_bin_dir != bin_dir or current_pipx_home != pipx_home:
        raise RuntimeError(
            "refusing to remove quality tools from a different pipx context; "
            f"restore PIPX_BIN_DIR={bin_dir} and PIPX_HOME={pipx_home}"
        )
    env = _pipx_env(bin_dir, pipx_home)
    alias = Path(receipt.get("lefthook_alias", bin_dir / "lefthook")).expanduser()
    pinned = Path(
        receipt.get("lefthook_pinned_path", bin_dir / f"lefthook-{LEFTHOOK_VERSION}")
    ).expanduser()
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
        package = _package_key(f"ruff-{RUFF_VERSION}")
        if _pipx_has_venv(package, env=env):
            _pipx("uninstall", f"ruff-{RUFF_VERSION}", env=env)
    if receipt.get("created_lefthook") == "true":
        package = _package_key(f"lefthook-{LEFTHOOK_VERSION}")
        if _pipx_has_venv(package, env=env):
            _pipx("uninstall", f"lefthook-{LEFTHOOK_VERSION}", env=env)
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
