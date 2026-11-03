"""Local opt-in test-harness mailbox client; never controls a host directly."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import time
import uuid
from pathlib import Path

FORMAT = "codex-harness-mailbox-v1"
_UUID_V4 = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}"
)


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid_commands(commands: object) -> bool:
    if not isinstance(commands, dict) or not 1 <= len(commands) <= 16:
        return False
    for name, schema in commands.items():
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name) is None
            or not isinstance(schema, dict)
            or len(schema) > 32
        ):
            return False
        for key, rule in schema.items():
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,31}", key) is None
                or not isinstance(rule, dict)
            ):
                return False
            if set(rule) == {"type", "min", "max"}:
                if (
                    rule["type"] not in ("number", "integer")
                    or type(rule["type"]) is not str
                    or not _finite_number(rule["min"])
                    or not _finite_number(rule["max"])
                    or rule["min"] > rule["max"]
                ):
                    return False
            elif set(rule) == {"enum"}:
                values = rule["enum"]
                if (
                    not isinstance(values, list)
                    or not 1 <= len(values) <= 32
                    or any(
                        not isinstance(value, str)
                        or len(value.encode("utf-16-le", "surrogatepass")) // 2 > 128
                        for value in values
                    )
                ):
                    return False
            elif set(rule) == {"type"}:
                if rule["type"] not in ("boolean", "uuid"):
                    return False
            else:
                return False
    return True


def _valid_arguments(arguments: object, schema: dict) -> bool:
    if not isinstance(arguments, dict) or set(arguments) != set(schema):
        return False
    for key, rule in schema.items():
        value = arguments[key]
        if "enum" in rule:
            if not isinstance(value, str) or value not in rule["enum"]:
                return False
        elif rule.get("type") == "boolean":
            if type(value) is not bool:
                return False
        elif rule.get("type") == "uuid":
            if not isinstance(value, str) or _UUID_V4.fullmatch(value) is None:
                return False
        elif (
            not _finite_number(value)
            or not rule["min"] <= value <= rule["max"]
            or (
                rule["type"] == "integer"
                and type(value) is float
                and not value.is_integer()
            )
        ):
            return False
    return True


def _read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("unsafe or oversized mailbox file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("invalid mailbox JSON object")  # noqa: TRY004
    return value


def _private(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("symlink mailbox ancestor")
        if (parent / ".git").exists():
            raise ValueError("mailbox must be outside Git")
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("mailbox directory must be private and owned")


def status(descriptor: Path) -> dict:
    descriptor = descriptor.absolute()
    if descriptor.name != "descriptor.json":
        raise ValueError("select an exact descriptor.json")
    _private(descriptor.parent)
    _private(descriptor.parent.parent)
    value = _read(descriptor)
    fields = {
        "format",
        "instance",
        "nonce",
        "pins",
        "commands",
        "sequence",
        "state",
        "pending",
        "expires_at_ms",
        "timeout_ms",
    }
    try:
        valid = (
            set(value) == fields
            and value["format"] == FORMAT
            and value["instance"] == descriptor.parent.name
            and str(uuid.UUID(value["instance"])) == value["instance"]
            and re.fullmatch(r"[a-f0-9]{64}", value["nonce"]) is not None
            and type(value["sequence"]) is int
            and value["sequence"] >= 0
            and value["state"] in ("ready", "pending", "fenced", "closed")
            and type(value["expires_at_ms"]) in (int, float)
            and math.isfinite(value["expires_at_ms"])
            and type(value["timeout_ms"]) is int
            and 100 <= value["timeout_ms"] <= 60000
            and _valid_commands(value["commands"])
            and isinstance(value["pins"], dict)
            and set(value["pins"]) == {"artifact_sha256", "components"}
            and re.fullmatch(r"[a-f0-9]{64}", value["pins"]["artifact_sha256"])
            and isinstance(value["pins"]["components"], dict)
            and 1 <= len(value["pins"]["components"]) <= 64
            and all(
                re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", key)
                and re.fullmatch(r"[a-f0-9]{64}", pin)
                for key, pin in value["pins"]["components"].items()
            )
            and (
                value["pending"] is None
                or (
                    isinstance(value["pending"], dict)
                    and set(value["pending"]) == {"request", "sequence"}
                    and str(uuid.UUID(value["pending"]["request"]))
                    == value["pending"]["request"]
                    and value["pending"]["sequence"] == value["sequence"]
                )
            )
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("invalid descriptor identity or schema")
    return {
        "descriptor": str(descriptor),
        "lease_current": value["expires_at_ms"] > time.time() * 1000,
        "liveness_claim": "timestamp freshness only; process liveness unproven",
        **value,
    }


def discover(root: Path) -> list[dict]:
    _private(root.absolute())
    return [status(p) for p in sorted(root.glob("*/descriptor.json"))]


def _atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + str(uuid.uuid4()) + ".tmp")
    try:
        with temporary.open("x") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def submit(
    descriptor: Path, pins: dict, command: str, arguments: dict, wait: float = 5
) -> dict:
    if not 0 < wait <= 60:
        raise ValueError("wait must be in (0, 60] seconds")
    initial = status(descriptor)
    if initial["pins"] != pins:
        raise ValueError("expected artifact/component pins mismatch")
    if command not in initial["commands"]:
        raise ValueError("command not advertised")
    if not _valid_arguments(arguments, initial["commands"][command]):
        raise ValueError("arguments do not match advertised schema")
    if len(json.dumps(arguments, allow_nan=False)) > 16384:
        raise ValueError("arguments exceed transport bound")
    directory = descriptor.absolute().parent
    actor = directory / "actor"
    request = str(uuid.uuid4())
    actor.mkdir(mode=0o700)  # Exclusive: one actor, never auto-break a stale lock.
    published = False
    try:
        _atomic(actor / "identity.json", {"request": request})
        current = status(descriptor)
        for key in ("instance", "nonce", "pins", "commands"):
            if current[key] != initial[key]:
                raise ValueError("descriptor changed while acquiring actor")
        if not current["lease_current"] or current["state"] != "ready":
            raise ValueError("receiver stale, pending, closed or fenced")
        identity = {k: current[k] for k in ("format", "instance", "nonce", "pins")}
        sequence = current["sequence"] + 1
        envelope = {
            **identity,
            "request": request,
            "sequence": sequence,
            "command": command,
            "args": arguments,
            "deadline_ms": time.time() * 1000 + current["timeout_ms"],
        }
        if (directory / "command.json").exists():
            raise ValueError("unresolved command exists; reconciliation required")
        _atomic(directory / "command.json", envelope)
        published = True
        until = time.monotonic() + wait
        while time.monotonic() < until:
            ack_path = directory / f"ack.{request}.json"
            if ack_path.exists():
                ack = _read(ack_path)
                expected = {**identity, "request": request, "sequence": sequence}
                if any(ack.get(k) != v for k, v in expected.items()):
                    raise ValueError("ACK correlation mismatch; effects unknown")
                if ack.get("status") not in ("completed", "failed", "unknown_effect"):
                    raise ValueError("invalid terminal ACK")
                return ack
            time.sleep(0.01)
        current = status(descriptor)
        correlated = current.get("pending") == {
            "request": request,
            "sequence": sequence,
        }
        return {
            **identity,
            "request": request,
            "sequence": sequence,
            "status": "enqueued" if correlated else "unconfirmed",
            "detail": "wait expired; no completion claim, no replay or unlock",
        }
    finally:
        if not published:
            # Only this unpublished actor can remove its lock. Once published,
            # the receiver owns release; old clients never unlock a later actor.
            (actor / "identity.json").unlink(missing_ok=True)
            actor.rmdir()


def register_cli(commands) -> None:
    group = commands.add_parser("harness", help="opt-in local test-harness mailboxes")
    actions = group.add_subparsers(dest="action", required=True)
    find = actions.add_parser(
        "discover", help="read descriptors, never select a target"
    )
    find.add_argument("--root", required=True, type=Path)
    show = actions.add_parser("status", help="read one exact descriptor")
    show.add_argument("--descriptor", required=True, type=Path)
    send = actions.add_parser(
        "submit", help="submit one capability to an exact harness"
    )
    send.add_argument("--descriptor", required=True, type=Path)
    send.add_argument("--expected-pins", required=True, type=Path)
    send.add_argument("--command", required=True)
    send.add_argument("--args", required=True, help="JSON object of bounded arguments")
    send.add_argument("--wait", type=float, default=5)
    retire = actions.add_parser(
        "reconcile", help="audit explicit unloaded-target retirement"
    )
    retire.add_argument("--descriptor", required=True, type=Path)
    retire.add_argument("--expected-pins", required=True, type=Path)
    retire.add_argument("--assert-target-unloaded", action="store_true", required=True)
    retire.add_argument("--reason", required=True)
    retire.add_argument("--operator-evidence", required=True)
    retire.add_argument("--evidence-output", required=True, type=Path)


def run_cli(args) -> tuple[object, int]:
    if args.action == "discover":
        return discover(args.root), 0
    if args.action == "status":
        return status(args.descriptor), 0
    if args.action == "reconcile":
        from .harness_reconcile import reconcile

        return reconcile(
            args.descriptor,
            args.expected_pins,
            args.evidence_output,
            assert_target_unloaded=args.assert_target_unloaded,
            reason=args.reason,
            operator_evidence=args.operator_evidence,
        ), 0
    result = submit(
        args.descriptor,
        _read(args.expected_pins),
        args.command,
        json.loads(args.args),
        args.wait,
    )
    return result, 0 if result["status"] == "completed" else 1
