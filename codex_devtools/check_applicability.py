"""Closed evidence contract for coordinator-asserted N/A required checks."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import quote

SCHEMA = "codex-devtools-required-check-applicability-v1"
API_VERSION = "2022-11-28"
UPGRADE_MESSAGE = (
    "Upgrade to GitHub Pro or make this repository public to enable this feature."
)
UNSUPPORTED_ENDPOINTS = {
    "branch_protection": "branches/{branch}/protection",
    "branch_rules": "rules/branches/{branch}",
    "rulesets": "rulesets?includes_parents=true&targets=branch",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _branch_route(branch: str) -> str:
    return quote(branch, safe="")


OBSERVATION_FIELDS = {
    "schema",
    "repository",
    "target_branch",
    "target_sha",
    "head_sha",
    "repository_private",
    "repository_owner_type",
    "repository_observation",
    "branch",
    "required_checks",
    "workflows",
    "check_runs",
    "statuses",
    "aggregate_status",
    "unsupported_endpoints",
}


def canonical_hash(value: Any) -> str:
    """Hash canonical JSON, without accepting non-JSON Python values."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _endpoint_row(
    row: Any,
    *,
    endpoint: str,
    status_code: int,
    fields: set[str],
) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != fields:
        raise ValueError(f"{endpoint} observation is malformed")
    if (
        row.get("endpoint") != endpoint
        or row.get("api_version") != API_VERSION
        or row.get("status_code") != status_code
        or type(row.get("status_code")) is not int
        or not isinstance(row.get("body_sha256"), str)
        or not SHA256.fullmatch(row["body_sha256"])
    ):
        raise ValueError(f"{endpoint} observation identity is invalid")
    return row


def validate_na_observation(
    observation: Any,
    *,
    repository: str,
    target_branch: str,
    target_sha: str,
    head_sha: str,
) -> str:
    """Require a complete, exact, empty-check observation and return its hash."""
    if not isinstance(observation, dict) or set(observation) != OBSERVATION_FIELDS:
        raise ValueError("required-check applicability observation is malformed")
    if (
        observation["schema"] != SCHEMA
        or observation["repository"] != repository
        or observation["target_branch"] != target_branch
        or observation["target_sha"] != target_sha
        or observation["head_sha"] != head_sha
        or observation["repository_private"] is not True
        or observation["repository_owner_type"] != "User"
    ):
        raise ValueError("required-check applicability target or visibility differs")

    _endpoint_row(
        observation["repository_observation"],
        endpoint=f"repos/{repository}",
        status_code=200,
        fields={"endpoint", "api_version", "status_code", "body_sha256"},
    )

    branch = _endpoint_row(
        observation["branch"],
        endpoint=f"branches/{_branch_route(target_branch)}",
        status_code=200,
        fields={
            "endpoint",
            "api_version",
            "status_code",
            "body_sha256",
            "sha",
            "protected",
        },
    )
    if branch["sha"] != target_sha or branch["protected"] is not False:
        raise ValueError(
            "exact target branch is not proven unprotected at the pinned SHA"
        )

    required = observation["required_checks"]
    if (
        not isinstance(required, dict)
        or set(required) != {"command", "exit_code", "body_sha256", "complete", "rows"}
        or required.get("command") != "gh pr checks --required --json"
        or type(required.get("exit_code")) is not int
        or required["exit_code"] not in (0, 1, 8)
        or required.get("complete") is not True
        or required.get("rows") != []
        or not isinstance(required.get("body_sha256"), str)
        or not SHA256.fullmatch(required["body_sha256"])
    ):
        raise ValueError("required-check command did not return a valid empty set")

    workflows = _endpoint_row(
        observation["workflows"],
        endpoint="actions/workflows?per_page=100",
        status_code=200,
        fields={
            "endpoint",
            "api_version",
            "status_code",
            "body_sha256",
            "total_count",
            "rows",
            "complete",
            "truncated",
        },
    )
    if type(workflows["total_count"]) is not int or workflows["total_count"] != 0:
        raise ValueError("workflow inventory is non-empty or malformed")
    if (
        workflows["rows"] != []
        or workflows.get("complete") is not True
        or workflows.get("truncated") is not False
    ):
        raise ValueError("workflow inventory is not valid-empty")

    for field, endpoint in (
        ("check_runs", f"commits/{head_sha}/check-runs?per_page=100"),
        ("statuses", f"commits/{head_sha}/statuses?per_page=100"),
    ):
        row = _endpoint_row(
            observation[field],
            endpoint=endpoint,
            status_code=200,
            fields={
                "endpoint",
                "api_version",
                "status_code",
                "body_sha256",
                "complete",
                "truncated",
                "rows",
            },
        )
        if (
            row["complete"] is not True
            or row["truncated"] is not False
            or row["rows"] != []
        ):
            raise ValueError(f"{field.replace('_', ' ')} inventory is not valid-empty")

    aggregate = observation["aggregate_status"]
    if (
        not isinstance(aggregate, dict)
        or set(aggregate)
        != {
            "endpoint",
            "api_version",
            "status_code",
            "body_sha256",
            "state",
            "contexts",
        }
        or aggregate.get("endpoint") != f"commits/{head_sha}/status"
        or aggregate.get("api_version") != API_VERSION
        or aggregate.get("status_code") != 200
        or type(aggregate.get("status_code")) is not int
        or aggregate.get("state") not in {"success", "pending", "failure", "error"}
        or aggregate.get("contexts") != []
        or not isinstance(aggregate.get("body_sha256"), str)
        or not SHA256.fullmatch(aggregate["body_sha256"])
    ):
        raise ValueError("aggregate status observation is malformed or has contexts")

    unsupported = observation["unsupported_endpoints"]
    expected_names = set(UNSUPPORTED_ENDPOINTS)
    if not isinstance(unsupported, list) or len(unsupported) != len(expected_names):
        raise ValueError("protected/rules endpoint observations are incomplete")
    names = [row.get("name") if isinstance(row, dict) else None for row in unsupported]
    if set(names) != expected_names or len(set(names)) != len(names):
        raise ValueError(
            "protected/rules endpoint observations are duplicated or unknown"
        )
    by_name = {row["name"]: row for row in unsupported}
    for name, endpoint_template in UNSUPPORTED_ENDPOINTS.items():
        endpoint = endpoint_template.format(branch=_branch_route(target_branch))
        row = by_name[name]
        if (
            set(row)
            != {
                "name",
                "endpoint",
                "api_version",
                "status_code",
                "body_sha256",
                "message",
                "classification",
            }
            or row.get("endpoint") != endpoint
            or row.get("api_version") != API_VERSION
            or row.get("status_code") != 403
            or type(row.get("status_code")) is not int
            or row.get("message") != UPGRADE_MESSAGE
            or row.get("classification") != "feature_unsupported"
            or not isinstance(row.get("body_sha256"), str)
            or not SHA256.fullmatch(row["body_sha256"])
        ):
            raise ValueError(
                f"{name} is not the exact private-repository upgrade limitation"
            )
    return canonical_hash(observation)
