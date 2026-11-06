"""Closed evidence contract for coordinator-asserted N/A required checks."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import quote

LEGACY_SCHEMA = "codex-devtools-required-check-applicability-v2"
SCHEMA = "codex-devtools-required-check-applicability-v3"
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
    "head_ref",
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
    """Hash stable v3 semantics or exact legacy JSON, rejecting non-JSON values."""
    if isinstance(value, dict) and value.get("schema") == SCHEMA:
        repository_observation = value.get("repository_observation")
        if isinstance(repository_observation, dict):
            value = {
                **value,
                "repository_observation": {
                    key: item
                    for key, item in repository_observation.items()
                    if key != "raw_body_sha256"
                },
            }
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
    head_ref: str,
) -> str:
    """Require a complete, exact, empty-check observation and return its hash."""
    if not isinstance(observation, dict) or set(observation) != OBSERVATION_FIELDS:
        raise ValueError("required-check applicability observation is malformed")
    schema = observation["schema"]
    if (
        not isinstance(schema, str)
        or schema not in {LEGACY_SCHEMA, SCHEMA}
        or observation["repository"] != repository
        or observation["target_branch"] != target_branch
        or observation["target_sha"] != target_sha
        or observation["head_sha"] != head_sha
        or observation["head_ref"] != head_ref
        or observation["repository_private"] is not True
        or observation["repository_owner_type"] != "User"
    ):
        raise ValueError("required-check applicability target or visibility differs")

    repository_observation = observation["repository_observation"]
    if schema == SCHEMA:
        fields = {
            "endpoint",
            "api_version",
            "status_code",
            "raw_body_sha256",
            "full_name",
            "private",
            "owner_type",
        }
        if (
            not isinstance(repository_observation, dict)
            or set(repository_observation) != fields
            or repository_observation.get("endpoint") != f"repos/{repository}"
            or repository_observation.get("api_version") != API_VERSION
            or repository_observation.get("status_code") != 200
            or type(repository_observation.get("status_code")) is not int
            or not isinstance(repository_observation.get("raw_body_sha256"), str)
            or not SHA256.fullmatch(repository_observation["raw_body_sha256"])
            or repository_observation.get("full_name") != repository
            or repository_observation.get("private") is not True
            or repository_observation.get("owner_type") != "User"
            or repository_observation.get("private")
            != observation["repository_private"]
            or repository_observation.get("owner_type")
            != observation["repository_owner_type"]
        ):
            raise ValueError("repository identity observation is malformed")
    else:
        _endpoint_row(
            repository_observation,
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
        or set(required)
        != {
            "command",
            "head_ref",
            "head_sha",
            "base_sha",
            "target_branch",
            "exit_code",
            "stdout_sha256",
            "stderr_sha256",
            "representation",
            "complete",
            "rows",
        }
        or required.get("command") != "gh pr checks --required --json"
        or required.get("head_ref") != head_ref
        or required.get("head_sha") != head_sha
        or required.get("base_sha") != target_sha
        or required.get("target_branch") != target_branch
        or type(required.get("exit_code")) is not int
        or required["exit_code"] not in (0, 1, 8)
        or required.get("complete") is not True
        or required.get("rows") != []
        or not isinstance(required.get("stdout_sha256"), str)
        or not SHA256.fullmatch(required["stdout_sha256"])
        or not isinstance(required.get("stderr_sha256"), str)
        or not SHA256.fullmatch(required["stderr_sha256"])
    ):
        raise ValueError("required-check command did not return a valid empty set")
    representation = required.get("representation")
    if representation == {"kind": "structured_json"}:
        if (
            required["exit_code"] not in (0, 1, 8)
            or required["stderr_sha256"] != hashlib.sha256(b"").hexdigest()
        ):
            raise ValueError("structured required-check result is invalid")
    elif representation == {"kind": "gh_no_checks_reported", "branch": head_ref}:
        if (
            required["exit_code"] != 1
            or required["stdout_sha256"] != hashlib.sha256(b"").hexdigest()
            or required["stderr_sha256"]
            != hashlib.sha256(
                f"no checks reported on the '{head_ref}' branch\n".encode()
            ).hexdigest()
        ):
            raise ValueError(
                "known no-check sentinel does not match the exact PR head ref"
            )
    else:
        raise ValueError("required-check CLI representation is unknown")

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
        fields = {
            "endpoint",
            "api_version",
            "status_code",
            "body_sha256",
            "complete",
            "truncated",
            "rows",
        }
        if field == "check_runs":
            fields.add("total_count")
        row = _endpoint_row(
            observation[field],
            endpoint=endpoint,
            status_code=200,
            fields=fields,
        )
        if (
            row["complete"] is not True
            or row["truncated"] is not False
            or (
                field == "check_runs"
                and (type(row["total_count"]) is not int or row["total_count"] != 0)
            )
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
