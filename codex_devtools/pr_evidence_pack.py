"""Assemble explicit local check receipts into a PR evidence declaration."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from . import pr_readiness

PACK_SCHEMA = "codex-devtools-pr-evidence-pack-v1"
PACK_KEYS = {"schema", "receipts"}
PACK_ROW_KEYS = {"check_id", "receipt_ref", "owner", "next_action", "limitations"}


def _plan_bytes(path: Path) -> bytes:
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("persisted plan is not a regular file")
        if metadata.st_size > pr_readiness.MAX_INPUT_BYTES:
            raise ValueError("persisted plan exceeds input size limit")
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read persisted plan: {path}") from exc
    if len(content) > pr_readiness.MAX_INPUT_BYTES:
        raise ValueError("persisted plan exceeds input size limit")
    return content


def _manifest_rows(value: Any) -> list[dict[str, Any]]:
    manifest = pr_readiness._closed(value, PACK_KEYS, "evidence pack manifest")
    if manifest["schema"] != PACK_SCHEMA:
        raise ValueError("unsupported evidence pack manifest schema")
    rows = manifest["receipts"]
    if not isinstance(rows, list) or len(rows) > pr_readiness.MAX_ITEMS:
        raise ValueError("evidence pack receipts must be a bounded array")
    return [
        pr_readiness._closed(row, PACK_ROW_KEYS, f"evidence pack receipts[{index}]")
        for index, row in enumerate(rows)
    ]


def _build_evidence(
    policy: dict[str, Any], manifest: Any, receipt_root: Path
) -> dict[str, Any]:
    declared_checks = {row["id"]: row for row in policy["checks"]}
    manifest_rows = _manifest_rows(manifest)
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(manifest_rows):
        check_id = pr_readiness._text(item["check_id"], f"receipt[{index}].check_id")
        if check_id not in declared_checks:
            raise ValueError(f"unknown evidence check id: {check_id}")
        if check_id in by_id:
            raise ValueError(f"duplicate evidence check id: {check_id}")
        reference = item["receipt_ref"]
        raw = pr_readiness._receipt_bytes(receipt_root, reference)
        try:
            receipt = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=pr_readiness._duplicate_free,
                parse_constant=pr_readiness._reject_constant,
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid receipt JSON for {check_id}") from exc
        receipt = pr_readiness._closed(
            receipt, pr_readiness.RECEIPT_KEYS, f"receipt {check_id}"
        )
        if receipt["check_id"] != check_id:
            raise ValueError(f"receipt check id differs from manifest: {check_id}")
        owner = pr_readiness._text(item["owner"], f"receipt[{index}].owner", empty=True)
        next_action = pr_readiness._text(
            item["next_action"], f"receipt[{index}].next_action", empty=True
        )
        limitations = item["limitations"]
        if (
            not isinstance(limitations, list)
            or len(limitations) > pr_readiness.MAX_ITEMS
        ):
            raise ValueError(f"receipt[{index}].limitations must be a bounded array")
        for limit_index, limitation in enumerate(limitations):
            pr_readiness._text(
                limitation, f"receipt[{index}].limitations[{limit_index}]"
            )
        evidence_row = {
            key: receipt[key]
            for key in (
                "check_id",
                "level",
                "base_sha",
                "head_sha",
                "component_pins",
                "command",
                "environment",
                "result",
                "exit_code",
                "test_count",
            )
        }
        evidence_row.update(
            {
                "receipt_ref": reference,
                "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                "limitations": limitations,
                "owner": owner,
                "next_action": next_action,
            }
        )
        pr_readiness._validate_receipt(receipt, evidence_row, declared_checks[check_id])
        by_id[check_id] = evidence_row

    missing = [
        check["id"]
        for check in policy["checks"]
        if check["required_for_merge"] and check["id"] not in by_id
    ]
    if missing:
        raise ValueError(f"missing required receipts: {', '.join(missing)}")
    return {
        "schema": pr_readiness.EVIDENCE_SCHEMA,
        "rows": [
            by_id[check["id"]] for check in policy["checks"] if check["id"] in by_id
        ],
    }


def _write_new_atomic(path: Path, content: bytes) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=".evidence-pack-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def pack(
    policy_path: Path, manifest_path: Path, plan_path: Path, output_path: Path
) -> dict[str, Any]:
    """Write one new evidence document from an explicit closed receipt list."""
    policy, _policy_bytes = pr_readiness._read_json(policy_path)
    manifest, _manifest_bytes = pr_readiness._read_json(manifest_path)
    plan_content = _plan_bytes(plan_path)
    manifest_path = manifest_path.resolve()
    output_candidate = output_path.expanduser()
    if output_candidate.is_symlink() or ".." in output_candidate.parts:
        raise ValueError("evidence output path is unsafe")
    if output_candidate.parent.resolve() != manifest_path.parent:
        raise ValueError("evidence output must be beside the manifest receipt tree")
    output_path = manifest_path.parent / output_candidate.name
    if output_path.name in ("", ".", ".."):
        raise ValueError("evidence output path is unsafe")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite evidence output: {output_path}")
    try:
        pr_readiness._validate_policy(
            policy,
            {"schema": pr_readiness.EVIDENCE_SCHEMA, "rows": []},
            plan_content,
        )
        evidence = _build_evidence(policy, manifest, manifest_path.parent)
        pr_readiness._validate_policy(policy, evidence, plan_content)
    except TypeError as exc:
        raise ValueError("policy, manifest, or receipt has an invalid schema") from exc
    encoded = (
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    if len(encoded) > pr_readiness.MAX_INPUT_BYTES:
        raise ValueError("assembled evidence exceeds input size limit")
    _write_new_atomic(output_path, encoded)
    return {
        "schema": "codex-devtools-pr-evidence-pack-report-v1",
        "command": "evidence-pack",
        "written": str(output_path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "receipt_count": len(evidence["rows"]),
        "readiness": "not evaluated; run pr plan-check",
        "authentication": "not established",
    }
