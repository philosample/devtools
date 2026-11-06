from __future__ import annotations

import contextlib
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from codex_devtools import pr_merge
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


DECISION_BODY = decision_body()
DECISION_HASH = hashlib.sha256(DECISION_BODY.encode()).hexdigest()


def ready_report():
    snapshot = {"head_sha": HEAD, "base_sha": BASE, "body_sha256": BODY_HASH}
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


class FakePorts:
    def __init__(self):
        self.snapshot_row = {
            "number": 75,
            "state": "OPEN",
            "head_sha": HEAD,
            "base_sha": BASE,
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
        self.snapshot_calls = 0
        self.review_calls = 0
        self.decision_calls = 0
        self.merge_calls: list[str] = []
        self.terminal_calls = 0
        self.raise_merge = False
        self.raise_merge_value_error = False
        self.raise_terminal = False
        self.raise_terminal_identity = False

    def snapshot(self):
        self.snapshot_calls += 1
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
        self.assertEqual(ports.snapshot_calls, 0)

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

    def invoke_decision(self, ports, **updates):
        updates.setdefault("review_reference", None)
        updates.setdefault("decision_reference", DECISION_REF)
        updates.setdefault("decision_sha256", DECISION_HASH)
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
