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
BODY_HASH = hashlib.sha256(b"reviewed body").hexdigest()
REVIEW = "https://github.com/philosample/devtools/pull/75#pullrequestreview-9"
ASSERTION = (
    "I am the designated coordinator and explicitly authorize this exact-head merge."
)
MERGE_SHA = "4" * 40


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
            "head_tree_sha": TREE,
            "body_sha256": BODY_HASH,
        }
        self.review_row = {"state": "APPROVED", "commit_sha": HEAD}
        self.merge_response = {"merged": True}
        self.terminal_row = {
            "state": "MERGED",
            "head_sha": HEAD,
            "head_tree_sha": TREE,
            "merge_commit_sha": MERGE_SHA,
        }
        self.snapshot_calls = 0
        self.review_calls = 0
        self.merge_calls: list[str] = []
        self.terminal_calls = 0
        self.raise_merge = False

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
        return self.merge_response

    def terminal(self):
        self.terminal_calls += 1
        return self.terminal_row


class PRMergeTests(unittest.TestCase):
    def invoke(self, ports, readiness=None, **updates):
        args = {
            "repository": "philosample/devtools",
            "number": 75,
            "expected_head": HEAD,
            "expected_base": BASE,
            "expected_tree": TREE,
            "body_sha256": BODY_HASH,
            "review_reference": REVIEW,
            "operator_assertion": ASSERTION,
            "readiness": readiness or ready_report,
            "ports": ports,
            "dry_run": False,
        }
        args.update(updates)
        return pr_merge.merge_pr(**args)

    def test_positive_exact_head_single_merge_and_terminal_verification(self):
        ports = FakePorts()
        result = self.invoke(ports)
        self.assertEqual(result["status"], "merged_verified")
        self.assertEqual(ports.merge_calls, [HEAD])
        self.assertEqual(ports.snapshot_calls, 1)
        self.assertEqual(ports.review_calls, 1)
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
        self.assertEqual(ports.snapshot_calls, 1)
        self.assertEqual(ports.review_calls, 1)

    def test_rejected_and_unknown_network_completion_never_retry(self):
        ports = FakePorts()
        ports.merge_response = {"merged": False}
        result = self.invoke(ports)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(ports.merge_calls, [HEAD])
        ports = FakePorts()
        ports.raise_merge = True
        result = self.invoke(ports)
        self.assertEqual(result["status"], "unknown_completion")
        self.assertEqual(result["retry"], False)
        self.assertEqual(ports.merge_calls, [HEAD])

    def test_terminal_head_or_tree_mismatch_does_not_report_verified_merge(self):
        for field, value in (("head_sha", "d" * 40), ("head_tree_sha", "e" * 40)):
            with self.subTest(field=field):
                ports = FakePorts()
                ports.terminal_row[field] = value
                result = self.invoke(ports)
                self.assertEqual(result["status"], "terminal_verification_failed")
                self.assertFalse(result["merged"])

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
                    "--body-sha256",
                    BODY_HASH,
                    "--review-reference",
                    REVIEW,
                    "--operator-assertion",
                    ASSERTION,
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "merged_verified")
        self.assertEqual(ports.merge_calls, [HEAD])


if __name__ == "__main__":
    unittest.main()
