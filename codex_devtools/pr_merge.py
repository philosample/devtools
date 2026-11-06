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

SHA1 = re.compile(r"^[0-9a-f]{40}$")
REVIEW_REFERENCE = re.compile(
    r"^(?:[1-9][0-9]*|https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*#pullrequestreview-[1-9][0-9]*)$"
)
APPROVAL_GATE = "coordinator_approval"


class RemoteRejected(ValueError):
    """GitHub returned a definite rejection for a mutation request."""


class UnknownCompletion(ValueError):
    """The request may have reached GitHub but no terminal response was observed."""


class MergePorts(Protocol):
    def snapshot(self) -> dict[str, Any]: ...

    def review(self, reference: str) -> dict[str, Any]: ...

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
                command, capture_output=True, text=True, timeout=20, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UnknownCompletion("GitHub API request completion is unknown") from exc
        if completed.returncode:
            # gh's nonzero exit alone does not prove whether a mutation reached
            # GitHub before its response was lost.
            raise UnknownCompletion("GitHub API returned no trustworthy completion")
        if len(completed.stdout.encode("utf-8")) > 1_048_576:
            raise ValueError("GitHub API response exceeds size limit")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("GitHub API returned invalid JSON") from exc

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

    def merge(self, expected_head: str) -> dict[str, Any]:
        return self._api(
            f"repos/{self.repository}/pulls/{self.number}/merge",
            method="PUT",
            fields=(("sha", expected_head), ("merge_method", "squash")),
        )

    def terminal(self) -> dict[str, Any]:
        pr = self._api(f"repos/{self.repository}/pulls/{self.number}")
        result = {
            "state": "MERGED" if pr.get("merged") else pr["state"].upper(),
            "head_sha": pr["head"]["sha"],
            "merge_commit_sha": pr.get("merge_commit_sha"),
            "head_tree_sha": None,
        }
        if result["head_sha"]:
            commit = self._api(f"repos/{self.repository}/commits/{result['head_sha']}")
            result["head_tree_sha"] = commit["commit"]["tree"]["sha"]
        return result


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
    non_approval = [item for item in blockers if item.get("gate") != APPROVAL_GATE]
    if non_approval or sum(item.get("gate") == APPROVAL_GATE for item in blockers) != 1:
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
    if checks.get("status") != "passed" or checks.get("complete") is not True:
        raise ValueError("required CI observation is not complete and passing")
    if threads.get("status") != "resolved" or threads.get("complete") is not True:
        raise ValueError("review-thread observation is not complete and resolved")
    if receipts.get("status") != "verified" or receipts.get("complete") is not True:
        raise ValueError("required evidence receipts are not verified")
    return pins


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
    review_reference: str,
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
    if not isinstance(review_reference, str) or not REVIEW_REFERENCE.fullmatch(
        review_reference
    ):
        raise ValueError("review reference must identify one explicit GitHub review")
    if review_reference.startswith("https://") and not review_reference.startswith(
        f"https://github.com/{repository}/pull/{number}#pullrequestreview-"
    ):
        raise ValueError(
            "review reference belongs to another repository or pull request"
        )
    if (
        not isinstance(operator_assertion, str)
        or operator_assertion.strip()
        != "I am the designated coordinator and explicitly authorize this exact-head merge."
    ):
        raise ValueError(
            "explicit designated-coordinator operator assertion is required"
        )

    report = readiness()
    pins = _review_ready(
        report,
        {"head_sha": head, "base_sha": base, "body_sha256": body_sha256},
        expected_declarations,
        repository,
        number,
    )
    # Final independent reads close the interval between readiness and mutation.
    current = ports.snapshot()
    if current.get("number") != number or current.get("state") != "OPEN":
        raise ValueError("PR is not the requested open pull request")
    if (
        current.get("head_sha"),
        current.get("base_sha"),
        current.get("head_tree_sha"),
        current.get("body_sha256"),
    ) != (head, base, tree, body_sha256):
        raise ValueError(
            "PR head, base, tree, or body changed immediately before merge"
        )
    review = ports.review(review_reference)
    if review.get("state") != "APPROVED" or review.get("commit_sha") != head:
        raise ValueError(
            "referenced review is not an approval of the exact current head"
        )
    # Re-read declarations and remote gates after the first exact review read,
    # then perform one last compact PR/review read at the mutation boundary.
    final_report = readiness()
    final_pins = _review_ready(
        final_report,
        {"head_sha": head, "base_sha": base, "body_sha256": body_sha256},
        expected_declarations,
        repository,
        number,
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
    ) != (head, base, tree, body_sha256):
        raise ValueError("PR pins changed at the merge boundary")
    review = ports.review(review_reference)
    if review.get("state") != "APPROVED" or review.get("commit_sha") != head:
        raise ValueError("review changed at the merge boundary")
    if dry_run:
        return {
            "command": "merge",
            "status": "dry_run_ready",
            "merged": False,
            "operator_assertion": "operator-asserted; not independently authenticated",
            "review_reference": review_reference,
            "head_sha": head,
            "head_tree_sha": tree,
        }

    try:
        response = ports.merge(head)
    except RemoteRejected:
        return {
            "command": "merge",
            "status": "rejected",
            "merged": False,
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
    if not isinstance(response, dict) or response.get("merged") is not True:
        return {
            "command": "merge",
            "status": "rejected",
            "merged": False,
            "retry": False,
        }
    try:
        terminal = ports.terminal()
    except (UnknownCompletion, OSError, TimeoutError, ValueError) as exc:
        return {
            "command": "merge",
            "status": "unknown_completion",
            "merged": None,
            "error": type(exc).__name__,
            "retry": False,
        }
    if (
        terminal.get("state") != "MERGED"
        or terminal.get("head_sha") != head
        or terminal.get("head_tree_sha") != tree
        or not isinstance(terminal.get("merge_commit_sha"), str)
        or not SHA1.fullmatch(terminal["merge_commit_sha"])
    ):
        return {
            "command": "merge",
            "status": "terminal_verification_failed",
            "merged": None,
            "retry": False,
        }
    return {
        "command": "merge",
        "status": "merged_verified",
        "merged": True,
        "head_sha": head,
        "head_tree_sha": tree,
        "merge_commit_sha": terminal["merge_commit_sha"],
        "review_reference": review_reference,
        "operator_assertion": "operator-asserted; not independently authenticated",
    }
