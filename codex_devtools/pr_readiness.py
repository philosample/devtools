"""Read-only PR policy/evidence checks; coordinator approval stays fail-closed."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

POLICY_SCHEMA = "codex-devtools-pr-policy-v1"
EVIDENCE_SCHEMA = "codex-devtools-pr-evidence-v1"
REPORT_SCHEMA = "codex-devtools-pr-readiness-v1"
MAX_INPUT_BYTES = 1_048_576
MAX_ITEMS = 128
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
LEVELS = ("development", "development_integration")
RESULTS = ("passed", "failed", "skipped", "unknown")
REVIEW_DECISIONS = ("", "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED")
CHECK_BUCKETS = ("pass", "fail", "pending", "skipping", "cancel")
CHECK_FIELDS = (
    "name",
    "bucket",
    "state",
    "workflow",
    "link",
    "startedAt",
    "completedAt",
)
MAX_REVIEW_THREAD_PAGES = 10
REVIEW_THREAD_PAGE_SIZE = 100
REVIEW_THREAD_QUERY = (
    "query PRReadinessReviewThreads($owner:String!,$name:String!,$number:Int!,"
    "$after:String){repository(owner:$owner,name:$name){pullRequest(number:$number){"
    "id headRefOid baseRefOid state reviewThreads(first:100,after:$after){"
    "nodes{id isResolved} pageInfo{hasNextPage endCursor}}}}}"
)
POLICY_KEYS = {
    "schema",
    "repository",
    "pr_number",
    "pull_request",
    "plan",
    "component_pins",
    "test_decision",
    "checks",
    "holds",
}
PR_KEYS = {"base_sha", "head_sha", "body_sha256"}
PLAN_KEYS = {"id", "ref", "sha256"}
DECISION_KEYS = {
    "coordinator_chat_id",
    "decision_ref",
    "rationale",
    "levels",
}
LEVEL_KEYS = {"required", "rationale"}
CHECK_KEYS = {"id", "level", "kind", "required_for_merge"}
HOLD_KEYS = {"id", "state", "reason", "owner", "next_action"}
EVIDENCE_KEYS = {"schema", "rows"}
ROW_KEYS = {
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
    "receipt_ref",
    "receipt_sha256",
    "limitations",
    "owner",
    "next_action",
}


def _duplicate_free(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _read_json(path: Path) -> tuple[Any, bytes]:
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"input is not a regular file: {path}")
        if metadata.st_size > MAX_INPUT_BYTES:
            raise ValueError(f"input file exceeds {MAX_INPUT_BYTES} bytes: {path}")
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read input file: {path}") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError(f"input file exceeds {MAX_INPUT_BYTES} bytes: {path}")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_duplicate_free,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid UTF-8 JSON: {path}") from exc
    return value, raw


def _closed(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    missing = keys - value.keys()
    extra = value.keys() - keys
    if missing or extra:
        raise ValueError(
            f"{label} fields differ (missing={sorted(missing)}, unknown={sorted(extra)})"
        )
    return value


def _text(value: Any, label: str, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or (not empty and not value.strip())
    ):
        raise ValueError(f"{label} must be a bounded non-empty string")
    return value


def _digest(value: Any, label: str, *, short: bool = False) -> str:
    pattern = SHA1 if short else SHA256
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(
            f"{label} must be a lowercase SHA-{1 if short else 256} digest"
        )
    return value


def _string_map(
    value: Any, label: str, *, digest_values: bool = False
) -> dict[str, str]:
    if not isinstance(value, dict) or not value or len(value) > MAX_ITEMS:
        raise ValueError(f"{label} must be a non-empty bounded object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not IDENTIFIER.fullmatch(key):
            raise ValueError(f"{label} has an invalid key")
        result[key] = (
            _digest(item, f"{label}.{key}")
            if digest_values
            else _text(item, f"{label}.{key}")
        )
    return result


def _validate_policy(
    policy: Any, evidence: Any, plan_bytes: bytes
) -> list[dict[str, str]]:
    policy = _closed(policy, POLICY_KEYS, "policy")
    if policy["schema"] != POLICY_SCHEMA:
        raise ValueError("unsupported policy schema")
    repository = _text(policy["repository"], "policy.repository")
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("policy.repository must be owner/name")
    if (
        type(policy["pr_number"]) is not int
        or not 1 <= policy["pr_number"] <= 2**31 - 1
    ):
        raise ValueError("policy.pr_number must be a positive bounded integer")

    pull_request = _closed(policy["pull_request"], PR_KEYS, "policy.pull_request")
    base_sha = _digest(
        pull_request["base_sha"], "policy.pull_request.base_sha", short=True
    )
    head_sha = _digest(
        pull_request["head_sha"], "policy.pull_request.head_sha", short=True
    )
    _digest(pull_request["body_sha256"], "policy.pull_request.body_sha256")

    plan = _closed(policy["plan"], PLAN_KEYS, "policy.plan")
    _text(plan["id"], "policy.plan.id")
    _text(plan["ref"], "policy.plan.ref")
    expected_plan_sha = _digest(plan["sha256"], "policy.plan.sha256")
    if hashlib.sha256(plan_bytes).hexdigest() != expected_plan_sha:
        raise ValueError("persisted plan hash does not match policy pin")

    component_pins = _string_map(
        policy["component_pins"], "policy.component_pins", digest_values=True
    )
    decision = _closed(policy["test_decision"], DECISION_KEYS, "policy.test_decision")
    _text(decision["coordinator_chat_id"], "policy.test_decision.coordinator_chat_id")
    _text(decision["decision_ref"], "policy.test_decision.decision_ref")
    _text(decision["rationale"], "policy.test_decision.rationale")
    levels = decision["levels"]
    if not isinstance(levels, dict) or set(levels) != set(LEVELS):
        raise ValueError("test decision must declare both development levels")
    for level in LEVELS:
        entry = _closed(levels[level], LEVEL_KEYS, f"test_decision.levels.{level}")
        if type(entry["required"]) is not bool:
            raise ValueError(f"test_decision.levels.{level}.required must be boolean")
        _text(entry["rationale"], f"test_decision.levels.{level}.rationale")

    checks = policy["checks"]
    if not isinstance(checks, list) or not checks or len(checks) > MAX_ITEMS:
        raise ValueError("policy.checks must be a non-empty bounded array")
    check_by_id: dict[str, dict[str, Any]] = {}
    required_levels: set[str] = set()
    for index, raw_check in enumerate(checks):
        check = _closed(raw_check, CHECK_KEYS, f"policy.checks[{index}]")
        check_id = _text(check["id"], f"policy.checks[{index}].id")
        if not IDENTIFIER.fullmatch(check_id) or check_id in check_by_id:
            raise ValueError("check ids must be valid and unique")
        if check["level"] not in LEVELS:
            raise ValueError(f"policy.checks[{index}].level is unsupported")
        if check["kind"] not in ("test", "inspection"):
            raise ValueError(f"policy.checks[{index}].kind is unsupported")
        if type(check["required_for_merge"]) is not bool:
            raise ValueError("check.required_for_merge must be boolean")
        if check["required_for_merge"]:
            required_levels.add(check["level"])
        check_by_id[check_id] = check
    for level in LEVELS:
        if levels[level]["required"] != (level in required_levels):
            raise ValueError(f"test decision and merge checks disagree for {level}")

    holds = policy["holds"]
    if not isinstance(holds, list) or len(holds) > MAX_ITEMS:
        raise ValueError("policy.holds must be a bounded array")
    blockers: list[dict[str, str]] = []
    for index, raw_hold in enumerate(holds):
        hold = _closed(raw_hold, HOLD_KEYS, f"policy.holds[{index}]")
        _text(hold["id"], f"policy.holds[{index}].id")
        if hold["state"] not in ("active", "resolved"):
            raise ValueError("hold state must be active or resolved")
        _text(hold["reason"], f"policy.holds[{index}].reason")
        if hold["state"] == "active":
            owner = _text(hold["owner"], f"policy.holds[{index}].owner")
            action = _text(hold["next_action"], f"policy.holds[{index}].next_action")
            blockers.append(
                {
                    "gate": "active_hold",
                    "reason": hold["reason"],
                    "owner": owner,
                    "next_action": action,
                }
            )
        else:
            _text(hold["owner"], f"policy.holds[{index}].owner", empty=True)
            _text(hold["next_action"], f"policy.holds[{index}].next_action", empty=True)

    evidence = _closed(evidence, EVIDENCE_KEYS, "evidence")
    if evidence["schema"] != EVIDENCE_SCHEMA:
        raise ValueError("unsupported evidence schema")
    rows = evidence["rows"]
    if not isinstance(rows, list) or len(rows) > MAX_ITEMS:
        raise ValueError("evidence.rows must be a bounded array")
    row_by_check: dict[str, dict[str, Any]] = {}
    for index, raw_row in enumerate(rows):
        row = _closed(raw_row, ROW_KEYS, f"evidence.rows[{index}]")
        check_id = _text(row["check_id"], f"evidence.rows[{index}].check_id")
        if check_id not in check_by_id or check_id in row_by_check:
            raise ValueError(
                "evidence check ids must match one declared check exactly once"
            )
        check = check_by_id[check_id]
        if row["level"] != check["level"]:
            raise ValueError(f"evidence level differs for {check_id}")
        if (
            _digest(row["base_sha"], f"evidence.rows[{index}].base_sha", short=True)
            != base_sha
        ):
            raise ValueError(f"stale evidence base pin for {check_id}")
        if (
            _digest(row["head_sha"], f"evidence.rows[{index}].head_sha", short=True)
            != head_sha
        ):
            raise ValueError(f"stale evidence head pin for {check_id}")
        pins = _string_map(
            row["component_pins"],
            f"evidence.rows[{index}].component_pins",
            digest_values=True,
        )
        if pins != component_pins:
            raise ValueError(f"wrong component pins for {check_id}")
        command = row["command"]
        if not isinstance(command, list) or not command or len(command) > 32:
            raise ValueError(
                f"evidence command for {check_id} must be a bounded argument list"
            )
        for item in command:
            _text(item, f"evidence.rows[{index}].command item")
        environment = row["environment"]
        if not isinstance(environment, dict) or len(environment) > MAX_ITEMS:
            raise ValueError(f"evidence environment for {check_id} must be an object")
        for key, item in environment.items():
            _text(key, f"evidence.rows[{index}].environment key")
            _text(item, f"evidence.rows[{index}].environment value")
        result = row["result"]
        if result not in RESULTS:
            raise ValueError(f"evidence result for {check_id} is unsupported")
        if type(row["exit_code"]) is not int or not -255 <= row["exit_code"] <= 255:
            raise ValueError(
                f"evidence exit_code for {check_id} must be a bounded integer"
            )
        if (
            type(row["test_count"]) is not int
            or not 0 <= row["test_count"] <= 1_000_000
        ):
            raise ValueError(f"evidence test_count for {check_id} must be bounded")
        _text(row["receipt_ref"], f"evidence.rows[{index}].receipt_ref")
        _digest(row["receipt_sha256"], f"evidence.rows[{index}].receipt_sha256")
        limitations = row["limitations"]
        if not isinstance(limitations, list) or len(limitations) > MAX_ITEMS:
            raise ValueError(
                f"evidence limitations for {check_id} must be a bounded array"
            )
        for limitation in limitations:
            _text(limitation, f"evidence.rows[{index}].limitations item")
        owner = _text(row["owner"], f"evidence.rows[{index}].owner", empty=True)
        next_action = _text(
            row["next_action"], f"evidence.rows[{index}].next_action", empty=True
        )
        zero_test_result = check["kind"] == "test" and row["test_count"] == 0
        if (result != "passed" or row["exit_code"] != 0 or zero_test_result) and (
            not owner or not next_action
        ):
            raise ValueError(
                f"non-passing evidence {check_id} requires owner and next_action"
            )
        row_by_check[check_id] = row

    for check_id, check in check_by_id.items():
        row = row_by_check.get(check_id)
        if row is None:
            if check["required_for_merge"]:
                blockers.append(
                    {
                        "gate": "missing_evidence",
                        "reason": f"required evidence missing: {check_id}",
                        "owner": "",
                        "next_action": "provide matching receipt",
                    }
                )
            continue
        passing = row["result"] == "passed" and row["exit_code"] == 0
        if check["kind"] == "test" and row["test_count"] == 0:
            passing = False
        if check["required_for_merge"] and not passing:
            reason = (
                "zero tests discovered"
                if check["kind"] == "test" and row["test_count"] == 0
                else row["result"]
            )
            blockers.append(
                {
                    "gate": "required_check",
                    "reason": f"{check_id}: {reason}",
                    "owner": row["owner"],
                    "next_action": row["next_action"],
                }
            )
    return blockers


def _inputs(
    policy_path: Path, evidence_path: Path, plan_path: Path
) -> tuple[dict[str, Any], dict[str, str]]:
    policy, policy_bytes = _read_json(policy_path)
    evidence, evidence_bytes = _read_json(evidence_path)
    try:
        metadata = plan_path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"persisted plan is not a regular file: {plan_path}")
        if metadata.st_size > MAX_INPUT_BYTES:
            raise ValueError("persisted plan exceeds input size limit")
        plan_bytes = plan_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read persisted plan: {plan_path}") from exc
    if len(plan_bytes) > MAX_INPUT_BYTES:
        raise ValueError("persisted plan exceeds input size limit")
    blockers = _validate_policy(policy, evidence, plan_bytes)
    pins = {
        "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
    }
    return {"policy": policy, "blockers": blockers}, pins


def plan_check(
    policy_path: Path, evidence_path: Path, plan_path: Path
) -> tuple[dict[str, Any], int]:
    try:
        data, pins = _inputs(policy_path, evidence_path, plan_path)
    except (TypeError, ValueError) as exc:
        return (
            {
                "schema": REPORT_SCHEMA,
                "command": "plan-check",
                "valid": False,
                "overall_ready": False,
                "coordinator_approval": {
                    "status": "unknown",
                    "reason": "no independent identity boundary",
                },
                "blockers": [
                    {
                        "gate": "input_validation",
                        "reason": str(exc),
                        "owner": "",
                        "next_action": "correct the declaration",
                    }
                ],
            },
            1,
        )
    blockers = data["blockers"]
    return (
        {
            "schema": REPORT_SCHEMA,
            "command": "plan-check",
            "valid": not blockers,
            "overall_ready": False,
            "coordinator_approval": {
                "status": "unknown",
                "reason": "no independent identity boundary",
            },
            "repository": data["policy"]["repository"],
            "pr_number": data["policy"]["pr_number"],
            "pins": pins,
            "blockers": blockers,
            "limitations": [
                "plan-check validates declarations only; it does not fetch GitHub or authenticate coordinator selection"
            ],
        },
        0 if not blockers else 1,
    )


def _fetch_pr(repository: str, number: int) -> dict[str, Any]:
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("--repo must be owner/name")
    if type(number) is not int or not 1 <= number <= 2**31 - 1:
        raise ValueError("--number must be a positive bounded integer")
    argv = [
        "gh",
        "pr",
        "view",
        str(number),
        "--repo",
        repository,
        "--json",
        "number,headRefOid,baseRefOid,body,state,reviewDecision",
    ]
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"GitHub read unavailable: {type(exc).__name__}") from exc
    if completed.returncode:
        raise ValueError("gh pr view failed; remote PR state is unknown")
    if len(completed.stdout.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("GitHub PR response exceeds size limit")
    try:
        snapshot = json.loads(
            completed.stdout,
            object_pairs_hook=_duplicate_free,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("gh pr view returned invalid JSON") from exc
    snapshot = _closed(
        snapshot,
        {"number", "headRefOid", "baseRefOid", "body", "state", "reviewDecision"},
        "gh pr view response",
    )
    if type(snapshot["number"]) is not int or snapshot["number"] != number:
        raise ValueError("GitHub PR number differs from request")
    _digest(snapshot["headRefOid"], "GitHub headRefOid", short=True)
    _digest(snapshot["baseRefOid"], "GitHub baseRefOid", short=True)
    if not isinstance(snapshot["body"], str):
        raise TypeError("GitHub PR body is missing or too large")
    try:
        body_bytes = snapshot["body"].encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("GitHub PR body is not valid UTF-8 text") from exc
    if len(body_bytes) > 512_000:
        raise ValueError("GitHub PR body is missing or too large")
    if snapshot["state"] not in ("OPEN", "CLOSED", "MERGED"):
        raise ValueError("GitHub PR state is unknown")
    if snapshot["reviewDecision"] not in REVIEW_DECISIONS:
        raise ValueError("GitHub reviewDecision is unknown")
    return snapshot


def _decode_gh_json(stdout: str, label: str) -> Any:
    if len(stdout.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError(f"{label} response exceeds size limit")
    try:
        return json.loads(
            stdout,
            object_pairs_hook=_duplicate_free,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} returned invalid JSON") from exc


def _check_result(row: Any, index: int) -> dict[str, str]:
    if not isinstance(row, dict):
        raise TypeError(f"required check {index} must be an object")
    allowed = set(CHECK_FIELDS)
    required = {"name", "bucket", "state"}
    if row.keys() - allowed or required - row.keys():
        raise ValueError(f"required check {index} has missing or unknown fields")
    name = _text(row["name"], f"required check {index}.name")
    bucket = row["bucket"]
    if not isinstance(bucket, str) or bucket not in CHECK_BUCKETS:
        raise ValueError(f"required check {name} has an unknown bucket")
    state = _text(row["state"], f"required check {name}.state")
    workflow = row.get("workflow")
    link = row.get("link")
    if workflow is not None:
        workflow = _text(workflow, f"required check {name}.workflow", empty=True)
    if link is not None:
        link = _text(link, f"required check {name}.link", empty=True)
    for timestamp_key in ("startedAt", "completedAt"):
        timestamp = row.get(timestamp_key)
        if timestamp is not None:
            _text(timestamp, f"required check {name}.{timestamp_key}", empty=True)

    if bucket == "pass" and state == "SUCCESS":
        status = "passed"
    elif bucket == "fail":
        status = "failed"
    elif bucket == "pending":
        status = "pending"
    elif bucket == "skipping" or state in ("NEUTRAL", "SKIPPED"):
        status = "skipped_or_neutral"
    elif bucket == "cancel":
        status = "cancelled"
    else:
        status = "unknown"
    return {
        "name": name,
        "workflow": workflow or "",
        "link": link or "",
        "bucket": bucket,
        "state": state,
        "status": status,
    }


def _read_required_checks(repository: str, number: int) -> dict[str, Any]:
    argv = [
        "gh",
        "pr",
        "checks",
        str(number),
        "--repo",
        repository,
        "--required",
        "--json",
        ",".join(CHECK_FIELDS),
    ]
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "unknown",
            "complete": False,
            "checks": [],
            "reason": f"required checks read unavailable: {type(exc).__name__}",
        }
    try:
        payload = _decode_gh_json(completed.stdout, "gh pr checks")
    except ValueError:
        # `gh pr checks --required` exits 1 with a text message when it reports
        # no checks. Empty/ambiguous results cannot prove there are no gates.
        message = "no required checks were returned; absence is ambiguous"
        if completed.returncode not in (0, 8):
            message = f"gh pr checks exited {completed.returncode}; required-check state is unknown"
        return {"status": "unknown", "complete": False, "checks": [], "reason": message}
    if completed.returncode not in (0, 1, 8):
        return {
            "status": "unknown",
            "complete": False,
            "checks": [],
            "reason": f"gh pr checks exited {completed.returncode}; required-check state is unknown",
        }
    if not isinstance(payload, list) or len(payload) >= MAX_ITEMS:
        return {
            "status": "unknown",
            "complete": False,
            "checks": [],
            "reason": "required-check response is malformed or reached the item bound",
        }
    if not payload:
        return {
            "status": "unknown",
            "complete": False,
            "checks": [],
            "reason": "no required checks were returned; absence is ambiguous",
        }
    try:
        checks = [_check_result(row, index) for index, row in enumerate(payload)]
    except (TypeError, ValueError) as exc:
        return {
            "status": "unknown",
            "complete": False,
            "checks": [],
            "reason": str(exc),
        }
    keys = [(row["name"], row["workflow"], row["link"]) for row in checks]
    if len(set(keys)) != len(keys):
        return {
            "status": "unknown",
            "complete": False,
            "checks": checks,
            "reason": "required-check identities are duplicated or ambiguous",
        }
    statuses = {row["status"] for row in checks}
    if completed.returncode == 8 and "pending" not in statuses:
        return {
            "status": "unknown",
            "complete": False,
            "checks": checks,
            "reason": "gh reported pending checks without a pending check record",
        }
    if completed.returncode == 1 and statuses == {"passed"}:
        return {
            "status": "unknown",
            "complete": False,
            "checks": checks,
            "reason": "gh reported failure with only passing check records",
        }
    if "unknown" in statuses:
        return {
            "status": "unknown",
            "complete": True,
            "checks": checks,
            "reason": "one or more required checks has an unsupported bucket/state combination",
        }
    status = "passed" if statuses == {"passed"} else "blocked"
    return {"status": status, "complete": True, "checks": checks, "reason": ""}


def _read_review_threads(repository: str, number: int) -> dict[str, Any]:
    owner, name = repository.split("/", 1)
    after = ""
    seen_cursors: set[str] = set()
    seen_ids: set[str] = set()
    threads: list[dict[str, Any]] = []
    pull_request_id: str | None = None
    head_sha: str | None = None
    base_sha: str | None = None
    state: str | None = None
    for page in range(1, MAX_REVIEW_THREAD_PAGES + 1):
        argv = [
            "gh",
            "api",
            "graphql",
            "-F",
            f"query={REVIEW_THREAD_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ]
        if after:
            argv.extend(("-F", f"after={after}"))
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, timeout=20, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "status": "unknown",
                "complete": False,
                "pages": page - 1,
                "threads": threads,
                "reason": f"review-thread read unavailable: {type(exc).__name__}",
            }
        try:
            payload = _decode_gh_json(completed.stdout, "GitHub review-thread query")
        except ValueError as exc:
            return {
                "status": "unknown",
                "complete": False,
                "pages": page - 1,
                "threads": threads,
                "reason": f"review-thread response unavailable: {exc}",
            }
        if (
            completed.returncode != 0
            or not isinstance(payload, dict)
            or payload.get("errors")
        ):
            return {
                "status": "unknown",
                "complete": False,
                "pages": page - 1,
                "threads": threads,
                "reason": "review-thread query failed or returned GraphQL errors",
            }
        if set(payload) != {"data"}:
            return {
                "status": "unknown",
                "complete": False,
                "pages": page - 1,
                "threads": threads,
                "reason": "review-thread response has unexpected top-level fields",
            }
        data = payload["data"]
        repository_data = data.get("repository") if isinstance(data, dict) else None
        pr = (
            repository_data.get("pullRequest")
            if isinstance(repository_data, dict)
            else None
        )
        if not isinstance(pr, dict) or set(pr) != {
            "id",
            "headRefOid",
            "baseRefOid",
            "state",
            "reviewThreads",
        }:
            return {
                "status": "unknown",
                "complete": False,
                "pages": page - 1,
                "threads": threads,
                "reason": "pull request or review-thread connection is unavailable",
            }
        try:
            current_id = _text(pr["id"], "review-thread pull request id")
            current_head = _digest(pr["headRefOid"], "review-thread head", short=True)
            current_base = _digest(pr["baseRefOid"], "review-thread base", short=True)
            current_state = _text(pr["state"], "review-thread pull request state")
            connection = pr["reviewThreads"]
            if not isinstance(connection, dict) or set(connection) != {
                "nodes",
                "pageInfo",
            }:
                raise ValueError("review-thread connection is malformed")
            nodes = connection["nodes"]
            page_info = connection["pageInfo"]
            if not isinstance(nodes, list) or len(nodes) > REVIEW_THREAD_PAGE_SIZE:
                raise ValueError("review-thread page exceeds its item bound")
            if not isinstance(page_info, dict) or set(page_info) != {
                "hasNextPage",
                "endCursor",
            }:
                raise ValueError("review-thread pageInfo is malformed")
            if type(page_info["hasNextPage"]) is not bool:
                raise ValueError("review-thread pageInfo.hasNextPage must be boolean")
            if page_info["endCursor"] is not None:
                _text(page_info["endCursor"], "review-thread cursor")
            if pull_request_id is None:
                pull_request_id, head_sha, base_sha, state = (
                    current_id,
                    current_head,
                    current_base,
                    current_state,
                )
            elif (current_id, current_head, current_base, current_state) != (
                pull_request_id,
                head_sha,
                base_sha,
                state,
            ):
                raise ValueError("pull request changed during review-thread pagination")
            for index, node in enumerate(nodes):
                if not isinstance(node, dict) or set(node) != {"id", "isResolved"}:
                    raise ValueError(f"review thread {index} is malformed")
                thread_id = _text(node["id"], f"review thread {index}.id")
                if type(node["isResolved"]) is not bool or thread_id in seen_ids:
                    raise ValueError("review-thread state is malformed or duplicated")
                seen_ids.add(thread_id)
                threads.append({"id": thread_id, "is_resolved": node["isResolved"]})
            if not page_info["hasNextPage"]:
                unresolved = [thread for thread in threads if not thread["is_resolved"]]
                return {
                    "status": "unresolved" if unresolved else "resolved",
                    "complete": True,
                    "pages": page,
                    "total": len(threads),
                    "unresolved_ids": sorted(thread["id"] for thread in unresolved),
                    "threads": sorted(threads, key=lambda item: item["id"]),
                    "head_sha": head_sha,
                    "base_sha": base_sha,
                    "state": state,
                    "reason": "",
                }
            cursor = page_info["endCursor"]
            if not cursor or cursor in seen_cursors:
                raise ValueError(
                    "review-thread pagination cursor is missing or repeated"
                )
            seen_cursors.add(cursor)
            after = cursor
        except (TypeError, ValueError) as exc:
            return {
                "status": "unknown",
                "complete": False,
                "pages": page,
                "threads": threads,
                "reason": str(exc),
            }
    return {
        "status": "unknown",
        "complete": False,
        "pages": MAX_REVIEW_THREAD_PAGES,
        "threads": threads,
        "reason": "review-thread pagination exceeded the configured page bound",
    }


def review_ready(
    repository: str,
    number: int,
    policy_path: Path,
    evidence_path: Path,
    plan_path: Path,
) -> tuple[dict[str, Any], int]:
    blockers: list[dict[str, str]] = []
    unverified_gates: list[dict[str, str]] = []
    checks_observed: dict[str, Any] = {
        "status": "unknown",
        "complete": False,
        "checks": [],
        "reason": "required check observation was not completed",
    }
    threads_observed: dict[str, Any] = {
        "status": "unknown",
        "complete": False,
        "pages": 0,
        "threads": [],
        "reason": "review-thread observation was not completed",
    }
    try:
        data, pins = _inputs(policy_path, evidence_path, plan_path)
        blockers.extend(data["blockers"])
    except (TypeError, ValueError) as exc:
        data, pins = None, {}
        blockers.append(
            {
                "gate": "input_validation",
                "reason": str(exc),
                "owner": "",
                "next_action": "correct the declaration",
            }
        )
    remote_before: dict[str, Any] | None = None
    remote_after: dict[str, Any] | None = None
    try:
        remote_before = _fetch_pr(repository, number)
    except (TypeError, ValueError) as exc:
        blockers.append(
            {
                "gate": "github_snapshot",
                "reason": str(exc),
                "owner": "",
                "next_action": "obtain a complete current PR snapshot",
            }
        )
    if remote_before is not None:
        checks_observed = _read_required_checks(repository, number)
        threads_observed = _read_review_threads(repository, number)
        try:
            remote_after = _fetch_pr(repository, number)
        except (TypeError, ValueError):
            remote_after = None

    def body_sha(snapshot: dict[str, Any] | None) -> str | None:
        if snapshot is None:
            return None
        try:
            return hashlib.sha256(snapshot["body"].encode("utf-8")).hexdigest()
        except UnicodeEncodeError:
            return None

    before_summary = (
        {
            "head_sha": remote_before["headRefOid"],
            "base_sha": remote_before["baseRefOid"],
            "body_sha256": body_sha(remote_before),
            "state": remote_before["state"],
            "review_decision": remote_before["reviewDecision"],
        }
        if remote_before
        else None
    )
    after_summary = (
        {
            "head_sha": remote_after["headRefOid"],
            "base_sha": remote_after["baseRefOid"],
            "body_sha256": body_sha(remote_after),
            "state": remote_after["state"],
            "review_decision": remote_after["reviewDecision"],
        }
        if remote_after
        else None
    )
    thread_snapshot_matches = True
    if remote_before is not None and threads_observed.get("head_sha"):
        thread_snapshot_matches = (
            threads_observed.get("head_sha") == remote_before["headRefOid"]
            and threads_observed.get("base_sha") == remote_before["baseRefOid"]
            and threads_observed.get("state") == remote_before["state"]
        )
    stable = (
        remote_before is not None
        and remote_after is not None
        and before_summary == after_summary
        and thread_snapshot_matches
    )
    if remote_before is not None and not stable:
        blockers.append(
            {
                "gate": "pr_snapshot_stability",
                "reason": "PR head, base, description, state, or review decision changed or could not be re-read during evidence collection",
                "owner": "coordinator",
                "next_action": "repeat observations against one unchanged frozen PR snapshot",
            }
        )
        checks_observed = {
            **checks_observed,
            "status": "unknown",
            "complete": False,
            "reason": "PR snapshot changed or final snapshot unavailable; required-check result is not applicable",
        }
        threads_observed = {
            **threads_observed,
            "status": "unknown",
            "complete": False,
            "reason": "PR snapshot changed or final snapshot unavailable; review-thread result is not applicable",
        }

    check_gate: dict[str, str] | None = None
    if checks_observed["status"] == "unknown":
        check_gate = {
            "gate": "github_required_checks",
            "status": "unknown",
            "reason": checks_observed["reason"],
            "owner": "coordinator",
            "next_action": "obtain a complete unambiguous required-check observation",
        }
        blockers.append(check_gate)
        unverified_gates.append(check_gate)
    if checks_observed["status"] in ("blocked", "unknown"):
        for check in checks_observed["checks"]:
            if check["status"] != "passed":
                blockers.append(
                    {
                        "gate": "required_check",
                        "status": check["status"],
                        "reason": f"required check {check['name']} is {check['status']} ({check['state']})",
                        "owner": "check owner",
                        "next_action": "resolve the required check and obtain a fresh snapshot",
                    }
                )

    thread_gate: dict[str, str] | None = None
    if threads_observed["status"] == "unknown":
        thread_gate = {
            "gate": "review_findings",
            "status": "unknown",
            "reason": threads_observed["reason"],
            "owner": "coordinator",
            "next_action": "obtain a complete review-thread observation",
        }
        blockers.append(thread_gate)
        unverified_gates.append(thread_gate)
    elif threads_observed["status"] == "unresolved":
        blockers.append(
            {
                "gate": "review_findings",
                "status": "unresolved",
                "reason": "one or more review threads remain unresolved",
                "owner": "coordinator",
                "next_action": "resolve the reported review threads and re-read current state",
            }
        )

    receipt_gate = {
        "gate": "receipt_verification",
        "status": "unknown",
        "reason": "declared receipt content and its hash have not been retrieved and verified",
        "owner": "evidence owner",
        "next_action": "retrieve each referenced receipt and verify its content hash",
    }
    unverified_gates.append(receipt_gate)
    blockers.append(receipt_gate)

    observed_decisions = {
        snapshot["reviewDecision"]
        for snapshot in (remote_before, remote_after)
        if snapshot is not None
    }
    if "CHANGES_REQUESTED" in observed_decisions:
        blockers.append(
            {
                "gate": "review_disposition",
                "reason": "GitHub reports CHANGES_REQUESTED; resolve the associated findings",
                "owner": "coordinator",
                "next_action": "inspect and resolve the current review findings",
            }
        )

    state = (
        remote_after["state"]
        if remote_after is not None
        else (remote_before["state"] if remote_before is not None else None)
    )
    if state is not None and state != "OPEN":
        blockers.append(
            {
                "gate": "pr_state",
                "reason": f"PR state is {state}",
                "owner": "coordinator",
                "next_action": "review current PR state",
            }
        )

    if remote_before is not None and data is not None:
        policy = data["policy"]
        if policy["repository"] != repository or policy["pr_number"] != number:
            blockers.append(
                {
                    "gate": "pr_identity",
                    "reason": "policy repository or number differs from fetched PR",
                    "owner": "",
                    "next_action": "refresh the policy pins",
                }
            )
        target = policy["pull_request"]
        if target["head_sha"] != remote_before["headRefOid"]:
            blockers.append(
                {
                    "gate": "head_pin",
                    "reason": "current PR head differs from policy",
                    "owner": "",
                    "next_action": "review and repin the new head",
                }
            )
        if target["base_sha"] != remote_before["baseRefOid"]:
            blockers.append(
                {
                    "gate": "base_pin",
                    "reason": "current PR base differs from policy",
                    "owner": "",
                    "next_action": "review and repin the new base",
                }
            )
        if target["body_sha256"] != body_sha(remote_before):
            blockers.append(
                {
                    "gate": "body_pin",
                    "reason": "current PR description differs from policy",
                    "owner": "",
                    "next_action": "review and repin the PR description",
                }
            )
    approval = {
        "status": "unknown",
        "reason": "no independent coordinator identity boundary",
    }
    blockers.append(
        {
            "gate": "coordinator_approval",
            "reason": "coordinator approval remains unknown; same-account reviews and worker assertions are not independent",
            "owner": "coordinator",
            "next_action": "establish a distinct authenticated review or signing boundary",
        }
    )
    result = {
        "schema": REPORT_SCHEMA,
        "command": "review-ready",
        "ready": False,
        "coordinator_approval": approval,
        "repository": repository,
        "pr_number": number,
        "pins": pins,
        "github_review_decision_observed": (
            remote_after["reviewDecision"]
            if remote_after
            else remote_before["reviewDecision"]
            if remote_before
            else None
        ),
        "github_state_observed": state,
        "pr_snapshot_before": before_summary,
        "pr_snapshot_after": after_summary,
        "github_required_checks": checks_observed,
        "review_threads": threads_observed,
        "blockers": blockers,
        "unverified_gates": unverified_gates,
        "limitations": [
            "GitHub reviewDecision is informational only and does not authenticate the coordinator under shared identity",
            "receipt references are hash-pinned declarations but receipt content and hashes are not retrieved or verified",
            "this read-only report provides no merge authorization",
        ],
    }
    return result, 1
