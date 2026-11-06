"""Closed declaration checks for the first read-only PR readiness increment."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_devtools import pr_readiness
from codex_devtools.cli import main

BASE = "1" * 40
HEAD = "2" * 40
COMPONENT = "3" * 64
RECEIPT = "4" * 64
BODY = "PR description"
REAL_OS_OPEN = os.open


class PRReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plan = self.root / "plan.md"
        self.plan.write_text("# Bounded plan\n", encoding="utf-8")
        self.policy = {
            "schema": pr_readiness.POLICY_SCHEMA,
            "repository": "philosample/devtools",
            "pr_number": 75,
            "pull_request": {
                "base_sha": BASE,
                "head_sha": HEAD,
                "body_sha256": hashlib.sha256(BODY.encode()).hexdigest(),
            },
            "plan": {
                "id": "devtools-75",
                "ref": "workstation-docs/plans/2026-11-05-devtools-pr75-readiness-increment.md",
                "sha256": hashlib.sha256(self.plan.read_bytes()).hexdigest(),
            },
            "component_pins": {"codex-devtools": COMPONENT},
            "test_decision": {
                "coordinator_chat_id": "coordinator-chat-id",
                "decision_ref": "issue-75-comment-6670362239",
                "rationale": "Focused schema, CLI and GitHub serialization tests fit this read-only increment.",
                "levels": {
                    "development": {
                        "required": True,
                        "rationale": "Changed validator and CLI.",
                    },
                    "development_integration": {
                        "required": True,
                        "rationale": "Exercise actual CLI and gh serialization.",
                    },
                },
            },
            "checks": [
                {
                    "id": "unit",
                    "level": "development",
                    "kind": "test",
                    "required_for_merge": True,
                },
                {
                    "id": "gh-read",
                    "level": "development_integration",
                    "kind": "inspection",
                    "required_for_merge": True,
                },
            ],
            "holds": [],
        }
        self.evidence = {
            "schema": pr_readiness.EVIDENCE_SCHEMA,
            "rows": [
                self.row("unit", "development"),
                self.row("gh-read", "development_integration", kind="inspection"),
            ],
        }
        self.policy_path = self.root / "policy.json"
        self.evidence_path = self.root / "evidence.json"
        self.save_inputs()

    def tearDown(self):
        self.temp.cleanup()

    def row(self, check_id, level, *, kind="test", **updates):
        row = {
            "check_id": check_id,
            "level": level,
            "base_sha": BASE,
            "head_sha": HEAD,
            "component_pins": {"codex-devtools": COMPONENT},
            # Declarations only: the validator must never execute these values.
            "command": [
                "python3",
                "-m",
                "untrusted-but-never-executed",
                f"$(touch {self.root / 'command-was-executed'})",
            ],
            "environment": {"python": "3.x"},
            "result": "passed",
            "exit_code": 0,
            "test_count": 2 if kind == "test" else 0,
            "receipt_ref": f"evidence/{check_id}.json",
            "receipt_sha256": RECEIPT,
            "limitations": [],
            "owner": "",
            "next_action": "",
        }
        row.update(updates)
        return row

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def save_inputs(self):
        receipt_root = self.root / "receipts"
        receipt_root.mkdir(exist_ok=True)
        receipt_fields = (
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
        )
        for row in self.evidence["rows"]:
            row["receipt_ref"] = f"receipts/{row['check_id']}.json"
            receipt = {
                "schema": pr_readiness.RECEIPT_SCHEMA,
                **{field: row[field] for field in receipt_fields},
            }
            raw = json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode()
            (self.root / row["receipt_ref"]).write_bytes(raw)
            row["receipt_sha256"] = hashlib.sha256(raw).hexdigest()
        self.write_json(self.policy_path, self.policy)
        self.write_json(self.evidence_path, self.evidence)

    @staticmethod
    def completed_json(value, returncode=0, stderr=""):
        stdout = value if isinstance(value, str) else json.dumps(value)
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    @classmethod
    def pr_response(
        cls,
        *,
        head=HEAD,
        head_ref="codex/pr75-remote-check-na",
        base_ref="main",
        base=BASE,
        body=BODY,
        decision="",
        state="OPEN",
        returncode=0,
    ):
        return cls.completed_json(
            {
                "number": 75,
                "headRefName": head_ref,
                "headRefOid": head,
                "baseRefName": base_ref,
                "baseRefOid": base,
                "body": body,
                "state": state,
                "reviewDecision": decision,
            },
            returncode,
        )

    @classmethod
    def checks_response(cls, rows=None, *, returncode=0, stderr=""):
        if rows is None:
            rows = [
                {
                    "name": "unit",
                    "bucket": "pass",
                    "state": "SUCCESS",
                    "workflow": "CI",
                    "link": "https://github.com/philosample/devtools/actions/runs/1",
                    "startedAt": "2026-11-05T12:00:00Z",
                    "completedAt": "2026-11-05T12:01:00Z",
                }
            ]
        return cls.completed_json(rows, returncode, stderr)

    @classmethod
    def thread_response(
        cls,
        nodes=None,
        *,
        has_next=False,
        cursor=None,
        head=HEAD,
        base=BASE,
        state="OPEN",
        pr_id="PR_kwDOtest",
    ):
        return cls.completed_json(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": pr_id,
                            "headRefOid": head,
                            "baseRefOid": base,
                            "state": state,
                            "reviewThreads": {
                                "nodes": nodes or [],
                                "pageInfo": {
                                    "hasNextPage": has_next,
                                    "endCursor": cursor,
                                },
                            },
                        }
                    }
                }
            }
        )

    def script_remote(
        self,
        run,
        *,
        decision="",
        check_rows=None,
        check_returncode=0,
        check_stderr="",
        thread_pages=None,
        before=None,
        after=None,
    ):
        thread_pages = thread_pages or [self.thread_response()]
        run.side_effect = [
            before or self.pr_response(decision=decision),
            self.checks_response(
                check_rows, returncode=check_returncode, stderr=check_stderr
            ),
            *thread_pages,
            after or self.pr_response(decision=decision),
        ]

    def test_plan_check_accepts_exact_closed_declarations_but_never_overall_ready(self):
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 0)
        self.assertTrue(report["valid"])
        self.assertFalse(report["overall_ready"])
        self.assertEqual(report["coordinator_approval"]["status"], "unknown")
        self.assertEqual(report["receipt_verification"]["status"], "verified")
        self.assertTrue(report["receipt_verification"]["complete"])
        self.assertIn("not authentication", report["limitations"][0])

    def test_receipt_missing_malformed_oversized_and_hash_mismatch_fail_closed(self):
        row = self.evidence["rows"][0]
        receipt_path = self.root / row["receipt_ref"]

        receipt_path.unlink()
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertEqual(report["receipt_verification"]["status"], "unknown")

        self.save_inputs()
        row = self.evidence["rows"][0]
        receipt_path = self.root / row["receipt_ref"]
        receipt_path.write_bytes(b"{" + b" " * (pr_readiness.MAX_RECEIPT_BYTES + 1))
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertEqual(report["receipt_verification"]["status"], "failed")

        self.save_inputs()
        row = self.evidence["rows"][0]
        receipt_path = self.root / row["receipt_ref"]
        receipt_path.write_bytes(b"{malformed")
        row["receipt_sha256"] = hashlib.sha256(b"{malformed").hexdigest()
        self.write_json(self.evidence_path, self.evidence)
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertEqual(report["receipt_verification"]["status"], "failed")

        self.save_inputs()
        row = self.evidence["rows"][0]
        receipt_path = self.root / row["receipt_ref"]
        receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertEqual(report["receipt_verification"]["status"], "failed")
        self.assertIn("hash", report["receipt_verification"]["receipts"][0]["reason"])

    def test_receipt_rejects_symlink_path_changed_content_and_zero_test_count(self):
        row = self.evidence["rows"][0]
        receipt_path = self.root / row["receipt_ref"]
        target = self.root / "outside.json"
        target.write_bytes(receipt_path.read_bytes())
        receipt_path.unlink()
        receipt_path.symlink_to(target)
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertEqual(report["receipt_verification"]["status"], "failed")
        self.assertIn(
            "symlink", report["receipt_verification"]["receipts"][0]["reason"]
        )

        receipt_path.unlink()
        self.save_inputs()
        row = self.evidence["rows"][0]
        receipt_link = self.root / "receipt-directory-link"
        receipt_link.symlink_to(self.root / "receipts", target_is_directory=True)
        row["receipt_ref"] = "receipt-directory-link/unit.json"
        self.write_json(self.evidence_path, self.evidence)
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertIn(
            "symlink", report["receipt_verification"]["receipts"][0]["reason"]
        )
        receipt_link.unlink()

        receipt_path.unlink()
        self.save_inputs()
        row = self.evidence["rows"][0]
        receipt_path = self.root / row["receipt_ref"]
        value = json.loads(receipt_path.read_text(encoding="utf-8"))
        value["head_sha"] = "9" * 40
        raw = json.dumps(value).encode()
        receipt_path.write_bytes(raw)
        row["receipt_sha256"] = hashlib.sha256(raw).hexdigest()
        self.write_json(self.evidence_path, self.evidence)
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertIn(
            "differs from its evidence row",
            report["receipt_verification"]["receipts"][0]["reason"],
        )

        self.evidence["rows"][0] = self.row(
            "unit", "development", test_count=0, owner="worker", next_action="rerun"
        )
        self.save_inputs()
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertTrue(
            any("zero tests" in item["reason"] for item in report["blockers"])
        )

    def test_receipt_paths_must_be_explicit_relative_files(self):
        for reference in ("../escape.json", "/tmp/receipt.json", "receipts//unit.json"):
            with self.subTest(reference=reference):
                self.save_inputs()
                self.evidence["rows"][0]["receipt_ref"] = reference
                self.write_json(self.evidence_path, self.evidence)
                report, code = pr_readiness.plan_check(
                    self.policy_path, self.evidence_path, self.plan
                )
                self.assertEqual(code, 1)
                self.assertEqual(report["receipt_verification"]["status"], "failed")

    @patch("codex_devtools.pr_readiness.os.open")
    def test_leaf_replaced_with_fifo_is_opened_nonblocking_then_rejected(
        self, open_mock
    ):
        replaced = False

        def replace_leaf(name, flags, mode=0o777, *, dir_fd=None):
            nonlocal replaced
            if name == "unit.json" and dir_fd is not None and not replaced:
                replaced = True
                os.unlink(name, dir_fd=dir_fd)
                os.mkfifo(name, mode=0o600, dir_fd=dir_fd)
                self.assertTrue(flags & os.O_NONBLOCK)
            if dir_fd is None:
                return REAL_OS_OPEN(name, flags, mode)
            return REAL_OS_OPEN(name, flags, mode, dir_fd=dir_fd)

        open_mock.side_effect = replace_leaf
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertTrue(replaced)
        self.assertEqual(code, 1)
        self.assertEqual(report["receipt_verification"]["status"], "failed")
        self.assertIn(
            "regular file", report["receipt_verification"]["receipts"][0]["reason"]
        )

    @patch("codex_devtools.pr_readiness.os.open")
    def test_intermediate_directory_replaced_with_symlink_is_not_followed(
        self, open_mock
    ):
        directory = self.root / "receipts"
        backup = self.root / "receipts-original"
        replaced = False

        def replace_parent(name, flags, mode=0o777, *, dir_fd=None):
            nonlocal replaced
            if name == "receipts" and dir_fd is not None and not replaced:
                replaced = True
                directory.rename(backup)
                directory.symlink_to(backup, target_is_directory=True)
                self.assertTrue(flags & os.O_DIRECTORY)
                self.assertTrue(flags & os.O_NOFOLLOW)
            if dir_fd is None:
                return REAL_OS_OPEN(name, flags, mode)
            return REAL_OS_OPEN(name, flags, mode, dir_fd=dir_fd)

        open_mock.side_effect = replace_parent
        try:
            report, code = pr_readiness.plan_check(
                self.policy_path, self.evidence_path, self.plan
            )
        finally:
            if directory.is_symlink():
                directory.unlink()
            if backup.exists():
                backup.rename(directory)
        self.assertTrue(replaced)
        self.assertEqual(code, 1)
        self.assertEqual(report["receipt_verification"]["status"], "failed")
        self.assertNotEqual(
            report["receipt_verification"]["receipts"][0]["status"], "verified"
        )

    def test_unknown_fields_duplicate_keys_and_malformed_json_fail_closed(self):
        self.policy["unreviewed"] = True
        self.save_inputs()
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertFalse(report["valid"])
        self.policy.pop("unreviewed")
        self.policy_path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
        self.assertEqual(
            pr_readiness.plan_check(self.policy_path, self.evidence_path, self.plan)[1],
            1,
        )
        self.policy_path.write_text("{broken", encoding="utf-8")
        self.assertEqual(
            pr_readiness.plan_check(self.policy_path, self.evidence_path, self.plan)[1],
            1,
        )

    def test_missing_stale_wrong_pin_zero_test_and_failed_required_checks_block(self):
        self.evidence["rows"].pop(0)
        self.save_inputs()
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertIn("missing_evidence", {item["gate"] for item in report["blockers"]})

        self.evidence["rows"] = [
            self.row("unit", "development", head_sha="9" * 40),
            self.evidence["rows"][0],
        ]
        self.save_inputs()
        self.assertEqual(
            pr_readiness.plan_check(self.policy_path, self.evidence_path, self.plan)[1],
            1,
        )

        self.evidence["rows"] = [
            self.row(
                "unit", "development", component_pins={"codex-devtools": "5" * 64}
            ),
            self.row("gh-read", "development_integration", kind="inspection"),
        ]
        self.save_inputs()
        self.assertEqual(
            pr_readiness.plan_check(self.policy_path, self.evidence_path, self.plan)[1],
            1,
        )

        self.evidence["rows"][0] = self.row(
            "unit",
            "development",
            test_count=0,
            owner="worker",
            next_action="run focused tests",
        )
        self.save_inputs()
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertTrue(
            any("zero tests" in item["reason"] for item in report["blockers"])
        )

        self.evidence["rows"][0] = self.row(
            "unit",
            "development",
            result="failed",
            exit_code=1,
            owner="worker",
            next_action="repair",
        )
        self.save_inputs()
        self.assertEqual(
            pr_readiness.plan_check(self.policy_path, self.evidence_path, self.plan)[1],
            1,
        )

    def test_skipped_unknown_evidence_and_active_hold_require_owner_action_and_block(
        self,
    ):
        for result in ("skipped", "unknown"):
            self.evidence["rows"][0] = self.row(
                "unit",
                "development",
                result=result,
                exit_code=0,
                owner="worker",
                next_action="resolve",
            )
            self.save_inputs()
            self.assertEqual(
                pr_readiness.plan_check(
                    self.policy_path, self.evidence_path, self.plan
                )[1],
                1,
            )
        self.policy["holds"] = [
            {
                "id": "native-gate",
                "state": "active",
                "reason": "Native pilot remains open",
                "owner": "coordinator",
                "next_action": "run bounded pilot",
            }
        ]
        self.evidence["rows"][0] = self.row("unit", "development")
        self.save_inputs()
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertIn("active_hold", {item["gate"] for item in report["blockers"]})

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_review_ready_parses_actual_gh_json_shape_and_forged_approval_stays_blocked(
        self, run
    ):
        # All responses are serialized payloads from the fixed gh/GraphQL queries.
        self.script_remote(run, decision="APPROVED")
        report, code = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertFalse(report["ready"])
        self.assertEqual(report["github_review_decision_observed"], "APPROVED")
        self.assertEqual(report["coordinator_approval"]["status"], "unknown")
        self.assertEqual(report["github_required_checks"]["status"], "passed")
        self.assertEqual(report["review_threads"]["status"], "resolved")
        gates = {item["gate"] for item in report["blockers"]}
        self.assertIn("coordinator_approval", gates)
        self.assertNotIn("receipt_verification", gates)
        self.assertEqual(report["receipt_verification"]["status"], "verified")
        self.assertNotIn("github_required_checks", gates)
        self.assertNotIn("review_findings", gates)
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "gh",
                "pr",
                "view",
                "75",
                "--repo",
                "philosample/devtools",
                "--json",
                "number,headRefName,headRefOid,baseRefName,baseRefOid,body,state,reviewDecision",
            ],
        )
        check_argv = run.call_args_list[1].args[0]
        self.assertEqual(
            check_argv[:7],
            [
                "gh",
                "pr",
                "checks",
                "75",
                "--repo",
                "philosample/devtools",
                "--required",
            ],
        )
        self.assertIn(
            "name,bucket,state,workflow,link,startedAt,completedAt", check_argv
        )
        graph_argv = run.call_args_list[2].args[0]
        self.assertEqual(graph_argv[:3], ["gh", "api", "graphql"])
        self.assertIn("PRReadinessReviewThreads", " ".join(graph_argv))
        self.assertEqual(run.call_count, 4)
        self.assertFalse((self.root / "command-was-executed").exists())

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_changes_requested_adds_disposition_block_and_keeps_unverified_gates(
        self, run
    ):
        self.script_remote(run, decision="CHANGES_REQUESTED")
        local_report, local_code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(local_code, 0)
        self.assertTrue(local_report["valid"])
        report, code = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertEqual(report["github_review_decision_observed"], "CHANGES_REQUESTED")
        self.assertEqual(report["coordinator_approval"]["status"], "unknown")
        gates = {item["gate"] for item in report["blockers"]}
        self.assertIn("review_disposition", gates)
        self.assertNotIn("github_required_checks", gates)
        self.assertNotIn("review_findings", gates)
        self.assertIn("coordinator_approval", gates)

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_remote_head_base_and_body_changes_are_independent_blockers(self, run):
        self.script_remote(
            run,
            before=self.pr_response(),
            after=self.pr_response(
                head="5" * 40, base="6" * 40, body="edited PR description"
            ),
        )
        report, _ = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        gates = {item["gate"] for item in report["blockers"]}
        self.assertTrue(
            {
                "pr_snapshot_stability",
                "github_required_checks",
                "review_findings",
            }.issubset(gates)
        )
        self.assertFalse({"head_pin", "base_pin", "body_pin"}.intersection(gates))
        self.assertEqual(report["github_required_checks"]["status"], "unknown")
        self.assertEqual(report["review_threads"]["status"], "unknown")

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_cli_uses_read_only_review_command(self, run):
        self.script_remote(run)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "pr",
                    "review-ready",
                    "--repo",
                    "philosample/devtools",
                    "--number",
                    "75",
                    "--policy",
                    str(self.policy_path),
                    "--evidence",
                    str(self.evidence_path),
                    "--plan",
                    str(self.plan),
                ]
            )
        self.assertEqual(code, 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report["ready"])
        self.assertEqual(report["github_required_checks"]["status"], "passed")
        self.assertEqual(report["review_threads"]["status"], "resolved")
        self.assertEqual(run.call_count, 4)

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_required_check_pass_fail_pending_skip_neutral_and_unknown(self, run):
        cases = (
            ("pass", "SUCCESS", 0, "passed"),
            ("fail", "FAILURE", 1, "failed"),
            ("pending", "PENDING", 8, "pending"),
            ("skipping", "SKIPPED", 0, "skipped_or_neutral"),
            ("skipping", "NEUTRAL", 0, "skipped_or_neutral"),
            ("future-bucket", "SUCCESS", 0, "unknown"),
        )
        for bucket, state, returncode, expected in cases:
            with self.subTest(bucket=bucket, state=state):
                row = {
                    "name": "required-build",
                    "bucket": bucket,
                    "state": state,
                    "workflow": "CI",
                    "link": "https://github.com/example/run/1",
                    "startedAt": None,
                    "completedAt": None,
                }
                self.script_remote(
                    run,
                    check_rows=[row],
                    check_returncode=returncode,
                    thread_pages=[self.thread_response()],
                )
                report, code = pr_readiness.review_ready(
                    "philosample/devtools",
                    75,
                    self.policy_path,
                    self.evidence_path,
                    self.plan,
                )
                self.assertEqual(code, 1)
                if expected == "unknown":
                    self.assertEqual(
                        report["github_required_checks"]["status"], "unknown"
                    )
                else:
                    self.assertEqual(
                        report["github_required_checks"]["checks"][0]["status"],
                        expected,
                    )
                self.assertFalse(report["ready"])
                if expected == "passed":
                    self.assertNotIn(
                        "github_required_checks",
                        {item["gate"] for item in report["blockers"]},
                    )
                elif expected == "unknown":
                    self.assertIn(
                        "github_required_checks",
                        {item["gate"] for item in report["blockers"]},
                    )
                else:
                    self.assertIn(
                        "required_check", {item["gate"] for item in report["blockers"]}
                    )

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_no_required_checks_and_permission_failures_remain_unknown(self, run):
        sentinel_stderr = (
            b"no checks reported on the 'codex/pr75-remote-check-na' branch\n"
        )
        for rows, returncode, stderr in (
            ([], 1, "no checks reported"),
            ("", 1, sentinel_stderr.decode()),
            ("", 4, "HTTP 403: Resource not accessible"),
        ):
            with self.subTest(returncode=returncode):
                self.script_remote(
                    run,
                    check_rows=rows,
                    check_returncode=returncode,
                    check_stderr=stderr,
                )
                report, _ = pr_readiness.review_ready(
                    "philosample/devtools",
                    75,
                    self.policy_path,
                    self.evidence_path,
                    self.plan,
                )
                self.assertEqual(report["github_required_checks"]["status"], "unknown")
                self.assertIs(report["ready"], False)
                transport = report["github_required_checks"]["transport"]
                self.assertEqual(transport["exit_code"], returncode)
                self.assertEqual(
                    transport["stdout_sha256"],
                    hashlib.sha256(
                        (rows if isinstance(rows, str) else json.dumps(rows)).encode()
                    ).hexdigest(),
                )
                self.assertEqual(
                    transport["stderr_sha256"],
                    hashlib.sha256(stderr.encode()).hexdigest(),
                )
                gate = next(
                    item
                    for item in report["unverified_gates"]
                    if item["gate"] == "github_required_checks"
                )
                self.assertEqual(gate["status"], "unknown")

        bounded_rows = [
            {
                "name": f"required-{index}",
                "bucket": "pass",
                "state": "SUCCESS",
                "workflow": "CI",
                "link": f"https://github.com/example/run/{index}",
            }
            for index in range(pr_readiness.MAX_ITEMS)
        ]
        self.script_remote(run, check_rows=bounded_rows)
        report, _ = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(report["github_required_checks"]["status"], "unknown")
        self.assertIn(
            "github_required_checks", {item["gate"] for item in report["blockers"]}
        )

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_unresolved_and_resolved_thread_serialization(self, run):
        for resolved, expected in ((False, "unresolved"), (True, "resolved")):
            with self.subTest(resolved=resolved):
                self.script_remote(
                    run,
                    thread_pages=[
                        self.thread_response(
                            [{"id": "PRRT_kwDOtest1", "isResolved": resolved}]
                        )
                    ],
                )
                report, _ = pr_readiness.review_ready(
                    "philosample/devtools",
                    75,
                    self.policy_path,
                    self.evidence_path,
                    self.plan,
                )
                self.assertEqual(report["review_threads"]["status"], expected)
                self.assertEqual(report["review_threads"]["total"], 1)
                finding_gates = [
                    item
                    for item in report["blockers"]
                    if item["gate"] == "review_findings"
                ]
                self.assertEqual(bool(finding_gates), not resolved)

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_review_thread_pagination_is_followed_and_truncation_stays_unknown(
        self, run
    ):
        first_nodes = [
            {"id": f"PRRT_kwDO{i:04d}", "isResolved": True} for i in range(100)
        ]
        self.script_remote(
            run,
            thread_pages=[
                self.thread_response(first_nodes, has_next=True, cursor="cursor-1"),
                self.thread_response([{"id": "PRRT_kwDO0100", "isResolved": True}]),
            ],
        )
        report, _ = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(report["review_threads"]["status"], "resolved")
        self.assertTrue(report["review_threads"]["complete"])
        self.assertEqual(report["review_threads"]["pages"], 2)
        self.assertEqual(report["review_threads"]["total"], 101)
        second_graphql_argv = run.call_args_list[3].args[0]
        self.assertIn("after=cursor-1", second_graphql_argv)

        pages = [
            self.thread_response(
                [{"id": f"PRRT_page{index}", "isResolved": True}],
                has_next=True,
                cursor=f"cursor-{index}",
            )
            for index in range(1, pr_readiness.MAX_REVIEW_THREAD_PAGES + 1)
        ]
        self.script_remote(run, thread_pages=pages)
        truncated, _ = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(truncated["review_threads"]["status"], "unknown")
        self.assertFalse(truncated["review_threads"]["complete"])
        self.assertIn(
            "review_findings", {item["gate"] for item in truncated["blockers"]}
        )

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_graphql_permission_or_partial_error_remains_unknown(self, run):
        denied = subprocess.CompletedProcess([], 1, "", "HTTP 403")
        self.script_remote(run, thread_pages=[denied])
        report, _ = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(report["review_threads"]["status"], "unknown")
        self.assertIn("review_findings", {item["gate"] for item in report["blockers"]})

        partial = self.completed_json(
            {
                "data": {"repository": None},
                "errors": [{"message": "permission denied"}],
            }
        )
        self.script_remote(run, thread_pages=[partial])
        report, _ = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(report["review_threads"]["status"], "unknown")

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_each_pr_pin_changing_across_reads_invalidates_remote_results(self, run):
        changes = (
            {"head": "5" * 40},
            {"base": "6" * 40},
            {"body": "updated description"},
        )
        for changed in changes:
            with self.subTest(changed=tuple(changed)):
                self.script_remote(
                    run,
                    before=self.pr_response(),
                    after=self.pr_response(**changed),
                )
                report, _ = pr_readiness.review_ready(
                    "philosample/devtools",
                    75,
                    self.policy_path,
                    self.evidence_path,
                    self.plan,
                )
                gates = {item["gate"] for item in report["blockers"]}
                self.assertIn("pr_snapshot_stability", gates)
                self.assertIn("github_required_checks", gates)
                self.assertIn("review_findings", gates)
                self.assertEqual(report["github_required_checks"]["status"], "unknown")
                self.assertEqual(report["review_threads"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
