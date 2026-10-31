"""Explicit, hash-checked deployment of individually listed files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_FORMAT = "codex-devtools-deployment-manifest-v1"
PLAN_FORMAT = "codex-devtools-deployment-plan-v1"
RECEIPT_FORMAT = "codex-devtools-deployment-receipt-v1"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        # Keep ValueError for existing CLI and library callers.
        raise ValueError(f"expected a JSON object in {path}")  # noqa: TRY004
    return value


def _root(path: Path, label: str) -> Path:
    if not path.is_dir():
        raise ValueError(f"{label} root is not a directory: {path}")
    return path.resolve()


def _logical_paths(raw_paths: list[Any]) -> list[str]:
    if not raw_paths:
        raise ValueError("deployment manifest must list at least one file")
    paths: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        if not isinstance(raw, str) or not raw or "\\" in raw:
            raise ValueError(f"unsafe deployment path: {raw!r}")
        p = PurePosixPath(raw)
        if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
            raise ValueError(f"unsafe deployment path: {raw!r}")
        normalized = p.as_posix()
        # Reject case-only collisions too, since these commonly collide on macOS/Windows.
        key = normalized.casefold()
        if key in seen:
            raise ValueError(f"duplicate deployment path: {raw}")
        seen.add(key)
        paths.append(normalized)
    ordered = sorted(paths, key=lambda value: (value.casefold(), value))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if right.casefold().startswith(left.casefold() + "/"):
                raise ValueError(f"overlapping deployment paths: {left} and {right}")
    return sorted(paths)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _check_no_symlinks(root: Path, relative: str, *, allow_missing: bool) -> Path:
    target = root
    parts = PurePosixPath(relative).parts
    for i, part in enumerate(parts):
        target = target / part
        if target.is_symlink():
            raise ValueError(f"symlink in deployment path: {target}")
        if target.exists() and i < len(parts) - 1 and not target.is_dir():
            raise ValueError(f"non-directory path component: {target}")
        if not target.exists() and not allow_missing:
            raise ValueError(f"deployment file is missing: {target}")
    resolved = target.resolve(strict=False)
    if not _within(resolved, root):
        raise ValueError(f"deployment path escapes root: {relative}")
    return target


def _roots_disjoint(
    source: Path, destination: Path, backup: Path | None = None
) -> None:
    roots = [("source", source), ("destination", destination)]
    if backup is not None:
        roots.append(("backup", backup.resolve(strict=False)))
    for i, (left_name, left) in enumerate(roots):
        for right_name, right in roots[i + 1 :]:
            if _within(left, right) or _within(right, left):
                raise ValueError(f"{left_name} and {right_name} roots must not overlap")


def make_plan(
    manifest_path: Path, source_root: Path, destination_root: Path
) -> dict[str, Any]:
    manifest = _json_file(manifest_path)
    if manifest.get("format") != MANIFEST_FORMAT:
        raise ValueError(f"manifest format must be {MANIFEST_FORMAT}")
    raw = manifest.get("files")
    if not isinstance(raw, list):
        # Keep ValueError for existing CLI and library callers.
        raise ValueError("manifest files must be an array of relative paths")  # noqa: TRY004
    paths = _logical_paths(raw)
    source = _root(source_root, "source")
    destination = destination_root.resolve(strict=False)
    _roots_disjoint(source, destination)
    records = []
    for relative in paths:
        src = _check_no_symlinks(source, relative, allow_missing=False)
        if not src.is_file():
            raise ValueError(f"source is not a regular file: {src}")
        dest = _check_no_symlinks(destination, relative, allow_missing=True)
        if dest.exists() and not dest.is_file():
            raise ValueError(f"destination is not a regular file: {dest}")
        records.append(
            {
                "path": relative,
                "source_sha256": sha256(src.read_bytes()),
                "destination_sha256": sha256(dest.read_bytes())
                if dest.exists()
                else None,
            }
        )
    return {
        "format": PLAN_FORMAT,
        "source_root": str(source),
        "destination_root": str(destination),
        "files": records,
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def _digest_or_none(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected regular file or absence at {path}")
    return sha256(path.read_bytes())


def _validate_plan(plan: dict[str, Any]) -> tuple[Path, Path, list[dict[str, Any]]]:
    if plan.get("format") != PLAN_FORMAT:
        raise ValueError(f"plan format must be {PLAN_FORMAT}")
    source_value = plan.get("source_root")
    destination_value = plan.get("destination_root")
    if not isinstance(source_value, str) or not source_value:
        raise ValueError("plan source_root must be a non-empty path")
    if not isinstance(destination_value, str) or not destination_value:
        raise ValueError("plan destination_root must be a non-empty path")
    source_path = Path(source_value)
    destination_path = Path(destination_value)
    for label, root_path in (
        ("source", source_path),
        ("destination", destination_path),
    ):
        if not root_path.is_absolute():
            raise ValueError(f"plan {label}_root must be absolute")
        anchor = Path(root_path.anchor)
        relative_root = root_path.relative_to(anchor).as_posix()
        _check_no_symlinks(anchor, relative_root, allow_missing=True)
    source = source_path.resolve(strict=False)
    destination = destination_path.resolve(strict=False)
    raw = plan.get("files")
    if not isinstance(raw, list) or not raw:
        raise ValueError("plan files must be a non-empty array")
    paths = _logical_paths(
        [item.get("path") if isinstance(item, dict) else None for item in raw]
    )
    if paths != [item.get("path") for item in raw]:
        raise ValueError("plan files must be sorted and have unique paths")
    for item in raw:
        if (
            not isinstance(item.get("source_sha256"), str)
            or len(item["source_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in item["source_sha256"])
        ):
            raise ValueError(f"invalid source hash for {item['path']}")
        if item.get("destination_sha256") is not None and (
            not isinstance(item["destination_sha256"], str)
            or len(item["destination_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in item["destination_sha256"])
        ):
            raise ValueError(f"invalid destination hash for {item['path']}")
    _roots_disjoint(source, destination)
    return source, destination, raw


def verify_plan(plan_path: Path) -> dict[str, Any]:
    """Read a plan and report whether each source is deployed at its destination."""
    plan = _json_file(plan_path)
    source, destination, files = _validate_plan(plan)
    results = []
    counts = {"match": 0, "drift": 0, "missing": 0}
    for item in files:
        relative = item["path"]
        source_path = _check_no_symlinks(source, relative, allow_missing=True)
        destination_path = _check_no_symlinks(destination, relative, allow_missing=True)
        source_hash = _digest_or_none(source_path)
        destination_hash = _digest_or_none(destination_path)

        if source_hash is None or destination_hash is None:
            status = "missing"
            if source_hash is None:
                detail = "source_missing"
            else:
                detail = "destination_missing"
        elif source_hash != item["source_sha256"]:
            status = "drift"
            detail = "source_drift"
        elif destination_hash != item["source_sha256"]:
            status = "drift"
            detail = "destination_drift"
        else:
            status = "match"
            detail = "deployed_bytes_match_planned_source"

        counts[status] += 1
        results.append(
            {
                "path": relative,
                "status": status,
                "detail": detail,
                "expected_source_sha256": item["source_sha256"],
                "current_source_sha256": source_hash,
                "current_destination_sha256": destination_hash,
            }
        )

    return {
        "format": "codex-devtools-deployment-verification-v1",
        "plan": str(plan_path),
        "passed": counts["drift"] == 0 and counts["missing"] == 0,
        "counts": {**counts, "total": len(results)},
        "files": results,
    }


def _rollback_entries(receipt: dict[str, Any], receipt_path: Path) -> None:
    destination = Path(receipt["destination_root"]).resolve(strict=False)
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("receipt files must be a non-empty array")
    paths = _logical_paths(
        [item.get("path") if isinstance(item, dict) else None for item in files]
    )
    if paths != [item.get("path") for item in files]:
        raise ValueError("receipt files must be sorted and have unique paths")
    for item in files:
        expected_backup = (
            f"backup-{files.index(item):04d}.bin"
            if item.get("destination_sha256") is not None
            else None
        )
        if item.get("backup") != expected_backup:
            raise ValueError(f"invalid backup reference for {item['path']}")
        if item.get("existed") != (expected_backup is not None):
            raise ValueError(f"invalid prior destination state for {item['path']}")
    _roots_disjoint(
        Path(receipt["source_root"]).resolve(strict=False),
        destination,
        receipt_path.parent,
    )
    failures = []
    for item in reversed(files):
        relative = item["path"]
        try:
            target = _check_no_symlinks(destination, relative, allow_missing=True)
            current = _digest_or_none(target)
            old_hash = item["destination_sha256"]
            deployed_hash = item["source_sha256"]
            if current == old_hash:
                item["state"] = "restored"
                _atomic_json(receipt_path, receipt)
                continue
            if current != deployed_hash:
                raise ValueError(
                    f"refusing rollback; destination changed after deployment: {target}"
                )
            if item["existed"]:
                backup = receipt_path.parent / item["backup"]
                if _digest_or_none(backup) != old_hash:
                    raise ValueError(f"backup missing or changed: {backup}")
                _atomic_write(target, backup.read_bytes())
            else:
                target.unlink()
            item["state"] = "restored"
            _atomic_json(receipt_path, receipt)
        except (OSError, ValueError) as exc:
            failures.append(f"{relative}: {exc}")
    receipt["status"] = "recovery_required" if failures else "rolled_back"
    receipt["rollback_errors"] = failures
    _atomic_json(receipt_path, receipt)
    if failures:
        raise ValueError("rollback incomplete; see receipt: " + "; ".join(failures))


def apply_plan(plan_path: Path, backup_root: Path) -> Path:
    plan = _json_file(plan_path)
    source, destination, files = _validate_plan(plan)
    if not source.is_dir() or not destination.is_dir() and destination.exists():
        raise ValueError("source or destination root is not a directory")
    backup = backup_root.resolve(strict=False)
    _roots_disjoint(source, destination, backup)
    if backup.exists() and (not backup.is_dir() or any(backup.iterdir())):
        raise FileExistsError(f"backup root must be absent or empty: {backup}")
    # Validate every source and destination before making any writes.
    targets = []
    for item in files:
        relative = item["path"]
        src = _check_no_symlinks(source, relative, allow_missing=False)
        if not src.is_file() or sha256(src.read_bytes()) != item["source_sha256"]:
            raise ValueError(f"source changed since plan: {relative}")
        dest = _check_no_symlinks(destination, relative, allow_missing=True)
        current_hash = _digest_or_none(dest)
        if current_hash != item["destination_sha256"]:
            raise ValueError(f"destination changed since plan: {relative}")
        targets.append((item, src, dest))
    backup.mkdir(parents=True, exist_ok=True)
    receipt_path = backup / "receipt.json"
    receipt = {
        "format": RECEIPT_FORMAT,
        "status": "prepared",
        "source_root": str(source),
        "destination_root": str(destination),
        "files": [],
    }
    receipt_written = False
    try:
        # Stage all prior versions before the first destination change.
        for index, (item, _src, dest) in enumerate(targets):
            existed = dest.exists()
            backup_name = f"backup-{index:04d}.bin" if existed else None
            if existed:
                _atomic_write(backup / backup_name, dest.read_bytes())
                if (
                    sha256((backup / backup_name).read_bytes())
                    != item["destination_sha256"]
                ):
                    raise ValueError(f"backup verification failed: {item['path']}")
            receipt["files"].append(
                {**item, "existed": existed, "backup": backup_name, "state": "pending"}
            )
        receipt["status"] = "applying"
        _atomic_json(receipt_path, receipt)
        receipt_written = True
        for (item, src, dest), entry in zip(targets, receipt["files"]):
            # Recheck immediately before replacement to catch edits during the batch.
            source_bytes = src.read_bytes()
            if sha256(source_bytes) != item["source_sha256"]:
                raise ValueError(f"source changed during deployment: {item['path']}")
            if _digest_or_none(dest) != item["destination_sha256"]:
                raise ValueError(
                    f"destination changed during deployment: {item['path']}"
                )
            # Write the exact bytes that passed the hash check; do not reopen a
            # mutable source between validation and replacement.
            _atomic_write(dest, source_bytes)
            entry["state"] = "applied"
            _atomic_json(receipt_path, receipt)
        receipt["status"] = "applied"
        _atomic_json(receipt_path, receipt)
        return receipt_path
    except BaseException as exc:
        if receipt_written and receipt.get("files"):
            receipt["status"] = "apply_failed_rolling_back"
            receipt["apply_error"] = str(exc)
            _atomic_json(receipt_path, receipt)
            # Roll back after interrupts as well as ordinary errors.
            try:
                _rollback_entries(receipt, receipt_path)
            except BaseException as rollback_exc:  # noqa: BLE001
                raise ValueError(
                    f"deployment failed ({exc}); rollback needs recovery ({rollback_exc}); receipt: {receipt_path}"
                ) from exc
        elif backup.exists():
            shutil.rmtree(backup)
        raise


def rollback(receipt_path: Path) -> None:
    receipt = _json_file(receipt_path)
    if receipt.get("format") != RECEIPT_FORMAT:
        raise ValueError(f"receipt format must be {RECEIPT_FORMAT}")
    if receipt.get("status") not in (
        "applied",
        "applying",
        "recovery_required",
        "apply_failed_rolling_back",
    ):
        raise ValueError(
            f"receipt is not rollbackable (status: {receipt.get('status')})"
        )
    _rollback_entries(receipt, receipt_path.resolve())
