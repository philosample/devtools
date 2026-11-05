"""Closed declaration checks for the first read-only PR readiness increment."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
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
        self.write_json(self.policy_path, self.policy)
        self.write_json(self.evidence_path, self.evidence)

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
        self.write_json(self.policy_path, self.policy)
        self.write_json(self.evidence_path, self.evidence)

    def test_plan_check_accepts_exact_closed_declarations_but_never_overall_ready(self):
        report, code = pr_readiness.plan_check(
            self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 0)
        self.assertTrue(report["valid"])
        self.assertFalse(report["overall_ready"])
        self.assertEqual(report["coordinator_approval"]["status"], "unknown")

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
        # Raw JSON mirrors `gh pr view --json number,headRefOid,baseRefOid,body,state,reviewDecision`.
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {
                "number": 75,
                "headRefOid": HEAD,
                "baseRefOid": BASE,
                "body": BODY,
                "state": "OPEN",
                "reviewDecision": "APPROVED",
            }
        )
        report, code = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        self.assertEqual(code, 1)
        self.assertFalse(report["ready"])
        self.assertEqual(report["github_review_decision_observed"], "APPROVED")
        self.assertEqual(report["coordinator_approval"]["status"], "unknown")
        self.assertIn(
            "coordinator_approval", {item["gate"] for item in report["blockers"]}
        )
        unknown = {item["gate"]: item for item in report["unverified_gates"]}
        self.assertEqual(
            set(unknown),
            {"github_required_checks", "review_findings", "receipt_verification"},
        )
        self.assertTrue(all(item["status"] == "unknown" for item in unknown.values()))
        self.assertTrue(
            set(unknown).issubset({item["gate"] for item in report["blockers"]})
        )
        self.assertEqual(
            run.call_args.args[0],
            [
                "gh",
                "pr",
                "view",
                "75",
                "--repo",
                "philosample/devtools",
                "--json",
                "number,headRefOid,baseRefOid,body,state,reviewDecision",
            ],
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertFalse((self.root / "command-was-executed").exists())

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_changes_requested_adds_disposition_block_and_keeps_unverified_gates(
        self, run
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {
                "number": 75,
                "headRefOid": HEAD,
                "baseRefOid": BASE,
                "body": BODY,
                "state": "OPEN",
                "reviewDecision": "CHANGES_REQUESTED",
            }
        )
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
        self.assertTrue(
            {
                "github_required_checks",
                "review_findings",
                "receipt_verification",
            }.issubset(gates)
        )
        self.assertIn("coordinator_approval", gates)

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_remote_head_base_and_body_changes_are_independent_blockers(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {
                "number": 75,
                "headRefOid": "5" * 40,
                "baseRefOid": "6" * 40,
                "body": "edited PR description",
                "state": "OPEN",
                "reviewDecision": "REVIEW_REQUIRED",
            }
        )
        report, _ = pr_readiness.review_ready(
            "philosample/devtools", 75, self.policy_path, self.evidence_path, self.plan
        )
        gates = {item["gate"] for item in report["blockers"]}
        self.assertTrue({"head_pin", "base_pin", "body_pin"}.issubset(gates))

    @patch("codex_devtools.pr_readiness.subprocess.run")
    def test_cli_uses_read_only_review_command(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {
                "number": 75,
                "headRefOid": HEAD,
                "baseRefOid": BASE,
                "body": BODY,
                "state": "OPEN",
                "reviewDecision": "",
            }
        )
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
        self.assertFalse(json.loads(output.getvalue())["ready"])
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
