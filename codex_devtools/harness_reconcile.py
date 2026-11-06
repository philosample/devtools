"""Audited operator assertion to retire one unloaded, fenced or expired-ready owner."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import harness_control as client
from .runtime import discover_node

HELPER_SHA256 = "0b7e943d89892dd3f1184a28c31316d901f850bac60973bbd3958516ff29b668"
HERE = Path(__file__).parent


def _decode(data: bytes):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate reconciliation JSON field")
            value[key] = item
        return value

    def nonfinite(_value):
        raise ValueError("nonfinite reconciliation JSON value")

    return json.loads(data, object_pairs_hook=unique, parse_constant=nonfinite)


def _safe(path: Path, *, directory: bool = False) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute paths without parent traversal required")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (ancestor / ".git").exists():
            raise ValueError("symlink or Git evidence/mailbox path")
    info = path.stat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected_type(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("path must be private, current-user owned and regular")


def _bytes(path: Path) -> bytes:
    _safe(path)
    with path.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("oversized reconciliation evidence member")
    return data


def _write(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _json(path: Path, value: dict) -> None:
    _write(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode())


def _tree(directory: Path) -> dict[str, bytes]:
    _safe(directory, directory=True)
    result = {}
    for entry in sorted(directory.iterdir()):
        if entry.name == "actor":
            _safe(entry, directory=True)
            if {p.name for p in entry.iterdir()} != {"identity.json"}:
                raise ValueError("unexpected actor members")
            result["actor/identity.json"] = _bytes(entry / "identity.json")
        elif entry.name in ("descriptor.json", "command.json", "identity.json") or (
            entry.name.startswith("ack.") and entry.name.endswith(".json")
        ):
            result[entry.name] = _bytes(entry)
        else:
            raise ValueError("unexpected reconciliation evidence member")
    if len(result) > 40:
        raise ValueError("too many reconciliation evidence members")
    return result


def _snapshot(
    root: Path, descriptor: Path, pins: dict
) -> tuple[dict, dict[str, bytes]]:
    for directory in (root, descriptor.parent, root / "owner"):
        _safe(directory, directory=True)
    _decode(_bytes(descriptor))
    current = client.status(descriptor)
    if current["pins"] != pins:
        raise ValueError("exact expected pins required")
    identity = {key: current[key] for key in ("format", "instance", "nonce", "pins")}
    owner = _tree(root / "owner")
    if set(owner) != {"identity.json"} or _decode(owner["identity.json"]) != identity:
        raise ValueError("root owner does not match selected exact identity")
    instance = _tree(descriptor.parent)
    _retirement_basis(current, instance)
    files = {"owner/" + k: v for k, v in owner.items()}
    files.update({current["instance"] + "/" + k: v for k, v in instance.items()})
    return identity, files


def _retirement_basis(current: dict, instance: dict[str, bytes]) -> str:
    if current["state"] == "fenced":
        return "fenced"
    if (
        current["state"] == "ready"
        and current["pending"] is None
        and not current["lease_current"]
    ):
        if "command.json" in instance or "actor/identity.json" in instance:
            raise ValueError("expired READY owner has unresolved command/actor")
        return "expired_ready_no_pending"
    raise ValueError(
        "fenced owner or expired READY owner with no pending command required"
    )


def reconcile(
    descriptor: Path,
    expected_pins: Path,
    evidence_output: Path,
    *,
    assert_target_unloaded: bool,
    reason: str,
    operator_evidence: str,
) -> dict:
    if not assert_target_unloaded:
        raise ValueError("explicit --assert-target-unloaded required")
    for value in (reason, operator_evidence):
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ValueError("nonempty bounded reason and operator evidence required")
    _safe(descriptor)
    if descriptor.name != "descriptor.json":
        raise ValueError("select exact descriptor.json")
    root = descriptor.parent.parent
    pin_bytes = _bytes(expected_pins)
    pins = _decode(pin_bytes)
    identity, before = _snapshot(root, descriptor, pins)
    retirement_basis = _retirement_basis(
        client.status(descriptor), _tree(descriptor.parent)
    )
    _safe(evidence_output.parent, directory=True)
    if evidence_output.exists() or evidence_output.is_symlink():
        raise ValueError("evidence output must be a new directory")
    if root == evidence_output or root in evidence_output.parents:
        raise ValueError("evidence must be outside mailbox root")
    helper = HERE / "harness_mailbox.cjs"
    if hashlib.sha256(helper.read_bytes()).hexdigest() != HELPER_SHA256:
        raise ValueError("unreviewed harness helper")
    runtime = discover_node()
    if not runtime["available"]:
        raise ValueError("actual Node prerequisite unavailable")
    actor = root / "reconciliation-actor"
    actor.mkdir(mode=0o700)  # Never clear another operator's actor, even after a crash.
    attempt = str(uuid.uuid4())
    actor_identity = {"attempt": attempt, "old_identity": identity}
    _json(actor / "identity.json", actor_identity)
    # Every failure after admission leaves the reconciliation actor as a fence.
    evidence_output.mkdir(mode=0o700)
    before_dir = evidence_output / "before"
    before_dir.mkdir(mode=0o700)
    for name, data in before.items():
        destination = before_dir / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write(destination, data)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in before.items()}
    intent = {
        "schema": "codex-harness-reconciliation/v1",
        "attempt": attempt,
        "at": datetime.now(timezone.utc).isoformat(),
        "operator_uid": os.getuid(),
        "assertion": "target-unloaded",
        "retirement_basis": retirement_basis,
        "reason": reason,
        "operator_evidence": operator_evidence,
        "root": str(root),
        "descriptor": str(descriptor),
        "identity": identity,
        "before_sha256": hashes,
        "helper_sha256": HELPER_SHA256,
        "expected_pins_sha256": hashlib.sha256(pin_bytes).hexdigest(),
        "node": runtime,
        "claim": "operator assertion only; no liveness, unload or domain proof",
        "old_command_outcome": "unchanged; no completion or cancellation asserted",
    }
    _write(evidence_output / "expected-pins.json", pin_bytes)
    _json(evidence_output / "intent.json", intent)
    try:
        if _bytes(expected_pins) != pin_bytes:
            raise ValueError("independent expected pins changed after snapshot")
        if _snapshot(root, descriptor, pins) != (identity, before):
            raise ValueError("old evidence changed after snapshot")
        result = subprocess.run(
            [
                runtime["path"],
                str(HERE / "harness_reconcile.cjs"),
                str(evidence_output),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        _write(evidence_output / "node.stdout", result.stdout.encode())
        _write(evidence_output / "node.stderr", result.stderr.encode())
        if result.returncode:
            raise ValueError(
                "reconciliation runner refused or failed; inspect evidence"
            )
        marker = client._read(evidence_output / "marker.json")
        runner = client._read(evidence_output / "runner.json")
        if (
            runner.get("status") != "closed"
            or runner.get("callback_count") != 0
            or runner.get("retirement_basis") != retirement_basis
        ):
            raise ValueError("inert marker dispatch or incomplete runner")
        if client._read(Path(runner["descriptor"])) != marker:
            raise ValueError("saved marker descriptor changed")
        if marker["state"] != "closed" or marker["pending"] is not None:
            raise ValueError("inert marker not closed")
        if (root / "owner").exists():
            raise ValueError("root ownership not released; effects unknown")
        # Old descriptor timestamps may change if an asserted-unloaded receiver
        # was still polling. Require the complete exact saved old evidence.
        after_instance = _tree(descriptor.parent)
        after = {identity["instance"] + "/" + k: v for k, v in after_instance.items()}
        expected = {k: v for k, v in before.items() if not k.startswith("owner/")}
        if after != expected:
            raise ValueError("old instance changed; effects unknown")
        outcome = {
            "status": "retired",
            "retirement_basis": retirement_basis,
            "marker": marker,
            "marker_callback_count": runner["callback_count"],
            "marker_sha256": hashlib.sha256(
                _bytes(Path(runner["descriptor"]))
            ).hexdigest(),
            "after_sha256": {
                k: hashlib.sha256(v).hexdigest() for k, v in after.items()
            },
            "old_command_outcome": intent["old_command_outcome"],
            "claim": intent["claim"],
        }
        # Check only our actor identity before releasing our cooperative lock.
        if _decode(_bytes(actor / "identity.json")) != actor_identity:
            raise ValueError("reconciliation actor changed; effects unknown")
        _json(evidence_output / "outcome.json", outcome)
        (actor / "identity.json").unlink()
        actor.rmdir()
        return {**outcome, "evidence": str(evidence_output)}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # An uncertain runner may have dropped ownership before failing. Restore
        # only by exclusive creation; never displace an existing/racing owner.
        owner_fence = "existing owner retained"
        try:
            (root / "owner").mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            owner_fence = "could not establish owner fence; inspect manually"
        else:
            try:
                _json(root / "owner/identity.json", identity)
                owner_fence = "old-identity owner fence restored; effects still unknown"
            except OSError:
                owner_fence = "empty exclusive owner fence; inspect manually"
        post_hashes = {}
        try:
            for prefix, directory in (
                ("owner", root / "owner"),
                (identity["instance"], descriptor.parent),
            ):
                post_hashes.update(
                    {
                        prefix + "/" + k: hashlib.sha256(v).hexdigest()
                        for k, v in _tree(directory).items()
                    }
                )
        except (OSError, ValueError):
            pass
        failure = {
            "status": "unknown",
            "detail": str(error),
            "owner_fence": owner_fence,
            "after_sha256": post_hashes,
            "old_command_outcome": intent["old_command_outcome"],
        }
        destination = (
            "failure.json"
            if (evidence_output / "outcome.json").exists()
            else "outcome.json"
        )
        _json(evidence_output / destination, failure)
        raise ValueError(
            f"{error}; actor retained, do not retry; evidence: {evidence_output}"
        ) from error
