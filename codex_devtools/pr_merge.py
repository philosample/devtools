"""Explicit operator-asserted exact-head PR merge boundary.

This adapter does not authenticate the operator.  It keeps the existing
readiness report read-only and requires a separate explicit operator action.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

from . import check_applicability

SHA1 = re.compile(r"^[0-9a-f]{40}$")
REVIEW_REFERENCE = re.compile(
    r"^(?:[1-9][0-9]*|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*#pullrequestreview-[1-9][0-9]*)$"
)
DECISION_REFERENCE = re.compile(
    r"^(?:[1-9][0-9]*|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*#issuecomment-[1-9][0-9]*)$"
)
DECISION_SCHEMA = "codex-devtools-pr-coordinator-decision-v1"
DECISION_SCHEMA_V2 = "codex-devtools-pr-coordinator-decision-v2"
DECISION_FIELDS = {
    "schema",
    "repository",
    "pr_number",
    "head_sha",
    "base_sha",
    "head_tree_sha",
    "merge_tree_sha",
    "body_sha256",
    "policy_sha256",
    "evidence_sha256",
    "plan_sha256",
    "operator_asserted",
}
DECISION_FIELDS_V2 = DECISION_FIELDS | {
    "target_branch",
    "target_sha",
    "required_checks_disposition",
    "applicability_observation_sha256",
    "applicability_reason",
}
APPROVAL_GATE = "coordinator_approval"


class UnknownCompletion(ValueError):
    """The request may have reached GitHub but no terminal response was observed."""


class TerminalVerificationError(ValueError):
    """Fetched terminal commit identity does not match its requested SHA."""


def _stdout_bytes(stdout: bytes | str, label: str) -> bytes:
    if isinstance(stdout, bytes):
        return stdout
    if isinstance(stdout, str):
        try:
            return stdout.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{label} stdout is not valid UTF-8 text") from exc
    raise ValueError(f"{label} stdout must be bytes or text")


def _has_next_link(link_header: str) -> bool:
    """Parse Link relations without confusing page query parameters for `next`."""
    if not isinstance(link_header, str):
        raise TypeError("GitHub Link header must be text")
    if not link_header.strip():
        return False
    entries: list[str] = []
    start = 0
    in_target = False
    in_quote = False
    for index, character in enumerate(link_header):
        if character == '"' and not in_target:
            in_quote = not in_quote
        elif character == "<" and not in_quote:
            in_target = True
        elif character == ">" and not in_quote:
            in_target = False
        elif character == "," and not in_target and not in_quote:
            entries.append(link_header[start:index].strip())
            start = index + 1
    if in_target or in_quote:
        raise ValueError("GitHub Link header delimiters are unbalanced")
    entries.append(link_header[start:].strip())
    for entry in entries:
        match = re.fullmatch(r"<[^<>]+>(.*)", entry)
        if match is None:
            raise ValueError("GitHub Link header entry is malformed")
        parameters = match.group(1).strip()
        found_relation = False
        is_next = False
        while parameters:
            parameter = re.match(
                r";\s*([!#$%&'*+.^_`|~0-9A-Za-z-]+)\s*=\s*"
                r'(?:"((?:[^"\\]|\\.)*)"|([^;\s,]+))(.*)$',
                parameters,
            )
            if parameter is None:
                raise ValueError("GitHub Link header parameter is malformed")
            name = parameter.group(1).lower()
            value = (
                parameter.group(2)
                if parameter.group(2) is not None
                else parameter.group(3)
            )
            if name == "rel":
                if found_relation or not value.strip():
                    raise ValueError("GitHub Link header relation is malformed")
                found_relation = True
                is_next = "next" in value.lower().split()
            parameters = parameter.group(4).strip()
        if not found_relation:
            raise ValueError("GitHub Link header relation is missing")
        if is_next:
            return True
    return False


class MergePorts(Protocol):
    def snapshot(self) -> dict[str, Any]: ...

    def review(self, reference: str) -> dict[str, Any]: ...

    def decision(self, reference: str) -> dict[str, Any]: ...

    def check_applicability(
        self, *, target_branch: str, target_sha: str, head_sha: str
    ) -> dict[str, Any]: ...

    def merge(self, expected_head: str) -> dict[str, Any]: ...

    def terminal(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GHMergePorts:
    repository: str
    number: int

    def _api(
        self,
        *args: str,
        method: str | None = None,
        fields: tuple[tuple[str, str], ...] = (),
    ) -> Any:
        command = ["gh", "api"]
        if method:
            command.extend(("--method", method))
        command.extend(args)
        for key, value in fields:
            command.extend(("-f", f"{key}={value}"))
        try:
            completed = subprocess.run(
                command, capture_output=True, text=False, timeout=20, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UnknownCompletion("GitHub API request completion is unknown") from exc
        if completed.returncode:
            # gh's nonzero exit alone does not prove whether a mutation reached
            # GitHub before its response was lost.
            raise UnknownCompletion("GitHub API returned no trustworthy completion")
        raw = _stdout_bytes(completed.stdout, "GitHub API")
        if len(raw) > 1_048_576:
            raise ValueError("GitHub API response exceeds size limit")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("GitHub API returned invalid JSON") from exc

    def _included_api(self, endpoint: str) -> dict[str, Any]:
        command = [
            "gh",
            "api",
            "--include",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            f"X-GitHub-Api-Version: {check_applicability.API_VERSION}",
            endpoint,
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=False, timeout=20, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("applicability endpoint is unavailable") from exc
        raw = _stdout_bytes(completed.stdout, "applicability API")
        if len(raw) > 1_048_576:
            raise ValueError("applicability endpoint response exceeds size limit")
        starts = list(re.finditer(rb"(?m)^HTTP/\S+\s+([0-9]{3})(?:\s|$)", raw))
        if not starts:
            raise ValueError("applicability endpoint omitted HTTP status headers")
        status_line = starts[-1]
        status_code = int(status_line.group(1))
        separator = re.search(rb"\r?\n\r?\n", raw[status_line.end() :])
        if separator is None:
            raise ValueError("applicability endpoint headers are malformed")
        separator_start = status_line.end() + separator.start()
        body_start = status_line.end() + separator.end()
        headers: dict[str, str] = {}
        for line in raw[status_line.end() : separator_start].splitlines():
            name, colon, value = line.partition(b":")
            if colon:
                header_name = name.strip().decode("ascii").lower()
                header_value = value.strip().decode("utf-8")
                if header_name == "link" and header_name in headers:
                    headers[header_name] += ", " + header_value
                else:
                    headers[header_name] = header_value
        body = raw[body_start:]
        body_sha256 = hashlib.sha256(body).hexdigest()
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("applicability endpoint body is invalid JSON") from exc
        return {
            "status_code": status_code,
            "headers": headers,
            "body": body.decode("utf-8"),
            "body_sha256": body_sha256,
            "payload": payload,
            "returncode": completed.returncode,
        }

    @staticmethod
    def _required_check_observation(repository: str, number: int) -> dict[str, Any]:
        from .pr_readiness import CHECK_FIELDS

        try:
            completed = subprocess.run(
                [
                    "gh",
                    "pr",
                    "checks",
                    str(number),
                    "--repo",
                    repository,
                    "--required",
                    "--json",
                    ",".join(CHECK_FIELDS),
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("required-check observation is unavailable") from exc
        raw = _stdout_bytes(completed.stdout, "required-check CLI")
        if len(raw) > 1_048_576:
            raise ValueError("required-check response exceeds size limit")
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("required-check response is not structured JSON") from exc
        if (
            completed.returncode not in (0, 1, 8)
            or not isinstance(rows, list)
            or len(rows) >= 500
        ):
            raise ValueError("required-check response is incomplete or malformed")
        return {
            "command": "gh pr checks --required --json",
            "exit_code": completed.returncode,
            "body_sha256": hashlib.sha256(raw).hexdigest(),
            "complete": True,
            "rows": rows,
        }

    def check_applicability(
        self, *, target_branch: str, target_sha: str, head_sha: str
    ) -> dict[str, Any]:
        """Collect one bounded, exact observation; ambiguous responses fail closed."""
        branch_path = quote(target_branch, safe="")
        repo_endpoint = f"repos/{self.repository}"
        branch_endpoint = f"repos/{self.repository}/branches/{branch_path}"
        workflows_endpoint = f"repos/{self.repository}/actions/workflows?per_page=100"
        run_endpoint = (
            f"repos/{self.repository}/commits/{head_sha}/check-runs?per_page=100"
        )
        statuses_endpoint = (
            f"repos/{self.repository}/commits/{head_sha}/statuses?per_page=100"
        )
        aggregate_endpoint = f"repos/{self.repository}/commits/{head_sha}/status"
        repo_response = self._included_api(repo_endpoint)
        branch_response = self._included_api(branch_endpoint)
        workflows_response = self._included_api(workflows_endpoint)
        runs_response = self._included_api(run_endpoint)
        statuses_response = self._included_api(statuses_endpoint)
        aggregate_response = self._included_api(aggregate_endpoint)
        required = self._required_check_observation(self.repository, self.number)

        def api_row(endpoint: str, response: dict[str, Any]) -> dict[str, Any]:
            if response["status_code"] != 200 or response["returncode"] != 0:
                raise ValueError(f"{endpoint} did not return a successful response")
            return {
                "endpoint": endpoint.removeprefix(f"repos/{self.repository}/"),
                "api_version": check_applicability.API_VERSION,
                "status_code": response["status_code"],
                "body_sha256": response["body_sha256"],
            }

        repo_payload = repo_response["payload"]
        branch_payload = branch_response["payload"]
        workflows_payload = workflows_response["payload"]
        runs_payload = runs_response["payload"]
        statuses_payload = statuses_response["payload"]
        aggregate_payload = aggregate_response["payload"]
        if not all(
            isinstance(value, dict)
            for value in (
                repo_payload,
                branch_payload,
                workflows_payload,
                runs_payload,
                aggregate_payload,
            )
        ) or not isinstance(statuses_payload, list):
            raise ValueError("applicability endpoint JSON shape is malformed")
        run_rows = runs_payload.get("check_runs")
        workflow_rows = workflows_payload.get("workflows")
        status_rows = statuses_payload
        if not isinstance(run_rows, list) or not isinstance(workflow_rows, list):
            raise TypeError("applicability endpoint row collection is malformed")
        workflow_link = _has_next_link(workflows_response["headers"].get("link", ""))
        run_link = _has_next_link(runs_response["headers"].get("link", ""))
        status_link = _has_next_link(statuses_response["headers"].get("link", ""))
        unsupported = []
        for name, template in check_applicability.UNSUPPORTED_ENDPOINTS.items():
            endpoint = template.format(branch=branch_path)
            response = self._included_api(f"repos/{self.repository}/{endpoint}")
            payload = response["payload"]
            message = payload.get("message") if isinstance(payload, dict) else None
            exact_upgrade = (
                repo_payload.get("private") is True
                and response["status_code"] == 403
                and message == check_applicability.UPGRADE_MESSAGE
            )
            unsupported.append(
                {
                    "name": name,
                    "endpoint": endpoint,
                    "api_version": check_applicability.API_VERSION,
                    "status_code": response["status_code"],
                    "body_sha256": response["body_sha256"],
                    "message": message,
                    "classification": "feature_unsupported"
                    if exact_upgrade
                    else "unknown",
                }
            )
        aggregate_statuses = aggregate_payload.get("statuses")
        if not isinstance(aggregate_statuses, list):
            raise TypeError("aggregate status contexts are malformed")
        return {
            "schema": check_applicability.SCHEMA,
            "repository": self.repository,
            "target_branch": target_branch,
            "target_sha": target_sha,
            "head_sha": head_sha,
            "repository_private": repo_payload.get("private"),
            "repository_owner_type": repo_payload.get("owner", {}).get("type")
            if isinstance(repo_payload.get("owner"), dict)
            else None,
            "repository_observation": api_row(repo_endpoint, repo_response),
            "branch": {
                **api_row(branch_endpoint, branch_response),
                "sha": branch_payload.get("commit", {}).get("sha")
                if isinstance(branch_payload.get("commit"), dict)
                else None,
                "protected": branch_payload.get("protected"),
            },
            "required_checks": required,
            "workflows": {
                **api_row(workflows_endpoint, workflows_response),
                "total_count": workflows_payload.get("total_count"),
                "rows": workflow_rows,
                "complete": not workflow_link,
                "truncated": workflow_link,
            },
            "check_runs": {
                **api_row(run_endpoint, runs_response),
                "total_count": runs_payload.get("total_count"),
                "complete": not run_link
                and type(runs_payload.get("total_count")) is int
                and runs_payload.get("total_count") == len(run_rows),
                "truncated": run_link,
                "rows": run_rows,
            },
            "statuses": {
                **api_row(statuses_endpoint, statuses_response),
                "complete": not status_link,
                "truncated": status_link,
                "rows": status_rows,
            },
            "aggregate_status": {
                **api_row(aggregate_endpoint, aggregate_response),
                "state": aggregate_payload.get("state"),
                "contexts": aggregate_statuses,
            },
            "unsupported_endpoints": unsupported,
        }

    def snapshot(self) -> dict[str, Any]:
        pr = self._api(f"repos/{self.repository}/pulls/{self.number}")
        head = pr["head"]["sha"]
        base = pr["base"]["sha"]
        head_commit = self._api(f"repos/{self.repository}/commits/{head}")
        body = pr["body"] or ""
        if not isinstance(body, str):
            raise TypeError("PR body is malformed")
        return {
            "number": pr["number"],
            "state": "MERGED" if pr.get("merged") else pr["state"].upper(),
            "head_sha": head,
            "base_sha": base,
            "base_ref": pr["base"]["ref"],
            "head_tree_sha": head_commit["commit"]["tree"]["sha"],
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }

    def review(self, reference: str) -> dict[str, Any]:
        match = re.search(r"pullrequestreview-([1-9][0-9]*)$", reference)
        identifier = match.group(1) if match else reference
        reviews = self._api(f"repos/{self.repository}/pulls/{self.number}/reviews")
        if not isinstance(reviews, list):
            raise TypeError("GitHub review list is malformed")
        matches = [row for row in reviews if str(row.get("id")) == identifier]
        if len(matches) != 1:
            raise ValueError("review reference is missing or ambiguous")
        row = matches[0]
        return {
            "id": str(row["id"]),
            "state": row["state"],
            "commit_sha": row["commit_id"],
            "html_url": row["html_url"],
        }

    def decision(self, reference: str) -> dict[str, Any]:
        match = re.search(r"(?:issuecomment-|^)([1-9][0-9]*)$", reference)
        if match is None:
            raise ValueError("decision reference has no comment ID")
        comment = self._api(f"repos/{self.repository}/issues/comments/{match.group(1)}")
        expected_issue_url = (
            f"https://api.github.com/repos/{self.repository}/issues/{self.number}"
        )
        if comment.get("issue_url") != expected_issue_url:
            raise ValueError("decision comment belongs to another pull request")
        return {
            "id": str(comment["id"]),
            "html_url": comment["html_url"],
            "issue_number": self.number,
            "body": comment["body"],
        }

    def merge(self, expected_head: str) -> dict[str, Any]:
        return self._api(
            f"repos/{self.repository}/pulls/{self.number}/merge",
            method="PUT",
            fields=(("sha", expected_head), ("merge_method", "squash")),
        )

    def terminal(self) -> dict[str, Any]:
        pr = self._api(f"repos/{self.repository}/pulls/{self.number}")
        head = pr["head"]["sha"]
        head_commit = self._api(f"repos/{self.repository}/commits/{head}")
        merge_commit_sha = pr.get("merge_commit_sha")
        if not merge_commit_sha:
            return {
                "state": "MERGED" if pr.get("merged") else pr["state"].upper(),
                "head_sha": head,
                "head_tree_sha": head_commit["commit"]["tree"]["sha"],
                "merge_commit_sha": None,
                "merge_commit_tree_sha": None,
                "parent_shas": [],
            }
        merge_commit = self._api(f"repos/{self.repository}/commits/{merge_commit_sha}")
        if merge_commit.get("sha") != merge_commit_sha:
            raise TerminalVerificationError(
                "fetched merge commit object SHA differs from the PR merge SHA"
            )
        return {
            "state": "MERGED" if pr.get("merged") else pr["state"].upper(),
            "head_sha": head,
            "head_tree_sha": head_commit["commit"]["tree"]["sha"],
            "merge_commit_sha": merge_commit_sha,
            "merge_commit_object_sha": merge_commit["sha"],
            "merge_commit_tree_sha": merge_commit["commit"]["tree"]["sha"],
            "parent_shas": [parent["sha"] for parent in merge_commit["parents"]],
        }


def check_applicability_report(
    *,
    repository: str,
    number: int,
    expected_head: str,
    expected_base: str,
    expected_head_tree: str,
    body_sha256: str,
    expected_policy_sha256: str,
    expected_evidence_sha256: str,
    expected_plan_sha256: str,
    ports: MergePorts,
) -> dict[str, Any]:
    """Return candidate evidence only; this report never changes readiness."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be owner/name")
    if type(number) is not int or number < 1:
        raise ValueError("number must be a positive integer")
    head = _digest(expected_head, "expected head")
    base = _digest(expected_base, "expected base")
    head_tree = _digest(expected_head_tree, "expected source head tree")
    hashes = {
        "body_sha256": body_sha256,
        "policy_sha256": expected_policy_sha256,
        "evidence_sha256": expected_evidence_sha256,
        "plan_sha256": expected_plan_sha256,
    }
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in hashes.values()
    ):
        raise ValueError(
            "reviewed body and declaration pins must be full SHA-256 values"
        )
    current = ports.snapshot()
    if (
        current.get("number") != number
        or current.get("state") != "OPEN"
        or current.get("head_sha") != head
        or current.get("base_sha") != base
        or current.get("head_tree_sha") != head_tree
        or current.get("body_sha256") != body_sha256
        or not isinstance(current.get("base_ref"), str)
        or not current["base_ref"]
    ):
        raise ValueError("PR pins do not match the requested applicability observation")
    observation = ports.check_applicability(
        target_branch=current["base_ref"], target_sha=base, head_sha=head
    )
    observation_sha256 = check_applicability.validate_na_observation(
        observation,
        repository=repository,
        target_branch=current["base_ref"],
        target_sha=base,
        head_sha=head,
    )
    return {
        "command": "check-applicability",
        "status": "candidate_evidence",
        "coordinator_receipt_required": True,
        "operator_asserted": False,
        "repository": repository,
        "pr_number": number,
        "head_sha": head,
        "base_sha": base,
        "target_branch": current["base_ref"],
        "head_tree_sha": head_tree,
        **hashes,
        "observation_sha256": observation_sha256,
        "observation": observation,
        "merge_authorized": False,
        "ci_passed": False,
    }


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA1.fullmatch(value):
        raise ValueError(f"{name} must be a full lowercase SHA-1")
    return value


def _review_ready(
    report: dict[str, Any],
    expected: dict[str, str],
    expected_declarations: dict[str, str],
    repository: str,
    number: int,
    allow_required_checks_na: bool = False,
) -> dict[str, str]:
    if (
        not isinstance(report, dict)
        or report.get("command") != "review-ready"
        or report.get("ready") is not False
    ):
        raise ValueError("readiness report is malformed or changed its strict result")
    approval = report.get("coordinator_approval")
    if not isinstance(approval, dict) or approval.get("status") != "unknown":
        raise ValueError("readiness report made an unsupported authentication claim")
    if report.get("repository") != repository or report.get("pr_number") != number:
        raise ValueError("readiness report belongs to another pull request")
    pins = report.get("pins")
    if (
        not isinstance(pins, dict)
        or set(pins)
        != {
            "policy_sha256",
            "evidence_sha256",
            "plan_sha256",
        }
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in pins.values()
        )
    ):
        raise ValueError("readiness policy, evidence, or plan pins are missing")
    if pins != expected_declarations:
        raise ValueError(
            "fresh readiness policy, evidence, or plan hash differs from the coordinator-reviewed pin"
        )
    blockers = report.get("blockers")
    if not isinstance(blockers, list):
        raise TypeError("readiness blockers are malformed")
    if any(not isinstance(item, dict) for item in blockers):
        raise ValueError("readiness blocker entry is malformed")
    allowed_gates = {APPROVAL_GATE}
    if allow_required_checks_na:
        allowed_gates.add("github_required_checks")
    if (
        any(item.get("gate") not in allowed_gates for item in blockers)
        or sum(item.get("gate") == APPROVAL_GATE for item in blockers) != 1
    ):
        raise ValueError("readiness has missing, unresolved, or unknown required gates")
    before, after = report.get("pr_snapshot_before"), report.get("pr_snapshot_after")
    for name, row in (("before", before), ("after", after)):
        if not isinstance(row, dict):
            raise TypeError(f"readiness {name} snapshot is missing")
        if (row.get("head_sha"), row.get("base_sha"), row.get("body_sha256")) != (
            expected["head_sha"],
            expected["base_sha"],
            expected["body_sha256"],
        ):
            raise ValueError(f"readiness {name} snapshot differs from expected pins")
    checks = report.get("github_required_checks", {})
    threads = report.get("review_threads", {})
    receipts = report.get("receipt_verification", {})
    if not all(isinstance(item, dict) for item in (checks, threads, receipts)):
        raise ValueError("readiness evidence observations are malformed")
    ci_blockers = [
        item for item in blockers if item.get("gate") == "github_required_checks"
    ]
    if allow_required_checks_na:
        if (
            checks.get("status") != "unknown"
            or checks.get("complete") is not False
            or checks.get("checks") != []
            or checks.get("reason")
            != "no required checks were returned; absence is ambiguous"
            or len(ci_blockers) != 1
            or ci_blockers[0].get("status") != "unknown"
            or ci_blockers[0].get("reason") != checks.get("reason")
        ):
            raise ValueError(
                "required-check N/A requires the exact ambiguous empty-check result"
            )
    elif checks.get("status") != "passed" or checks.get("complete") is not True:
        raise ValueError("required CI observation is not complete and passing")
    elif ci_blockers:
        raise ValueError("readiness has a contradictory required-check blocker")
    if threads.get("status") != "resolved" or threads.get("complete") is not True:
        raise ValueError("review-thread observation is not complete and resolved")
    if receipts.get("status") != "verified" or receipts.get("complete") is not True:
        raise ValueError("required evidence receipts are not verified")
    return pins


def _duplicate_free(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate decision JSON key: {key}")
        result[key] = value
    return result


def _decision_receipt(
    ports: MergePorts,
    *,
    reference: str,
    expected_sha256: str,
    repository: str,
    number: int,
    head: str,
    base: str,
    head_tree: str,
    merge_tree: str,
    body_sha256: str,
    declarations: dict[str, str],
    target_branch: str,
) -> dict[str, Any]:
    if not isinstance(reference, str) or not DECISION_REFERENCE.fullmatch(reference):
        raise ValueError("decision reference must identify one explicit GitHub comment")
    if reference.startswith("https://") and not reference.startswith(
        f"https://github.com/{repository}/pull/{number}#issuecomment-"
    ):
        raise ValueError("decision comment reference belongs to another pull request")
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise ValueError("decision comment hash must be a full lowercase SHA-256")
    comment = ports.decision(reference)
    identifier_match = re.search(r"(?:issuecomment-|^)([1-9][0-9]*)$", reference)
    if identifier_match is None or str(comment.get("id")) != identifier_match.group(1):
        raise ValueError("decision comment ID differs from the pinned reference")
    if comment.get("issue_number") != number:
        raise ValueError("decision comment belongs to another pull request")
    if reference.startswith("https://") and comment.get("html_url") != reference:
        raise ValueError("decision comment URL differs from the pinned reference")
    body = comment.get("body")
    if not isinstance(body, str):
        raise TypeError("decision comment body is missing or malformed")
    try:
        raw = body.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("decision comment is not valid UTF-8") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("decision comment content hash changed")
    try:
        receipt = json.loads(body, object_pairs_hook=_duplicate_free)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(
            "decision comment must contain one structured JSON receipt"
        ) from exc
    if not isinstance(receipt, dict) or set(receipt) not in (
        DECISION_FIELDS,
        DECISION_FIELDS_V2,
    ):
        raise ValueError(
            "decision receipt fields are malformed or include unsupported claims"
        )
    if (
        type(receipt["pr_number"]) is not int
        or type(receipt["operator_asserted"]) is not bool
    ):
        raise ValueError(
            "decision receipt number and operator assertion types are invalid"
        )
    expected = {
        "repository": repository,
        "pr_number": number,
        "head_sha": head,
        "base_sha": base,
        "head_tree_sha": head_tree,
        "merge_tree_sha": merge_tree,
        "body_sha256": body_sha256,
        **declarations,
        "operator_asserted": True,
    }
    if receipt.get("schema") == DECISION_SCHEMA:
        expected["schema"] = DECISION_SCHEMA
    elif receipt.get("schema") == DECISION_SCHEMA_V2:
        reason = receipt.get("applicability_reason")
        if (
            receipt.get("required_checks_disposition") != "not_applicable"
            or receipt.get("target_branch") != target_branch
            or receipt.get("target_sha") != base
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 500
            or not isinstance(receipt.get("applicability_observation_sha256"), str)
            or not re.fullmatch(
                r"[0-9a-f]{64}", receipt["applicability_observation_sha256"]
            )
        ):
            raise ValueError(
                "decision v2 required-check applicability pins are malformed"
            )
        observation = ports.check_applicability(
            target_branch=target_branch, target_sha=base, head_sha=head
        )
        observation_sha256 = check_applicability.validate_na_observation(
            observation,
            repository=repository,
            target_branch=target_branch,
            target_sha=base,
            head_sha=head,
        )
        expected.update(
            {
                "schema": DECISION_SCHEMA_V2,
                "target_branch": target_branch,
                "target_sha": base,
                "required_checks_disposition": "not_applicable",
                "applicability_observation_sha256": observation_sha256,
                "applicability_reason": reason,
            }
        )
    else:
        raise ValueError("decision receipt schema is unsupported")
    if receipt != expected:
        raise ValueError(
            "decision receipt does not bind the exact reviewed pins and evidence"
        )
    return {
        "decision_sha256": expected_sha256,
        "required_checks_disposition": receipt.get(
            "required_checks_disposition", "passed"
        ),
        "applicability_observation_sha256": receipt.get(
            "applicability_observation_sha256"
        ),
        "applicability_reason": receipt.get("applicability_reason"),
        "target_branch": target_branch,
    }


def merge_pr(
    *,
    repository: str,
    number: int,
    expected_head: str,
    expected_base: str,
    expected_tree: str,
    body_sha256: str,
    expected_policy_sha256: str,
    expected_evidence_sha256: str,
    expected_plan_sha256: str,
    expected_head_tree: str,
    review_reference: str | None,
    decision_reference: str | None,
    decision_sha256: str | None,
    operator_assertion: str,
    readiness: Callable[[], dict[str, Any]],
    ports: MergePorts,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate one fresh review and optionally issue exactly one merge request."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be owner/name")
    if type(number) is not int or number < 1:
        raise ValueError("number must be a positive integer")
    head, base, tree = (
        _digest(expected_head, "expected head"),
        _digest(expected_base, "expected base"),
        _digest(expected_tree, "expected tree"),
    )
    head_tree = _digest(expected_head_tree, "expected source head tree")
    if not isinstance(body_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", body_sha256
    ):
        raise ValueError("body hash must be a full lowercase SHA-256")
    expected_declarations = {
        "policy_sha256": expected_policy_sha256,
        "evidence_sha256": expected_evidence_sha256,
        "plan_sha256": expected_plan_sha256,
    }
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in expected_declarations.values()
    ):
        raise ValueError(
            "reviewed policy/evidence/plan pins must be full SHA-256 values"
        )
    use_review = review_reference is not None
    use_decision = decision_reference is not None or decision_sha256 is not None
    if use_review == use_decision:
        raise ValueError(
            "provide exactly one formal review or coordinator decision reference"
        )
    if use_review:
        if not isinstance(review_reference, str) or not REVIEW_REFERENCE.fullmatch(
            review_reference
        ):
            raise ValueError(
                "review reference must identify one explicit GitHub review"
            )
        if review_reference.startswith("https://") and not review_reference.startswith(
            f"https://github.com/{repository}/pull/{number}#pullrequestreview-"
        ):
            raise ValueError(
                "review reference belongs to another repository or pull request"
            )
    elif not isinstance(decision_reference, str) or decision_sha256 is None:
        raise ValueError(
            "decision reference and exact content hash must be supplied together"
        )
    if (
        not isinstance(operator_assertion, str)
        or operator_assertion.strip()
        != "I am the designated coordinator and explicitly authorize this exact-head merge."
    ):
        raise ValueError(
            "explicit designated-coordinator operator assertion is required"
        )

    current = ports.snapshot()
    if current.get("number") != number or current.get("state") != "OPEN":
        raise ValueError("PR is not the requested open pull request")
    if (
        current.get("head_sha"),
        current.get("base_sha"),
        current.get("head_tree_sha"),
        current.get("body_sha256"),
    ) != (head, base, head_tree, body_sha256):
        raise ValueError(
            "PR head, base, source tree, or body changed immediately before merge"
        )
    target_branch = current.get("base_ref")
    if use_decision and (not isinstance(target_branch, str) or not target_branch):
        raise ValueError("PR target branch name is missing or malformed")
    decision_metadata = None
    if use_decision:
        decision_metadata = _decision_receipt(
            ports,
            reference=decision_reference,
            expected_sha256=decision_sha256,
            repository=repository,
            number=number,
            head=head,
            base=base,
            head_tree=head_tree,
            merge_tree=tree,
            body_sha256=body_sha256,
            declarations=expected_declarations,
            target_branch=target_branch,
        )
    allow_required_checks_na = (
        decision_metadata is not None
        and decision_metadata["required_checks_disposition"] == "not_applicable"
    )
    report = readiness()
    pins = _review_ready(
        report,
        {"head_sha": head, "base_sha": base, "body_sha256": body_sha256},
        expected_declarations,
        repository,
        number,
        allow_required_checks_na=allow_required_checks_na,
    )
    if use_review:
        review = ports.review(review_reference)
        if review.get("state") != "APPROVED" or review.get("commit_sha") != head:
            raise ValueError(
                "referenced review is not an approval of the exact current head"
            )
    # Re-read declarations and remote gates after the first exact review read,
    # then perform one last compact PR/review and applicability read.
    final_report = readiness()
    final_pins = _review_ready(
        final_report,
        {"head_sha": head, "base_sha": base, "body_sha256": body_sha256},
        expected_declarations,
        repository,
        number,
        allow_required_checks_na=allow_required_checks_na,
    )
    if final_pins != pins:
        raise ValueError(
            "readiness declaration pins drifted during the merge preflight"
        )
    current = ports.snapshot()
    if current.get("number") != number or current.get("state") != "OPEN":
        raise ValueError("PR changed state at the merge boundary")
    if (
        current.get("head_sha"),
        current.get("base_sha"),
        current.get("head_tree_sha"),
        current.get("body_sha256"),
    ) != (head, base, head_tree, body_sha256):
        raise ValueError("PR pins changed at the merge boundary")
    if use_decision and current.get("base_ref") != target_branch:
        raise ValueError("PR target branch changed at the merge boundary")
    if use_review:
        review = ports.review(review_reference)
        if review.get("state") != "APPROVED" or review.get("commit_sha") != head:
            raise ValueError("review changed at the merge boundary")
    else:
        final_decision_metadata = _decision_receipt(
            ports,
            reference=decision_reference,
            expected_sha256=decision_sha256,
            repository=repository,
            number=number,
            head=head,
            base=base,
            head_tree=head_tree,
            merge_tree=tree,
            body_sha256=body_sha256,
            declarations=expected_declarations,
            target_branch=target_branch,
        )
        if final_decision_metadata != decision_metadata:
            raise ValueError("coordinator decision or applicability evidence changed")
    if dry_run:
        result = {
            "command": "merge",
            "status": "dry_run_ready",
            "merged": False,
            "operator_assertion": "operator-asserted; not independently authenticated",
            "review_reference": review_reference,
            "decision_reference": decision_reference,
            "head_sha": head,
            "head_tree_sha": head_tree,
            "merge_tree_sha": tree,
        }
        if use_decision:
            result["decision_sha256"] = decision_metadata["decision_sha256"]
        if allow_required_checks_na:
            result["required_checks_disposition"] = "not_applicable"
            result["applicability_observation_sha256"] = decision_metadata[
                "applicability_observation_sha256"
            ]
            result["applicability_reason"] = decision_metadata["applicability_reason"]
        return result

    try:
        response = ports.merge(head)
    except (UnknownCompletion, OSError, TimeoutError, ValueError) as exc:
        return {
            "command": "merge",
            "status": "unknown_completion",
            "merged": None,
            "error": type(exc).__name__,
            "retry": False,
        }
    if not isinstance(response, dict):
        return {
            "command": "merge",
            "status": "unknown_completion",
            "merged": None,
            "retry": False,
        }
    if "merged" not in response or type(response["merged"]) is not bool:
        return {
            "command": "merge",
            "status": "unknown_completion",
            "merged": None,
            "retry": False,
        }
    if response["merged"] is False:
        return {
            "command": "merge",
            "status": "rejected",
            "merged": False,
            "retry": False,
        }
    response_sha = response.get("sha")
    if not isinstance(response_sha, str) or not SHA1.fullmatch(response_sha):
        return {
            "command": "merge",
            "status": "terminal_verification_failed",
            "merged": None,
            "retry": False,
        }
    try:
        terminal = ports.terminal()
    except TerminalVerificationError:
        return {
            "command": "merge",
            "status": "terminal_verification_failed",
            "merged": None,
            "retry": False,
        }
    except (UnknownCompletion, OSError, TimeoutError, ValueError) as exc:
        return {
            "command": "merge",
            "status": "unknown_completion",
            "merged": None,
            "error": type(exc).__name__,
            "retry": False,
        }
    except (AttributeError, KeyError, TypeError):
        return {
            "command": "merge",
            "status": "terminal_verification_failed",
            "merged": None,
            "retry": False,
        }
    if (
        not isinstance(terminal, dict)
        or terminal.get("state") != "MERGED"
        or terminal.get("head_sha") != head
        or terminal.get("head_tree_sha") != head_tree
        or not isinstance(terminal.get("merge_commit_sha"), str)
        or not SHA1.fullmatch(terminal["merge_commit_sha"])
        or terminal.get("merge_commit_sha") != response_sha
        or terminal.get("merge_commit_object_sha") != terminal.get("merge_commit_sha")
        or terminal.get("merge_commit_tree_sha") != tree
        or terminal.get("parent_shas") != [base]
    ):
        return {
            "command": "merge",
            "status": "terminal_verification_failed",
            "merged": None,
            "retry": False,
        }
    result = {
        "command": "merge",
        "status": "merged_verified",
        "merged": True,
        "head_sha": head,
        "head_tree_sha": head_tree,
        "merge_tree_sha": tree,
        "merge_commit_sha": terminal["merge_commit_sha"],
        "review_reference": review_reference,
        "operator_assertion": "operator-asserted; not independently authenticated",
    }
    if use_decision:
        result["decision_reference"] = decision_reference
        result["decision_sha256"] = decision_metadata["decision_sha256"]
    if allow_required_checks_na:
        result["required_checks_disposition"] = "not_applicable"
        result["applicability_observation_sha256"] = decision_metadata[
            "applicability_observation_sha256"
        ]
        result["applicability_reason"] = decision_metadata["applicability_reason"]
    return result
