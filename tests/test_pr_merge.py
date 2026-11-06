from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_devtools import check_applicability, pr_merge, pr_readiness
from codex_devtools.cli import main

HEAD = "1" * 40
BASE = "2" * 40
TREE = "3" * 40
HEAD_TREE = "5" * 40
BODY_HASH = hashlib.sha256(b"reviewed body").hexdigest()
REVIEW = "https://github.com/philosample/devtools/pull/75#pullrequestreview-9"
DECISION_REF = "https://github.com/philosample/devtools/pull/75#issuecomment-13"
ASSERTION = (
    "I am the designated coordinator and explicitly authorize this exact-head merge."
)
MERGE_SHA = "4" * 40
HEAD_REF = "codex/pr75-remote-check-na"


def decision_body(*, head=HEAD, extra=None):
    receipt = {
        "schema": pr_merge.DECISION_SCHEMA,
        "repository": "philosample/devtools",
        "pr_number": 75,
        "head_sha": head,
        "base_sha": BASE,
        "head_tree_sha": HEAD_TREE,
        "merge_tree_sha": TREE,
        "body_sha256": BODY_HASH,
        "policy_sha256": "a" * 64,
        "evidence_sha256": "b" * 64,
        "plan_sha256": "c" * 64,
        "operator_asserted": True,
    }
    if extra:
        receipt.update(extra)
    return json.dumps(receipt, separators=(",", ":"), sort_keys=True)


def na_observation(*, target_branch="main"):
    def endpoint(name, **fields):
        return {
            "endpoint": name,
            "api_version": check_applicability.API_VERSION,
            "status_code": 200,
            "body_sha256": "d" * 64,
            **fields,
        }

    branch_path = target_branch.replace("/", "%2F")
    return {
        "schema": check_applicability.SCHEMA,
        "repository": "philosample/devtools",
        "target_branch": target_branch,
        "target_sha": BASE,
        "head_sha": HEAD,
        "head_ref": HEAD_REF,
        "repository_private": True,
        "repository_owner_type": "User",
        "repository_observation": endpoint("repos/philosample/devtools"),
        "branch": endpoint(f"branches/{branch_path}", sha=BASE, protected=False),
        "required_checks": {
            "command": "gh pr checks --required --json",
            "head_ref": HEAD_REF,
            "head_sha": HEAD,
            "base_sha": BASE,
            "target_branch": target_branch,
            "exit_code": 0,
            "stdout_sha256": hashlib.sha256(b"[]\n").hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            "representation": {"kind": "structured_json"},
            "complete": True,
            "rows": [],
        },
        "workflows": endpoint(
            "actions/workflows?per_page=100",
            total_count=0,
            rows=[],
            complete=True,
            truncated=False,
        ),
        "check_runs": endpoint(
            f"commits/{HEAD}/check-runs?per_page=100",
            total_count=0,
            complete=True,
            truncated=False,
            rows=[],
        ),
        "statuses": endpoint(
            f"commits/{HEAD}/statuses?per_page=100",
            complete=True,
            truncated=False,
            rows=[],
        ),
        "aggregate_status": endpoint(
            f"commits/{HEAD}/status", state="pending", contexts=[]
        ),
        "unsupported_endpoints": [
            {
                "name": name,
                "endpoint": endpoint_path.format(branch=branch_path),
                "api_version": check_applicability.API_VERSION,
                "status_code": 403,
                "body_sha256": "f" * 64,
                "message": check_applicability.UPGRADE_MESSAGE,
                "classification": "feature_unsupported",
            }
            for name, endpoint_path in check_applicability.UNSUPPORTED_ENDPOINTS.items()
        ],
    }


def na_decision_body(*, target_branch="main", observation=None, extra=None):
    observation = observation or na_observation(target_branch=target_branch)
    receipt = {
        "schema": pr_merge.DECISION_SCHEMA_V2,
        "repository": "philosample/devtools",
        "pr_number": 75,
        "head_sha": HEAD,
        "base_sha": BASE,
        "head_tree_sha": HEAD_TREE,
        "merge_tree_sha": TREE,
        "body_sha256": BODY_HASH,
        "policy_sha256": "a" * 64,
        "evidence_sha256": "b" * 64,
        "plan_sha256": "c" * 64,
        "operator_asserted": True,
        "target_branch": target_branch,
        "target_sha": BASE,
        "required_checks_disposition": "not_applicable",
        "applicability_observation_sha256": check_applicability.canonical_hash(
            observation
        ),
        "applicability_reason": "Coordinator reviewed the empty required-check evidence.",
    }
    if extra:
        receipt.update(extra)
    return json.dumps(receipt, separators=(",", ":"), sort_keys=True)


DECISION_BODY = decision_body()
DECISION_HASH = hashlib.sha256(DECISION_BODY.encode()).hexdigest()


def completed_http(command, status, payload, *, link=None, as_text=False):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
    headers = b"Content-Type: application/json\r\n"
    if link is not None:
        headers += f"Link: {link}\r\n".encode("ascii")
    output = (
        f"HTTP/2.0 {status} {'OK' if status == 200 else 'Forbidden'}\r\n".encode()
        + headers
        + b"\r\n"
        + body
    )
    if as_text:
        output = output.decode("utf-8")
    return subprocess.CompletedProcess(command, 0 if status == 200 else 1, output, b"")


def ready_report():
    snapshot = {
        "head_ref": HEAD_REF,
        "base_ref": "main",
        "head_sha": HEAD,
        "base_sha": BASE,
        "body_sha256": BODY_HASH,
    }
    return {
        "command": "review-ready",
        "ready": False,
        "repository": "philosample/devtools",
        "pr_number": 75,
        "coordinator_approval": {"status": "unknown"},
        "pins": {
            "policy_sha256": "a" * 64,
            "evidence_sha256": "b" * 64,
            "plan_sha256": "c" * 64,
        },
        "blockers": [{"gate": "coordinator_approval"}],
        "pr_snapshot_before": snapshot,
        "pr_snapshot_after": snapshot,
        "github_required_checks": {"status": "passed", "complete": True},
        "review_threads": {"status": "resolved", "complete": True},
        "receipt_verification": {"status": "verified", "complete": True},
    }


def ready_report_checks_unknown(*, target_branch="main"):
    report = ready_report()
    reason = "no required checks were returned; absence is ambiguous"
    required = na_observation(target_branch=target_branch)["required_checks"]
    report["pr_snapshot_before"]["base_ref"] = target_branch
    report["pr_snapshot_after"]["base_ref"] = target_branch
    report["github_required_checks"] = {
        "status": "unknown",
        "complete": False,
        "checks": [],
        "reason": reason,
        "transport": {
            "exit_code": required["exit_code"],
            "stdout_sha256": required["stdout_sha256"],
            "stderr_sha256": required["stderr_sha256"],
        },
    }
    report["blockers"].append(
        {
            "gate": "github_required_checks",
            "status": "unknown",
            "reason": reason,
        }
    )
    return report


class FakePorts:
    def __init__(self):
        self.snapshot_row = {
            "number": 75,
            "state": "OPEN",
            "head_sha": HEAD,
            "head_ref": HEAD_REF,
            "base_sha": BASE,
            "base_ref": "main",
            "head_tree_sha": HEAD_TREE,
            "body_sha256": BODY_HASH,
        }
        self.review_row = {"state": "APPROVED", "commit_sha": HEAD}
        self.merge_response = {"merged": True, "sha": MERGE_SHA}
        self.terminal_row = {
            "state": "MERGED",
            "head_sha": HEAD,
            "head_tree_sha": HEAD_TREE,
            "merge_commit_sha": MERGE_SHA,
            "merge_commit_object_sha": MERGE_SHA,
            "merge_commit_tree_sha": TREE,
            "parent_shas": [BASE],
        }
        self.decision_body = DECISION_BODY
        self.decision_bodies: list[str] | None = None
        self.applicability_observation = na_observation()
        self.applicability_observations: list[dict] | None = None
        self.applicability_calls = 0
        self.snapshot_calls = 0
        self.snapshot_rows: list[dict] | None = None
        self.review_calls = 0
        self.decision_calls = 0
        self.merge_calls: list[str] = []
        self.terminal_calls = 0
        self.raise_merge = False
        self.raise_merge_value_error = False
        self.raise_terminal = False
        self.raise_terminal_identity = False

    def snapshot(self):
        index = self.snapshot_calls
        self.snapshot_calls += 1
        if self.snapshot_rows is not None:
            return self.snapshot_rows[index]
        return self.snapshot_row

    def review(self, reference):
        self.review_calls += 1
        self.asserted_reference = reference
        return self.review_row

    def merge(self, expected_head):
        self.merge_calls.append(expected_head)
        if self.raise_merge:
            raise TimeoutError
        if self.raise_merge_value_error:
            raise ValueError("remote outcome is ambiguous")
        return self.merge_response

    def decision(self, reference):
        index = self.decision_calls
        self.decision_calls += 1
        body = (
            self.decision_bodies[index]
            if self.decision_bodies is not None
            else self.decision_body
        )
        return {
            "id": "13",
            "html_url": DECISION_REF,
            "issue_number": 75,
            "body": body,
        }

    def check_applicability(self, *, target_branch, target_sha, head_sha, head_ref):
        index = self.applicability_calls
        self.applicability_calls += 1
        if self.applicability_observations is not None:
            observation = self.applicability_observations[index]
        else:
            observation = self.applicability_observation
        return observation

    def terminal(self):
        self.terminal_calls += 1
        if self.raise_terminal:
            raise TimeoutError
        if self.raise_terminal_identity:
            raise pr_merge.TerminalVerificationError
        return self.terminal_row


class PRMergeTests(unittest.TestCase):
    def invoke(self, ports, readiness=None, **updates):
        args = {
            "repository": "philosample/devtools",
            "number": 75,
            "expected_head": HEAD,
            "expected_base": BASE,
            "expected_tree": TREE,
            "expected_head_tree": HEAD_TREE,
            "body_sha256": BODY_HASH,
            "expected_policy_sha256": "a" * 64,
            "expected_evidence_sha256": "b" * 64,
            "expected_plan_sha256": "c" * 64,
            "review_reference": REVIEW,
            "decision_reference": None,
            "decision_sha256": None,
            "operator_assertion": ASSERTION,
            "readiness": readiness or ready_report,
            "ports": ports,
            "dry_run": False,
        }
        args.update(updates)
        return pr_merge.merge_pr(**args)

    def test_positive_exact_head_single_merge_and_terminal_verification(self):
        ports = FakePorts()
        readiness_calls = 0

        def readiness():
            nonlocal readiness_calls
            readiness_calls += 1
            return ready_report()

        result = self.invoke(ports, readiness=readiness)
        self.assertEqual(result["status"], "merged_verified")
        self.assertEqual(ports.merge_calls, [HEAD])
        self.assertEqual(ports.snapshot_calls, 2)
        self.assertEqual(ports.review_calls, 2)
        self.assertEqual(ports.decision_calls, 0)
        self.assertEqual(readiness_calls, 2)
        self.assertEqual(ports.terminal_calls, 1)

    def test_fresh_head_base_tree_and_body_drift_block_before_merge(self):
        for field, value in (
            ("head_sha", "5" * 40),
            ("base_sha", "6" * 40),
            ("head_tree_sha", "7" * 40),
            ("body_sha256", "8" * 64),
        ):
            with self.subTest(field=field):
                ports = FakePorts()
                ports.snapshot_row[field] = value
                with self.assertRaises(ValueError):
                    self.invoke(ports)
                self.assertEqual(ports.merge_calls, [])

    def test_readiness_pin_and_evidence_blockers_prevent_merge(self):
        for field, changed in (
            ("head_sha", "9" * 40),
            ("base_sha", "a" * 40),
            ("body_sha256", "b" * 64),
        ):
            with self.subTest(field=field):
                report = ready_report()
                report["pr_snapshot_after"][field] = changed
                ports = FakePorts()
                with self.assertRaises(ValueError):
                    self.invoke(ports, readiness=lambda report=report: report)
                self.assertEqual(ports.merge_calls, [])

    def test_changed_evidence_pin_against_coordinator_reviewed_value_never_merges(self):
        report = ready_report()
        report["pins"]["evidence_sha256"] = "f" * 64
        ports = FakePorts()
        with self.assertRaisesRegex(
            ValueError, "differs from the coordinator-reviewed pin"
        ):
            self.invoke(ports, readiness=lambda: report)
        self.assertEqual(ports.merge_calls, [])
        self.assertEqual(ports.snapshot_calls, 1)

    def test_declaration_pin_drift_between_fresh_readiness_calls_never_merges(self):
        first = ready_report()
        second = ready_report()
        second["pins"]["evidence_sha256"] = "f" * 64
        reports = iter((first, second))
        ports = FakePorts()
        with self.assertRaisesRegex(
            ValueError, "differs from the coordinator-reviewed pin"
        ):
            self.invoke(ports, readiness=lambda: next(reports))
        self.assertEqual(ports.merge_calls, [])
        self.assertEqual(ports.snapshot_calls, 1)

    def test_missing_review_wrong_head_or_nonapproval_blocks(self):
        for review in (
            {},
            {"state": "COMMENTED", "commit_sha": HEAD},
            {"state": "APPROVED", "commit_sha": "c" * 40},
        ):
            with self.subTest(review=review):
                ports = FakePorts()
                ports.review_row = review
                with self.assertRaises(ValueError):
                    self.invoke(ports)
                self.assertEqual(ports.merge_calls, [])

    def test_unresolved_required_test_or_hold_blocks_merge(self):
        for blocker in (
            {"gate": "required_check", "reason": "failed"},
            {"gate": "active_hold", "reason": "hold"},
            {"gate": "review_findings", "reason": "unresolved"},
        ):
            with self.subTest(blocker=blocker):
                report = ready_report()
                report["blockers"].append(blocker)
                ports = FakePorts()
                with self.assertRaises(ValueError):
                    self.invoke(ports, readiness=lambda report=report: report)
                self.assertEqual(ports.merge_calls, [])

    def test_unknown_ci_threads_or_evidence_block_even_without_report_blocker(self):
        for field, replacement in (
            ("github_required_checks", {"status": "unknown", "complete": False}),
            ("review_threads", {"status": "unresolved", "complete": True}),
            ("receipt_verification", {"status": "failed", "complete": False}),
        ):
            with self.subTest(field=field):
                report = ready_report()
                report[field] = replacement
                ports = FakePorts()
                with self.assertRaises(ValueError):
                    self.invoke(ports, readiness=lambda report=report: report)
                self.assertEqual(ports.merge_calls, [])

    def test_forged_authentication_claim_is_not_accepted(self):
        report = ready_report()
        report["coordinator_approval"] = {
            "status": "authenticated",
            "identity": "forged-worker-claim",
        }
        ports = FakePorts()
        with self.assertRaisesRegex(ValueError, "authentication claim"):
            self.invoke(ports, readiness=lambda: report)
        self.assertEqual(ports.merge_calls, [])

    def test_wrong_pull_request_review_url_and_operator_assertion_block(self):
        for updates in (
            {
                "review_reference": "https://github.com/elsewhere/devtools/pull/75#pullrequestreview-9"
            },
            {"operator_assertion": "authenticated=true"},
        ):
            with self.subTest(updates=updates):
                ports = FakePorts()
                with self.assertRaises(ValueError):
                    self.invoke(ports, **updates)
                self.assertEqual(ports.merge_calls, [])

    def test_dry_run_is_pure_and_still_checks_exact_review(self):
        ports = FakePorts()
        result = self.invoke(ports, dry_run=True)
        self.assertEqual(result["status"], "dry_run_ready")
        self.assertEqual(ports.merge_calls, [])
        self.assertEqual(ports.terminal_calls, 0)
        self.assertEqual(ports.snapshot_calls, 2)
        self.assertEqual(ports.review_calls, 2)

    def test_rejected_and_unknown_network_completion_never_retry(self):
        ports = FakePorts()
        ports.merge_response = {"merged": False}
        result = self.invoke(ports)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["retry"])
        self.assertFalse(result["merged"])
        self.assertEqual(ports.merge_calls, [HEAD])
        for response in (None, "malformed", {}, {"merged": None}, {"merged": 1}):
            with self.subTest(response=response):
                ports = FakePorts()
                ports.merge_response = response
                result = self.invoke(ports)
                self.assertEqual(result["status"], "unknown_completion")
                self.assertIsNone(result["merged"])
                self.assertFalse(result["retry"])
                self.assertEqual(ports.merge_calls, [HEAD])
                self.assertEqual(ports.terminal_calls, 0)
        ports = FakePorts()
        ports.raise_merge = True
        result = self.invoke(ports)
        self.assertEqual(result["status"], "unknown_completion")
        self.assertEqual(result["retry"], False)
        self.assertEqual(ports.merge_calls, [HEAD])
        ports = FakePorts()
        ports.raise_merge_value_error = True
        result = self.invoke(ports)
        self.assertEqual(result["status"], "unknown_completion")
        self.assertEqual(result["merged"], None)
        self.assertEqual(result["retry"], False)
        self.assertEqual(ports.merge_calls, [HEAD])

    def test_terminal_head_or_tree_mismatch_does_not_report_verified_merge(self):
        for field, value in (
            ("head_sha", "d" * 40),
            ("head_tree_sha", "e" * 40),
            ("merge_commit_object_sha", "f" * 40),
            ("merge_commit_tree_sha", "f" * 40),
            ("merge_commit_sha", None),
        ):
            with self.subTest(field=field):
                ports = FakePorts()
                ports.terminal_row[field] = value
                result = self.invoke(ports)
                self.assertEqual(result["status"], "terminal_verification_failed")
                self.assertIsNone(result["merged"])
                self.assertFalse(result["retry"])
                self.assertEqual(ports.merge_calls, [HEAD])
                self.assertEqual(ports.terminal_calls, 1)

    def test_unavailable_terminal_read_is_unknown_and_never_retried(self):
        ports = FakePorts()
        ports.raise_terminal = True
        result = self.invoke(ports)
        self.assertEqual(result["status"], "unknown_completion")
        self.assertFalse(result["retry"])
        self.assertEqual(ports.merge_calls, [HEAD])
        self.assertEqual(ports.terminal_calls, 1)

    def test_fetched_terminal_object_identity_failure_never_retries(self):
        ports = FakePorts()
        ports.raise_terminal_identity = True
        result = self.invoke(ports)
        self.assertEqual(result["status"], "terminal_verification_failed")
        self.assertIsNone(result["merged"])
        self.assertFalse(result["retry"])
        self.assertEqual(ports.merge_calls, [HEAD])
        self.assertEqual(ports.terminal_calls, 1)

    def test_terminal_merge_tree_mismatch_fails_when_source_tree_is_unchanged(self):
        ports = FakePorts()
        result = self.invoke(ports)
        self.assertEqual(ports.snapshot_row["head_tree_sha"], HEAD_TREE)
        self.assertEqual(ports.terminal_row["head_tree_sha"], HEAD_TREE)
        self.assertEqual(result["status"], "merged_verified")

        ports = FakePorts()
        ports.terminal_row["merge_commit_tree_sha"] = "f" * 40
        result = self.invoke(ports)
        self.assertEqual(ports.snapshot_row["head_tree_sha"], HEAD_TREE)
        self.assertEqual(ports.terminal_row["head_tree_sha"], HEAD_TREE)
        self.assertEqual(result["status"], "terminal_verification_failed")
        self.assertIsNone(result["merged"])
        self.assertEqual(ports.merge_calls, [HEAD])

    def test_terminal_merge_commit_must_match_positive_merge_response_sha(self):
        ports = FakePorts()
        ports.merge_response["sha"] = "e" * 40
        result = self.invoke(ports)
        self.assertEqual(result["status"], "terminal_verification_failed")
        self.assertIsNone(result["merged"])
        self.assertEqual(ports.merge_calls, [HEAD])

    def test_github_terminal_port_fetches_the_actual_merge_commit_tree(self):
        pr = {
            "head": {"sha": HEAD},
            "merged": True,
            "state": "closed",
            "merge_commit_sha": MERGE_SHA,
        }
        source_commit = {"commit": {"tree": {"sha": HEAD_TREE}}}
        merge_commit = {
            "sha": MERGE_SHA,
            "commit": {"tree": {"sha": TREE}},
            "parents": [{"sha": BASE}],
        }
        with patch.object(
            pr_merge.GHMergePorts,
            "_api",
            side_effect=(pr, source_commit, merge_commit),
        ) as api:
            terminal = pr_merge.GHMergePorts("philosample/devtools", 75).terminal()
        self.assertEqual(terminal["merge_commit_sha"], MERGE_SHA)
        self.assertEqual(terminal["merge_commit_object_sha"], MERGE_SHA)
        self.assertEqual(terminal["head_tree_sha"], HEAD_TREE)
        self.assertEqual(terminal["merge_commit_tree_sha"], TREE)
        self.assertEqual(terminal["parent_shas"], [BASE])
        self.assertEqual(
            api.call_args_list[2].args,
            (f"repos/philosample/devtools/commits/{MERGE_SHA}",),
        )

    def test_github_terminal_port_rejects_fetched_object_sha_mismatch(self):
        pr = {
            "head": {"sha": HEAD},
            "merged": True,
            "state": "closed",
            "merge_commit_sha": MERGE_SHA,
        }
        source_commit = {"commit": {"tree": {"sha": HEAD_TREE}}}
        merge_commit = {
            "sha": "e" * 40,
            "commit": {"tree": {"sha": TREE}},
            "parents": [{"sha": BASE}],
        }
        with (
            patch.object(
                pr_merge.GHMergePorts,
                "_api",
                side_effect=(pr, source_commit, merge_commit),
            ) as api,
            self.assertRaises(pr_merge.TerminalVerificationError),
        ):
            pr_merge.GHMergePorts("philosample/devtools", 75).terminal()
        self.assertEqual(api.call_count, 3)

    def test_api_normalizes_completed_process_bytes_and_text(self):
        payload = {"private": True, "owner": {"type": "User"}}
        for stdout in (json.dumps(payload).encode(), json.dumps(payload)):
            with self.subTest(stdout_type=type(stdout).__name__):
                completed = subprocess.CompletedProcess([], 0, stdout, b"")
                with patch(
                    "codex_devtools.pr_merge.subprocess.run", return_value=completed
                ):
                    result = pr_merge.GHMergePorts("philosample/devtools", 86)._api(
                        "repos/philosample/devtools"
                    )
                self.assertEqual(result, payload)

    def test_api_http_error_is_unknown_without_stdout_type_crash(self):
        completed = subprocess.CompletedProcess(
            [], 1, b'{"message":"Resource not accessible"}\n', b"HTTP 403"
        )
        with (
            patch("codex_devtools.pr_merge.subprocess.run", return_value=completed),
            self.assertRaises(pr_merge.UnknownCompletion),
        ):
            pr_merge.GHMergePorts("philosample/devtools", 86)._api(
                "repos/philosample/devtools"
            )

    def test_included_api_normalizes_http_200_and_known_403_and_hashes_raw_body(self):
        for status, payload, stdout in (
            (200, {"ok": True}, completed_http([], 200, {"ok": True}).stdout),
            (
                403,
                {"message": check_applicability.UPGRADE_MESSAGE},
                completed_http(
                    [],
                    403,
                    {"message": check_applicability.UPGRADE_MESSAGE},
                    as_text=True,
                ).stdout,
            ),
        ):
            with self.subTest(status=status):
                if status == 403:
                    expected_body = (
                        json.dumps(payload, separators=(",", ":")).encode() + b"\n"
                    )
                else:
                    expected_body = (
                        json.dumps(payload, separators=(",", ":")).encode() + b"\n"
                    )
                completed = subprocess.CompletedProcess(
                    [], 0 if status == 200 else 1, stdout, b""
                )
                with patch(
                    "codex_devtools.pr_merge.subprocess.run", return_value=completed
                ):
                    response = pr_merge.GHMergePorts(
                        "philosample/devtools", 86
                    )._included_api("repos/philosample/devtools")
                self.assertEqual(response["status_code"], status)
                self.assertEqual(response["payload"], payload)
                self.assertEqual(
                    response["body_sha256"], hashlib.sha256(expected_body).hexdigest()
                )

    def test_included_api_transport_error_is_not_reported_as_size_limit(self):
        completed = subprocess.CompletedProcess([], 1, b"", b"network error")
        with (
            patch("codex_devtools.pr_merge.subprocess.run", return_value=completed),
            self.assertRaisesRegex(ValueError, "omitted HTTP status"),
        ):
            pr_merge.GHMergePorts("philosample/devtools", 86)._included_api(
                "repos/philosample/devtools"
            )
        completed = subprocess.CompletedProcess([], 0, 1, b"")
        with (
            patch("codex_devtools.pr_merge.subprocess.run", return_value=completed),
            self.assertRaisesRegex(ValueError, "stdout must be bytes or text"),
        ):
            pr_merge.GHMergePorts("philosample/devtools", 86)._included_api(
                "repos/philosample/devtools"
            )

    def test_link_parser_uses_relations_not_page_query_text(self):
        self.assertTrue(
            pr_merge._has_next_link(
                '<https://api.github.com/repos/a/b/commits/x/check-runs?per_page=100&page=2>; rel="next", '
                '<https://api.github.com/repos/a/b/commits/x/check-runs?per_page=100&page=1>; rel="prev"'
            )
        )
        self.assertFalse(
            pr_merge._has_next_link(
                '<https://api.github.com/repos/a/b/commits/x/check-runs?per_page=100&page=1>; rel="prev"'
            )
        )
        self.assertTrue(
            pr_merge._has_next_link(
                '<https://api.github.com/repos/a/b/checks?page=2>; rel="prev next"'
            )
        )
        with self.assertRaisesRegex(ValueError, "Link header entry is malformed"):
            pr_merge._has_next_link("not a Link entry")

    def test_read_only_snapshot_and_collector_parse_real_transport_shapes(self):
        pr_payload = {
            "number": 86,
            "state": "open",
            "merged": False,
            "head": {"sha": HEAD, "ref": HEAD_REF},
            "base": {"sha": BASE, "ref": "main"},
            "body": "reviewed body",
        }
        snapshot_commit = {"commit": {"tree": {"sha": HEAD_TREE}}}
        pins = {
            "repository": "philosample/devtools",
            "number": 86,
            "expected_head": HEAD,
            "expected_base": BASE,
            "expected_head_tree": HEAD_TREE,
            "body_sha256": BODY_HASH,
            "expected_policy_sha256": "a" * 64,
            "expected_evidence_sha256": "b" * 64,
            "expected_plan_sha256": "c" * 64,
        }

        def fake_run_for(link_header, required_response=None):
            calls = 0

            def fake_run(command, **_kwargs):
                nonlocal calls
                calls += 1
                if command[:3] == ["gh", "pr", "checks"]:
                    if required_response is not None:
                        return subprocess.CompletedProcess(
                            command,
                            required_response.returncode,
                            required_response.stdout,
                            required_response.stderr,
                        )
                    stdout = b"[]\n" if calls % 2 else "[]\n"
                    return subprocess.CompletedProcess(command, 0, stdout, b"")
                endpoint = command[-1]
                if (
                    len(command) > 2
                    and command[1] == "api"
                    and "--include" not in command
                ):
                    payload = (
                        pr_payload
                        if endpoint.endswith("/pulls/86")
                        else snapshot_commit
                    )
                    raw = json.dumps(payload).encode()
                    return subprocess.CompletedProcess(
                        command, 0, raw if calls % 2 else raw.decode(), b""
                    )
                if endpoint == "repos/philosample/devtools":
                    status, payload = 200, {"private": True, "owner": {"type": "User"}}
                elif endpoint == "repos/philosample/devtools/branches/main":
                    status, payload = 200, {"commit": {"sha": BASE}, "protected": False}
                elif endpoint.endswith("/actions/workflows?per_page=100"):
                    status, payload = 200, {"total_count": 0, "workflows": []}
                elif endpoint.endswith("/check-runs?per_page=100"):
                    status, payload = 200, {"total_count": 0, "check_runs": []}
                elif endpoint.endswith("/statuses?per_page=100"):
                    status, payload = 200, []
                elif endpoint.endswith("/status"):
                    status, payload = 200, {"state": "pending", "statuses": []}
                else:
                    status, payload = (
                        403,
                        {"message": check_applicability.UPGRADE_MESSAGE},
                    )
                next_link = (
                    link_header
                    if endpoint.endswith("/check-runs?per_page=100")
                    else None
                )
                return completed_http(
                    command,
                    status,
                    payload,
                    link=next_link,
                    as_text=bool(calls % 2),
                )

            return fake_run

        for link_header in (
            None,
            (
                "<https://api.github.com/repos/philosample/devtools/commits/"
                f'{HEAD}/check-runs?per_page=100&page=1>; rel="prev"'
            ),
        ):
            with self.subTest(link=link_header):
                ports = pr_merge.GHMergePorts("philosample/devtools", 86)
                with patch(
                    "codex_devtools.pr_merge.subprocess.run",
                    side_effect=fake_run_for(link_header),
                ):
                    result = pr_merge.check_applicability_report(**pins, ports=ports)
                self.assertEqual(result["status"], "candidate_evidence")
                self.assertFalse(result["merge_authorized"])
                self.assertFalse(result["ci_passed"])

        sentinel = subprocess.CompletedProcess(
            ["gh", "pr", "checks"],
            1,
            b"",
            f"no checks reported on the '{HEAD_REF}' branch\n".encode(),
        )
        ports = pr_merge.GHMergePorts("philosample/devtools", 86)
        with patch(
            "codex_devtools.pr_merge.subprocess.run",
            side_effect=fake_run_for(None, sentinel),
        ):
            result = pr_merge.check_applicability_report(**pins, ports=ports)
        required = result["observation"]["required_checks"]
        self.assertEqual(result["head_ref"], HEAD_REF)
        self.assertEqual(
            required["representation"],
            {"kind": "gh_no_checks_reported", "branch": HEAD_REF},
        )
        self.assertEqual(required["head_sha"], HEAD)
        self.assertEqual(required["base_sha"], BASE)
        self.assertEqual(required["target_branch"], "main")
        self.assertEqual(required["stdout_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(
            required["stderr_sha256"], hashlib.sha256(sentinel.stderr).hexdigest()
        )
        check_applicability.validate_na_observation(
            result["observation"],
            repository="philosample/devtools",
            target_branch="main",
            target_sha=BASE,
            head_sha=HEAD,
            head_ref=HEAD_REF,
        )

        next_link = (
            f"<https://api.github.com/repos/philosample/devtools/commits/{HEAD}"
            '/check-runs?per_page=100&page=2>; rel="next"'
        )
        ports = pr_merge.GHMergePorts("philosample/devtools", 86)
        with (
            patch(
                "codex_devtools.pr_merge.subprocess.run",
                side_effect=fake_run_for(next_link),
            ),
            self.assertRaisesRegex(ValueError, "inventory is not valid-empty"),
        ):
            pr_merge.check_applicability_report(**pins, ports=ports)

    def test_github_applicability_adapter_builds_slash_branch_observation(self):
        def fake_run(command, **_kwargs):
            if command[:3] == ["gh", "pr", "checks"]:
                return subprocess.CompletedProcess(command, 0, b"[]\n", b"")
            endpoint = command[-1]
            if endpoint == "repos/philosample/devtools":
                status, payload = 200, {"private": True, "owner": {"type": "User"}}
            elif endpoint == "repos/philosample/devtools/branches/release%2F2026":
                status, payload = (
                    200,
                    {
                        "commit": {"sha": BASE},
                        "protected": False,
                    },
                )
            elif endpoint.endswith("/actions/workflows?per_page=100"):
                status, payload = 200, {"total_count": 0, "workflows": []}
            elif endpoint.endswith("/check-runs?per_page=100"):
                status, payload = 200, {"total_count": 0, "check_runs": []}
            elif endpoint.endswith("/statuses?per_page=100"):
                status, payload = 200, []
            elif endpoint.endswith("/status"):
                status, payload = 200, {"state": "pending", "statuses": []}
            else:
                status, payload = 403, {"message": check_applicability.UPGRADE_MESSAGE}
            output = (
                f"HTTP/2.0 {status} {'OK' if status == 200 else 'Forbidden'}\n"
                "Content-Type: application/json\n\n" + json.dumps(payload)
            ).encode()
            return subprocess.CompletedProcess(
                command, 0 if status == 200 else 1, output, b""
            )

        ports = pr_merge.GHMergePorts("philosample/devtools", 75)
        with patch("codex_devtools.pr_merge.subprocess.run", side_effect=fake_run):
            observation = ports.check_applicability(
                target_branch="release/2026",
                target_sha=BASE,
                head_sha=HEAD,
                head_ref=HEAD_REF,
            )
        self.assertEqual(observation["branch"]["endpoint"], "branches/release%2F2026")
        self.assertEqual(
            check_applicability.validate_na_observation(
                observation,
                repository="philosample/devtools",
                target_branch="release/2026",
                target_sha=BASE,
                head_sha=HEAD,
                head_ref=HEAD_REF,
            ),
            check_applicability.canonical_hash(observation),
        )

    def test_github_applicability_adapter_does_not_classify_generic_403(self):
        def fake_run(command, **_kwargs):
            if command[:3] == ["gh", "pr", "checks"]:
                return subprocess.CompletedProcess(command, 0, b"[]\n", b"")
            endpoint = command[-1]
            if endpoint == "repos/philosample/devtools":
                status, payload = 200, {"private": True, "owner": {"type": "User"}}
            elif endpoint == "repos/philosample/devtools/branches/main":
                status, payload = 200, {"commit": {"sha": BASE}, "protected": False}
            elif endpoint.endswith("/actions/workflows?per_page=100"):
                status, payload = 200, {"total_count": 0, "workflows": []}
            elif endpoint.endswith("/check-runs?per_page=100"):
                status, payload = 200, {"total_count": 0, "check_runs": []}
            elif endpoint.endswith("/statuses?per_page=100"):
                status, payload = 200, []
            elif endpoint.endswith("/status"):
                status, payload = 200, {"state": "pending", "statuses": []}
            elif endpoint.endswith("/branches/main/protection"):
                status, payload = (
                    403,
                    {"message": "Resource not accessible by integration"},
                )
            else:
                status, payload = 403, {"message": check_applicability.UPGRADE_MESSAGE}
            output = (
                f"HTTP/2.0 {status} {'OK' if status == 200 else 'Forbidden'}\n"
                "Content-Type: application/json\n\n" + json.dumps(payload)
            ).encode()
            return subprocess.CompletedProcess(
                command, 0 if status == 200 else 1, output, b""
            )

        ports = pr_merge.GHMergePorts("philosample/devtools", 75)
        with patch("codex_devtools.pr_merge.subprocess.run", side_effect=fake_run):
            observation = ports.check_applicability(
                target_branch="main",
                target_sha=BASE,
                head_sha=HEAD,
                head_ref=HEAD_REF,
            )
        with self.assertRaisesRegex(ValueError, "exact private-repository upgrade"):
            check_applicability.validate_na_observation(
                observation,
                repository="philosample/devtools",
                target_branch="main",
                target_sha=BASE,
                head_sha=HEAD,
                head_ref=HEAD_REF,
            )

    def invoke_decision(self, ports, **updates):
        updates.setdefault("review_reference", None)
        updates.setdefault("decision_reference", DECISION_REF)
        updates.setdefault(
            "decision_sha256",
            hashlib.sha256(ports.decision_body.encode()).hexdigest(),
        )
        return self.invoke(ports, **updates)

    def test_structured_decision_comment_is_pinned_and_re_read_before_merge(self):
        ports = FakePorts()
        result = self.invoke_decision(ports)
        self.assertEqual(result["status"], "merged_verified")
        self.assertEqual(ports.decision_calls, 2)
        self.assertEqual(ports.review_calls, 0)
        self.assertEqual(ports.merge_calls, [HEAD])
        self.assertEqual(result["decision_reference"], DECISION_REF)
        self.assertEqual(result["decision_sha256"], DECISION_HASH)

    def test_v2_na_receipt_allows_only_required_check_gate_and_reports_na(self):
        ports = FakePorts()
        ports.decision_body = na_decision_body()
        result = self.invoke_decision(ports, readiness=ready_report_checks_unknown)
        self.assertEqual(result["status"], "merged_verified")
        self.assertEqual(result["required_checks_disposition"], "not_applicable")
        self.assertEqual(
            result["applicability_observation_sha256"],
            check_applicability.canonical_hash(na_observation()),
        )
        self.assertEqual(ports.merge_calls, [HEAD])
        self.assertEqual(ports.applicability_calls, 2)
        self.assertEqual(ports.decision_calls, 2)

    def test_review_ready_check_unknown_stays_blocked_without_v2_na(self):
        ports = FakePorts()
        with self.assertRaisesRegex(ValueError, "required gates"):
            self.invoke(ports, readiness=ready_report_checks_unknown)
        self.assertEqual(ports.merge_calls, [])
        self.assertEqual(ports.applicability_calls, 0)

    def test_v2_receipt_binds_slash_branch_and_encoded_endpoint(self):
        ports = FakePorts()
        ports.snapshot_row["base_ref"] = "release/2026"
        ports.applicability_observation = na_observation(target_branch="release/2026")
        ports.decision_body = na_decision_body(target_branch="release/2026")
        result = self.invoke_decision(
            ports,
            readiness=lambda: ready_report_checks_unknown(target_branch="release/2026"),
        )
        self.assertEqual(result["status"], "merged_verified")
        self.assertEqual(result["required_checks_disposition"], "not_applicable")

    def test_changed_or_malformed_applicability_evidence_blocks_mutation(self):
        mutations = (
            (
                "branch SHA",
                lambda observation: observation["branch"].update(sha="f" * 40),
            ),
            (
                "source ref mismatch",
                lambda observation: observation.update(head_ref="codex/wrong-source"),
            ),
            (
                "required-check source identity mismatch",
                lambda observation: observation["required_checks"].update(
                    head_ref="codex/wrong-source"
                ),
            ),
            (
                "required-check base identity mismatch",
                lambda observation: observation["required_checks"].update(
                    base_sha="f" * 40
                ),
            ),
            (
                "required-check target branch mismatch",
                lambda observation: observation["required_checks"].update(
                    target_branch="release/changed"
                ),
            ),
            (
                "required check present",
                lambda observation: observation["required_checks"].update(
                    rows=[{"name": "required"}]
                ),
            ),
            (
                "protected",
                lambda observation: observation["branch"].update(protected=True),
            ),
            (
                "generic 403",
                lambda observation: observation["unsupported_endpoints"][0].update(
                    message="Resource not accessible", classification="unknown"
                ),
            ),
            (
                "unexpected 404",
                lambda observation: observation["unsupported_endpoints"][1].update(
                    status_code=404, classification="unknown"
                ),
            ),
            (
                "nonempty check runs",
                lambda observation: observation["check_runs"].update(
                    rows=[{"status": "success"}]
                ),
            ),
            (
                "nonempty status contexts",
                lambda observation: observation["statuses"].update(
                    rows=[{"state": "success"}]
                ),
            ),
            (
                "truncated check runs",
                lambda observation: observation["check_runs"].update(
                    complete=False, truncated=True
                ),
            ),
            (
                "contradictory empty check-run count",
                lambda observation: observation["check_runs"].update(total_count=1),
            ),
            (
                "workflow present",
                lambda observation: observation["workflows"].update(
                    total_count=1, rows=[{"id": 1}]
                ),
            ),
            (
                "unknown repository visibility",
                lambda observation: observation.update(repository_private=None),
            ),
            (
                "missing protected field",
                lambda observation: observation["branch"].pop("protected"),
            ),
            (
                "missing aggregate endpoint",
                lambda observation: observation.pop("aggregate_status"),
            ),
            (
                "duplicate unsupported endpoint",
                lambda observation: observation["unsupported_endpoints"].__setitem__(
                    1, observation["unsupported_endpoints"][0].copy()
                ),
            ),
            (
                "truncated statuses",
                lambda observation: observation["statuses"].update(
                    complete=False, truncated=True
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                observation = na_observation()
                mutate(observation)
                ports = FakePorts()
                ports.applicability_observation = observation
                ports.decision_body = na_decision_body(observation=observation)
                with self.assertRaises(ValueError):
                    self.invoke_decision(ports, readiness=ready_report_checks_unknown)
                self.assertEqual(ports.merge_calls, [])

    def test_any_nonempty_check_or_status_state_blocks_na(self):
        for field, state_key, states in (
            (
                "check_runs",
                "conclusion",
                ("success", "failure", "pending", "cancelled", "neutral", "skipped"),
            ),
            (
                "statuses",
                "state",
                (
                    "success",
                    "failure",
                    "pending",
                    "error",
                    "cancelled",
                    "neutral",
                    "skipped",
                ),
            ),
        ):
            for state in states:
                with self.subTest(field=field, state=state):
                    observation = na_observation()
                    observation[field]["rows"] = [{state_key: state}]
                    ports = FakePorts()
                    ports.applicability_observation = observation
                    ports.decision_body = na_decision_body(observation=observation)
                    with self.assertRaises(ValueError):
                        self.invoke_decision(
                            ports, readiness=ready_report_checks_unknown
                        )
                    self.assertEqual(ports.merge_calls, [])

    def test_stale_v2_observation_hash_and_network_errors_block_before_merge(self):
        ports = FakePorts()
        ports.decision_body = na_decision_body(
            extra={"applicability_observation_sha256": "0" * 64}
        )
        with self.assertRaisesRegex(ValueError, "does not bind"):
            self.invoke_decision(ports, readiness=ready_report_checks_unknown)
        self.assertEqual(ports.merge_calls, [])
        self.assertEqual(ports.applicability_calls, 1)

        ports = FakePorts()
        ports.decision_body = na_decision_body()
        ports.decision_bodies = [ports.decision_body, ports.decision_body + " "]
        with self.assertRaisesRegex(ValueError, "content hash changed"):
            self.invoke_decision(ports, readiness=ready_report_checks_unknown)
        self.assertEqual(ports.merge_calls, [])

        class UnavailablePorts(FakePorts):
            def check_applicability(
                self, *, target_branch, target_sha, head_sha, head_ref
            ):
                self.applicability_calls += 1
                raise OSError("network unavailable")

        ports = UnavailablePorts()
        ports.decision_body = na_decision_body()
        with self.assertRaisesRegex(OSError, "network unavailable"):
            self.invoke_decision(ports, readiness=ready_report_checks_unknown)
        self.assertEqual(ports.merge_calls, [])

    def test_required_check_cli_rejects_unrecognized_sentinels_and_failures(self):
        identity = {
            "head_ref": HEAD_REF,
            "head_sha": HEAD,
            "base_sha": BASE,
            "target_branch": "main",
        }
        sentinel = f"no checks reported on the '{HEAD_REF}' branch\n".encode()
        with (
            patch(
                "codex_devtools.pr_merge.subprocess.run",
                side_effect=subprocess.TimeoutExpired("gh", 20),
            ),
            self.assertRaisesRegex(ValueError, "unavailable"),
        ):
            pr_merge.GHMergePorts._required_check_observation(
                "philosample/devtools", 75, **identity
            )
        failures = (
            (b"", 1, b"no checks reported on the 'wrong/branch' branch\n"),
            (b"", 1, b"permission denied\n"),
            (b"not json", 0, b""),
            (b"[]\n", 1, sentinel),
            (b"", 0, sentinel),
            (b"[]\n", 4, b""),
        )
        for stdout, returncode, stderr in failures:
            with self.subTest(stdout=stdout, returncode=returncode, stderr=stderr):
                completed = subprocess.CompletedProcess([], returncode, stdout, stderr)
                with (
                    patch(
                        "codex_devtools.pr_merge.subprocess.run", return_value=completed
                    ),
                    self.assertRaises(ValueError),
                ):
                    pr_merge.GHMergePorts._required_check_observation(
                        "philosample/devtools", 75, **identity
                    )

        completed = subprocess.CompletedProcess([], 0, b"[{}]", b"")
        with patch("codex_devtools.pr_merge.subprocess.run", return_value=completed):
            nonempty = pr_merge.GHMergePorts._required_check_observation(
                "philosample/devtools", 75, **identity
            )
        self.assertEqual(nonempty["rows"], [{}])

        completed = subprocess.CompletedProcess([], 1, b"[]\n", b"")
        with patch("codex_devtools.pr_merge.subprocess.run", return_value=completed):
            required = pr_merge.GHMergePorts._required_check_observation(
                "philosample/devtools", 75, **identity
            )
        self.assertEqual(required["rows"], [])
        self.assertTrue(required["complete"])
        self.assertEqual(required["representation"], {"kind": "structured_json"})
        self.assertEqual(required["stdout_sha256"], hashlib.sha256(b"[]\n").hexdigest())
        self.assertEqual(required["stderr_sha256"], hashlib.sha256(b"").hexdigest())

    def test_collector_rejects_head_or_base_drift_before_applicability_reads(self):
        pins = {
            "repository": "philosample/devtools",
            "number": 86,
            "expected_head": HEAD,
            "expected_base": BASE,
            "expected_head_tree": HEAD_TREE,
            "body_sha256": BODY_HASH,
            "expected_policy_sha256": "a" * 64,
            "expected_evidence_sha256": "b" * 64,
            "expected_plan_sha256": "c" * 64,
        }

        class DriftPorts:
            def __init__(self, *, head=HEAD, base=BASE):
                self.head = head
                self.base = base

            def snapshot(self):
                return {
                    "number": 86,
                    "state": "OPEN",
                    "head_sha": self.head,
                    "head_ref": HEAD_REF,
                    "base_sha": self.base,
                    "base_ref": "main",
                    "head_tree_sha": HEAD_TREE,
                    "body_sha256": BODY_HASH,
                }

            def check_applicability(self, **_kwargs):
                raise AssertionError("drift must stop before applicability reads")

        for field, change in (("head", "5" * 40), ("base", "6" * 40)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "pins"):
                pr_merge.check_applicability_report(
                    **pins, ports=DriftPorts(**{field: change})
                )

        class MidCollectionDriftPorts(DriftPorts):
            def __init__(self, field):
                super().__init__()
                self.field = field
                self.snapshots = 0

            def snapshot(self):
                self.snapshots += 1
                row = super().snapshot()
                if self.snapshots == 2:
                    row = row.copy()
                    row["head_sha" if self.field == "head" else "base_sha"] = "7" * 40
                return row

            def check_applicability(self, **_kwargs):
                return na_observation()

        for field in ("head", "base"):
            with (
                self.subTest(mid_collection_field=field),
                self.assertRaisesRegex(ValueError, "changed during applicability"),
            ):
                pr_merge.check_applicability_report(
                    **pins, ports=MidCollectionDriftPorts(field)
                )

    def test_v2_final_observation_drift_and_comment_change_block_mutation(self):
        observation = na_observation()
        changed = na_observation()
        changed["branch"]["body_sha256"] = "a" * 64
        ports = FakePorts()
        ports.decision_body = na_decision_body(observation=observation)
        ports.applicability_observations = [observation, changed]
        with self.assertRaisesRegex(ValueError, "does not bind"):
            self.invoke_decision(ports, readiness=ready_report_checks_unknown)
        self.assertEqual(ports.merge_calls, [])
        self.assertEqual(ports.applicability_calls, 2)

    def test_v2_target_branch_pin_drift_at_mutation_boundary_blocks(self):
        ports = FakePorts()
        first = ports.snapshot_row.copy()
        final = ports.snapshot_row.copy()
        final["base_ref"] = "release/changed"
        ports.snapshot_rows = [first, final]
        ports.decision_body = na_decision_body()
        with self.assertRaisesRegex(ValueError, "target branch changed"):
            self.invoke_decision(ports, readiness=ready_report_checks_unknown)
        self.assertEqual(ports.merge_calls, [])
        self.assertEqual(ports.snapshot_calls, 2)

        ports = FakePorts()
        first = ports.snapshot_row.copy()
        final = ports.snapshot_row.copy()
        final["head_ref"] = "codex/renamed-source"
        ports.snapshot_rows = [first, final]
        ports.decision_body = na_decision_body()
        with self.assertRaisesRegex(ValueError, "source or target branch changed"):
            self.invoke_decision(ports, readiness=ready_report_checks_unknown)
        self.assertEqual(ports.merge_calls, [])
        self.assertEqual(ports.snapshot_calls, 2)
        self.assertEqual(ports.applicability_calls, 1)

    def test_v2_dry_run_and_cli_integration_keep_mutation_explicit(self):
        ports = FakePorts()
        ports.decision_body = na_decision_body()
        result = self.invoke_decision(
            ports, readiness=ready_report_checks_unknown, dry_run=True
        )
        self.assertEqual(result["status"], "dry_run_ready")
        self.assertEqual(result["required_checks_disposition"], "not_applicable")
        self.assertEqual(ports.merge_calls, [])

    def test_strict_readiness_and_merge_accept_only_exact_cli_sentinel(self):
        sentinel = f"no checks reported on the '{HEAD_REF}' branch\n".encode()
        pr_view = {
            "number": 75,
            "headRefName": HEAD_REF,
            "headRefOid": HEAD,
            "baseRefName": "main",
            "baseRefOid": BASE,
            "body": "reviewed body",
            "state": "OPEN",
            "reviewDecision": "REVIEW_REQUIRED",
        }
        graphql = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "id": "PR_75",
                        "headRefOid": HEAD,
                        "baseRefOid": BASE,
                        "state": "OPEN",
                        "reviewThreads": {
                            "nodes": [],
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": None,
                            },
                        },
                    }
                }
            }
        }

        def api_payload(endpoint):
            if endpoint == "repos/philosample/devtools":
                return 200, {"private": True, "owner": {"type": "User"}}
            if endpoint == "repos/philosample/devtools/branches/main":
                return 200, {"commit": {"sha": BASE}, "protected": False}
            if endpoint.endswith("/actions/workflows?per_page=100"):
                return 200, {"total_count": 0, "workflows": []}
            if endpoint.endswith("/check-runs?per_page=100"):
                return 200, {"total_count": 0, "check_runs": []}
            if endpoint.endswith("/statuses?per_page=100"):
                return 200, []
            if endpoint.endswith("/status"):
                return 200, {"state": "pending", "statuses": []}
            return 403, {"message": check_applicability.UPGRADE_MESSAGE}

        def run_case(*, generic_after_candidate):
            check_calls = 0

            def fake_run(command, **_kwargs):
                nonlocal check_calls
                if command[:3] == ["gh", "pr", "checks"]:
                    check_calls += 1
                    generic = generic_after_candidate and check_calls == 3
                    return subprocess.CompletedProcess(
                        command,
                        1,
                        b"",
                        sentinel if not generic else b"GraphQL: transient failure\n",
                    )
                if command[:3] == ["gh", "pr", "view"]:
                    return subprocess.CompletedProcess(
                        command, 0, json.dumps(pr_view), ""
                    )
                if command[:3] == ["gh", "api", "graphql"]:
                    return subprocess.CompletedProcess(
                        command, 0, json.dumps(graphql), ""
                    )
                endpoint = command[-1]
                status, payload = api_payload(endpoint)
                return completed_http(command, status, payload)

            collector = pr_merge.GHMergePorts("philosample/devtools", 75)
            with patch("codex_devtools.pr_merge.subprocess.run", side_effect=fake_run):
                observation = collector.check_applicability(
                    target_branch="main",
                    target_sha=BASE,
                    head_sha=HEAD,
                    head_ref=HEAD_REF,
                )
            ports = FakePorts()
            ports.applicability_observation = observation

            def collect_again(*, target_branch, target_sha, head_sha, head_ref):
                return collector.check_applicability(
                    target_branch=target_branch,
                    target_sha=target_sha,
                    head_sha=head_sha,
                    head_ref=head_ref,
                )

            ports.check_applicability = collect_again
            ports.decision_body = na_decision_body(observation=observation)
            readiness_reports = []

            def strict_readiness():
                with (
                    patch(
                        "codex_devtools.pr_readiness._inputs",
                        return_value=(
                            {
                                "policy": {
                                    "repository": "philosample/devtools",
                                    "pr_number": 75,
                                    "pull_request": {
                                        "head_sha": HEAD,
                                        "base_sha": BASE,
                                        "body_sha256": BODY_HASH,
                                    },
                                },
                                "blockers": [],
                                "receipt_verification": {
                                    "status": "verified",
                                    "complete": True,
                                    "count": 1,
                                    "receipts": [],
                                },
                            },
                            {
                                "policy_sha256": "a" * 64,
                                "evidence_sha256": "b" * 64,
                                "plan_sha256": "c" * 64,
                            },
                        ),
                    ),
                    patch(
                        "codex_devtools.pr_readiness.subprocess.run",
                        side_effect=fake_run,
                    ),
                ):
                    report, _ = pr_readiness.review_ready(
                        "philosample/devtools",
                        75,
                        Path("policy"),
                        Path("evidence"),
                        Path("plan"),
                    )
                readiness_reports.append(report)
                return report

            if generic_after_candidate:
                with (
                    patch(
                        "codex_devtools.pr_merge.subprocess.run", side_effect=fake_run
                    ),
                    self.assertRaisesRegex(
                        ValueError, "transport differs from exact applicability"
                    ),
                ):
                    self.invoke_decision(
                        ports, readiness=strict_readiness, dry_run=True
                    )
                self.assertFalse(readiness_reports[0]["ready"])
                self.assertEqual(
                    readiness_reports[0]["github_required_checks"]["status"],
                    "unknown",
                )
            else:
                with patch(
                    "codex_devtools.pr_merge.subprocess.run", side_effect=fake_run
                ):
                    result = self.invoke_decision(
                        ports, readiness=strict_readiness, dry_run=True
                    )
                self.assertEqual(result["status"], "dry_run_ready")
                self.assertFalse(readiness_reports[0]["ready"])
                self.assertEqual(
                    readiness_reports[0]["github_required_checks"]["transport"][
                        "stderr_sha256"
                    ],
                    hashlib.sha256(sentinel).hexdigest(),
                )
            self.assertEqual(ports.merge_calls, [])

        run_case(generic_after_candidate=False)
        run_case(generic_after_candidate=True)

        ports = FakePorts()
        ports.decision_body = na_decision_body()
        output = io.StringIO()
        with (
            patch.object(pr_merge, "GHMergePorts", return_value=ports),
            contextlib.redirect_stdout(output),
            patch(
                "codex_devtools.pr_readiness.review_ready",
                return_value=(ready_report_checks_unknown(), 1),
            ),
        ):
            code = main(
                [
                    "pr",
                    "merge",
                    "--repo",
                    "philosample/devtools",
                    "--number",
                    "75",
                    "--policy",
                    "policy.json",
                    "--evidence",
                    "evidence.json",
                    "--plan",
                    "plan.md",
                    "--expected-head",
                    HEAD,
                    "--expected-base",
                    BASE,
                    "--expected-head-tree",
                    HEAD_TREE,
                    "--expected-tree",
                    TREE,
                    "--body-sha256",
                    BODY_HASH,
                    "--expected-policy-sha256",
                    "a" * 64,
                    "--expected-evidence-sha256",
                    "b" * 64,
                    "--expected-plan-sha256",
                    "c" * 64,
                    "--decision-reference",
                    DECISION_REF,
                    "--decision-sha256",
                    hashlib.sha256(ports.decision_body.encode()).hexdigest(),
                    "--operator-assertion",
                    ASSERTION,
                ]
            )
        self.assertEqual(code, 0)
        output_row = json.loads(output.getvalue())
        self.assertEqual(output_row["required_checks_disposition"], "not_applicable")
        self.assertEqual(ports.merge_calls, [HEAD])

    def test_read_only_applicability_cli_emits_candidate_hash_only(self):
        ports = FakePorts()
        output = io.StringIO()
        with (
            patch.object(pr_merge, "GHMergePorts", return_value=ports),
            contextlib.redirect_stdout(output),
        ):
            code = main(
                [
                    "pr",
                    "check-applicability",
                    "--repo",
                    "philosample/devtools",
                    "--number",
                    "75",
                    "--expected-head",
                    HEAD,
                    "--expected-base",
                    BASE,
                    "--expected-head-tree",
                    HEAD_TREE,
                    "--body-sha256",
                    BODY_HASH,
                    "--expected-policy-sha256",
                    "a" * 64,
                    "--expected-evidence-sha256",
                    "b" * 64,
                    "--expected-plan-sha256",
                    "c" * 64,
                ]
            )
        self.assertEqual(code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "candidate_evidence")
        self.assertTrue(report["coordinator_receipt_required"])
        self.assertFalse(report["merge_authorized"])
        self.assertFalse(report["ci_passed"])
        self.assertEqual(
            report["observation_sha256"],
            check_applicability.canonical_hash(na_observation()),
        )
        self.assertEqual(ports.merge_calls, [])

    def test_changed_decision_comment_hash_blocks_before_merge(self):
        ports = FakePorts()
        ports.decision_bodies = [DECISION_BODY, DECISION_BODY + " "]
        with self.assertRaisesRegex(ValueError, "content hash changed"):
            self.invoke_decision(ports)
        self.assertEqual(ports.decision_calls, 2)
        self.assertEqual(ports.merge_calls, [])

    def test_mismatched_head_in_decision_receipt_blocks_before_merge(self):
        ports = FakePorts()
        ports.decision_body = decision_body(head="d" * 40)
        with self.assertRaisesRegex(ValueError, "does not bind"):
            self.invoke_decision(
                ports,
                decision_sha256=hashlib.sha256(
                    ports.decision_body.encode()
                ).hexdigest(),
            )
        self.assertEqual(ports.merge_calls, [])

    def test_forged_auth_claim_in_decision_receipt_is_rejected(self):
        for extra in ({"authenticated": True}, {"operator_asserted": 1}):
            with self.subTest(extra=extra):
                ports = FakePorts()
                ports.decision_body = decision_body(extra=extra)
                with self.assertRaises(ValueError):
                    self.invoke_decision(
                        ports,
                        decision_sha256=hashlib.sha256(
                            ports.decision_body.encode()
                        ).hexdigest(),
                    )
                self.assertEqual(ports.merge_calls, [])

    def test_cli_integration_uses_injected_ports_and_explicit_operator_action(self):
        ports = FakePorts()
        output = io.StringIO()
        with (
            patch.object(pr_merge, "GHMergePorts", return_value=ports),
            contextlib.redirect_stdout(output),
            patch(
                "codex_devtools.pr_readiness.review_ready",
                return_value=(ready_report(), 1),
            ),
        ):
            code = main(
                [
                    "pr",
                    "merge",
                    "--repo",
                    "philosample/devtools",
                    "--number",
                    "75",
                    "--policy",
                    "policy.json",
                    "--evidence",
                    "evidence.json",
                    "--plan",
                    "plan.md",
                    "--expected-head",
                    HEAD,
                    "--expected-base",
                    BASE,
                    "--expected-tree",
                    TREE,
                    "--expected-head-tree",
                    HEAD_TREE,
                    "--body-sha256",
                    BODY_HASH,
                    "--expected-policy-sha256",
                    "a" * 64,
                    "--expected-evidence-sha256",
                    "b" * 64,
                    "--expected-plan-sha256",
                    "c" * 64,
                    "--review-reference",
                    REVIEW,
                    "--operator-assertion",
                    ASSERTION,
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "merged_verified")
        self.assertEqual(ports.merge_calls, [HEAD])

    def test_cli_integration_accepts_pinned_decision_comment(self):
        ports = FakePorts()
        output = io.StringIO()
        with (
            patch.object(pr_merge, "GHMergePorts", return_value=ports),
            contextlib.redirect_stdout(output),
            patch(
                "codex_devtools.pr_readiness.review_ready",
                return_value=(ready_report(), 1),
            ),
        ):
            code = main(
                [
                    "pr",
                    "merge",
                    "--repo",
                    "philosample/devtools",
                    "--number",
                    "75",
                    "--policy",
                    "policy.json",
                    "--evidence",
                    "evidence.json",
                    "--plan",
                    "plan.md",
                    "--expected-head",
                    HEAD,
                    "--expected-base",
                    BASE,
                    "--expected-head-tree",
                    HEAD_TREE,
                    "--expected-tree",
                    TREE,
                    "--body-sha256",
                    BODY_HASH,
                    "--expected-policy-sha256",
                    "a" * 64,
                    "--expected-evidence-sha256",
                    "b" * 64,
                    "--expected-plan-sha256",
                    "c" * 64,
                    "--decision-reference",
                    DECISION_REF,
                    "--decision-sha256",
                    DECISION_HASH,
                    "--operator-assertion",
                    ASSERTION,
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "merged_verified")
        self.assertEqual(ports.review_calls, 0)
        self.assertEqual(ports.decision_calls, 2)
        self.assertEqual(ports.merge_calls, [HEAD])


if __name__ == "__main__":
    unittest.main()
