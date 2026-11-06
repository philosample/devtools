"""Offline explicit receipt packing preserves PR readiness declarations."""

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

from codex_devtools import pr_evidence_pack, pr_readiness
from codex_devtools.cli import main

BASE = "1" * 40
HEAD = "2" * 40
COMPONENTS = {"codex-devtools": "3" * 64}


class PREvidencePackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.receipts = self.root / "receipts"
        self.receipts.mkdir()
        self.plan = self.root / "plan.md"
        self.plan.write_text("# Evidence pack fixture\n", encoding="utf-8")
        self.policy_path = self.root / "policy.json"
        self.manifest_path = self.root / "manifest.json"
        self.output = self.root / "assembled-evidence.json"
        self.policy = {
            "schema": pr_readiness.POLICY_SCHEMA,
            "repository": "philosample/devtools",
            "pr_number": 75,
            "pull_request": {
                "base_sha": BASE,
                "head_sha": HEAD,
                "body_sha256": "4" * 64,
            },
            "plan": {
                "id": "devtools-75",
                "ref": "plans/evidence-pack.md",
                "sha256": hashlib.sha256(self.plan.read_bytes()).hexdigest(),
            },
            "component_pins": COMPONENTS,
            "test_decision": {
                "coordinator_chat_id": "coordinator",
                "decision_ref": "issue-75-comment-1",
                "rationale": "Focused serializer tests fit the bounded change.",
                "levels": {
                    "development": {
                        "required": True,
                        "rationale": "Exercises the packer and CLI.",
                    },
                    "development_integration": {
                        "required": True,
                        "rationale": "Checks the existing plan-check interface.",
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
                    "id": "integration",
                    "level": "development_integration",
                    "kind": "inspection",
                    "required_for_merge": True,
                },
                {
                    "id": "optional-note",
                    "level": "development_integration",
                    "kind": "inspection",
                    "required_for_merge": False,
                },
            ],
            "holds": [],
        }
        self.policy_path.write_text(json.dumps(self.policy), encoding="utf-8")
        self.receipt_facts = {
            "unit": self.receipt("unit", "development", test_count=3),
            "integration": self.receipt(
                "integration", "development_integration", kind="inspection"
            ),
            "optional-note": self.receipt(
                "optional-note",
                "development_integration",
                kind="inspection",
                result="skipped",
                exit_code=1,
            ),
        }
        self.save_receipt("unit")
        self.save_receipt("integration")
        self.save_receipt("optional-note")
        self.manifest = {
            "schema": pr_evidence_pack.PACK_SCHEMA,
            "receipts": [
                self.manifest_row("unit"),
                self.manifest_row("integration"),
                self.manifest_row("optional-note"),
            ],
        }
        self.save_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def receipt(
        self,
        check_id,
        level,
        *,
        kind="test",
        result="passed",
        exit_code=0,
        test_count=None,
        component_pins=None,
    ):
        return {
            "schema": pr_readiness.RECEIPT_SCHEMA,
            "check_id": check_id,
            "level": level,
            "base_sha": BASE,
            "head_sha": HEAD,
            "component_pins": component_pins or COMPONENTS,
            "command": ["python3", "-m", "offline-fixture"],
            "environment": {"python": "fixture"},
            "result": result,
            "exit_code": exit_code,
            "test_count": (2 if kind == "test" else 0)
            if test_count is None
            else test_count,
        }

    def receipt_path(self, check_id):
        return self.receipts / f"{check_id}.json"

    def save_receipt(self, check_id):
        self.receipt_path(check_id).write_text(
            json.dumps(self.receipt_facts[check_id], separators=(",", ":")),
            encoding="utf-8",
        )

    def manifest_row(self, check_id, **updates):
        row = {
            "check_id": check_id,
            "receipt_ref": f"receipts/{check_id}.json",
            "owner": ""
            if self.receipt_facts[check_id]["result"] == "passed"
            else "operator",
            "next_action": ""
            if self.receipt_facts[check_id]["result"] == "passed"
            else "investigate result",
            "limitations": ["fixture limitation"],
        }
        row.update(updates)
        return row

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def pack(self):
        return pr_evidence_pack.pack(
            self.policy_path, self.manifest_path, self.plan, self.output
        )

    def cli_plan_check(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "pr",
                    "plan-check",
                    "--policy",
                    str(self.policy_path),
                    "--evidence",
                    str(self.output),
                    "--plan",
                    str(self.plan),
                ]
            )
        return json.loads(stdout.getvalue()), code

    def test_cli_packs_deterministically_and_existing_plan_check_accepts(self):
        argv = [
            "pr",
            "evidence-pack",
            "--policy",
            str(self.policy_path),
            "--manifest",
            str(self.manifest_path),
            "--plan",
            str(self.plan),
            "--out",
            str(self.output),
        ]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(argv), 0)
        report = json.loads(stdout.getvalue())
        document = json.loads(self.output.read_bytes())
        optional = next(
            row for row in document["rows"] if row["check_id"] == "optional-note"
        )
        self.assertEqual(optional["result"], "skipped")
        self.assertEqual(optional["test_count"], 0)
        self.assertEqual(optional["limitations"], ["fixture limitation"])
        first_bytes = self.output.read_bytes()
        self.assertEqual(report["sha256"], hashlib.sha256(first_bytes).hexdigest())
        plan_report, code = self.cli_plan_check()
        self.assertTrue(plan_report["valid"])
        self.assertEqual(code, 0)
        second = self.root / "second.json"
        pr_evidence_pack.pack(self.policy_path, self.manifest_path, self.plan, second)
        self.assertEqual(second.read_bytes(), first_bytes)

    def test_failed_required_fact_is_preserved_and_plan_check_still_blocks(self):
        self.receipt_facts["unit"] = self.receipt(
            "unit", "development", result="failed", exit_code=1, test_count=3
        )
        self.save_receipt("unit")
        self.manifest["receipts"][0] = self.manifest_row("unit")
        self.save_manifest()
        self.pack()
        document = json.loads(self.output.read_bytes())
        failed = next(row for row in document["rows"] if row["check_id"] == "unit")
        self.assertEqual(
            (failed["result"], failed["exit_code"], failed["test_count"]),
            ("failed", 1, 3),
        )
        report, code = self.cli_plan_check()
        self.assertEqual(code, 1)
        self.assertTrue(
            any(blocker["gate"] == "required_check" for blocker in report["blockers"])
        )

    def test_missing_required_duplicate_unknown_and_wrong_pins_refuse_without_output(
        self,
    ):
        invalid_manifests = []
        missing = json.loads(json.dumps(self.manifest))
        missing["receipts"] = [
            row for row in missing["receipts"] if row["check_id"] != "unit"
        ]
        invalid_manifests.append(missing)
        duplicate = json.loads(json.dumps(self.manifest))
        duplicate["receipts"].append(duplicate["receipts"][0])
        invalid_manifests.append(duplicate)
        unknown = json.loads(json.dumps(self.manifest))
        unknown["receipts"][0]["check_id"] = "unknown"
        invalid_manifests.append(unknown)
        for manifest in invalid_manifests:
            with self.subTest(manifest=manifest):
                self.manifest = manifest
                self.save_manifest()
                with self.assertRaises(ValueError):
                    self.pack()
                self.assertFalse(self.output.exists())

        self.manifest = json.loads(
            json.dumps(
                {
                    "schema": pr_evidence_pack.PACK_SCHEMA,
                    "receipts": [
                        self.manifest_row("unit"),
                        self.manifest_row("integration"),
                    ],
                }
            )
        )
        self.receipt_facts["unit"] = self.receipt(
            "unit", "development", component_pins={"codex-devtools": "5" * 64}
        )
        self.save_receipt("unit")
        self.save_manifest()
        with self.assertRaises(ValueError):
            self.pack()
        self.assertFalse(self.output.exists())

    def test_missing_file_duplicate_json_and_stale_pins_refuse_without_output(self):
        original = json.loads(json.dumps(self.manifest))
        self.manifest["receipts"][0]["receipt_ref"] = "receipts/absent.json"
        self.save_manifest()
        with self.assertRaises((OSError, ValueError)):
            self.pack()
        self.assertFalse(self.output.exists())

        self.manifest = original
        self.manifest_path.write_text(
            '{"schema":"one","schema":"two","receipts":[]}', encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            self.pack()
        self.assertFalse(self.output.exists())

        self.manifest_path.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.pack()
        self.assertFalse(self.output.exists())

        self.save_manifest()
        self.receipt_facts["unit"] = self.receipt("unit", "development")
        self.receipt_facts["unit"]["head_sha"] = "5" * 40
        self.save_receipt("unit")
        with self.assertRaises(ValueError):
            self.pack()
        self.assertFalse(self.output.exists())

    def test_unsafe_symlink_malformed_and_oversized_receipts_refuse_without_output(
        self,
    ):
        for reference, setup in (
            ("../outside.json", None),
            (
                "receipts/link.json",
                lambda: os.symlink(
                    self.receipt_path("unit"), self.receipts / "link.json"
                ),
            ),
        ):
            with self.subTest(reference=reference):
                row = self.manifest_row("unit", receipt_ref=reference)
                self.manifest["receipts"][0] = row
                self.save_manifest()
                if setup:
                    setup()
                with self.assertRaises((OSError, ValueError)):
                    self.pack()
                self.assertFalse(self.output.exists())
                (self.receipts / "link.json").unlink(missing_ok=True)

        self.manifest["receipts"][0] = self.manifest_row("unit")
        self.receipt_path("unit").write_text(
            '{"schema": "bad", "schema": "duplicate"}', encoding="utf-8"
        )
        self.save_manifest()
        with self.assertRaises(ValueError):
            self.pack()
        self.assertFalse(self.output.exists())
        self.receipt_path("unit").write_bytes(
            b"x" * (pr_readiness.MAX_RECEIPT_BYTES + 1)
        )
        with self.assertRaises(ValueError):
            self.pack()
        self.assertFalse(self.output.exists())

    def test_zero_test_receipt_and_output_overwrite_are_rejected(self):
        self.receipt_facts["unit"] = self.receipt("unit", "development", test_count=0)
        self.save_receipt("unit")
        with self.assertRaises(ValueError):
            self.pack()
        self.assertFalse(self.output.exists())
        self.receipt_facts["unit"] = self.receipt("unit", "development", test_count=3)
        self.save_receipt("unit")
        self.output.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.pack()
        self.assertEqual(self.output.read_text(encoding="utf-8"), "keep")

    def test_output_symlink_is_rejected_without_changing_target(self):
        target = self.root / "target.json"
        target.write_text("keep", encoding="utf-8")
        self.output.symlink_to(target)
        with self.assertRaises(ValueError):
            self.pack()
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_output_traversal_is_rejected(self):
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaises(ValueError):
            pr_evidence_pack.pack(
                self.policy_path,
                self.manifest_path,
                self.plan,
                nested / ".." / "assembled.json",
            )
        self.assertFalse(self.output.exists())

    def test_no_command_or_network_calls_and_output_must_be_beside_receipts(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        with patch.object(
            subprocess, "run", side_effect=AssertionError("unexpected process")
        ):
            with self.assertRaises(ValueError):
                pr_evidence_pack.pack(
                    self.policy_path,
                    self.manifest_path,
                    self.plan,
                    elsewhere / "out.json",
                )
            report = self.pack()
        self.assertEqual(report["authentication"], "not established")
        self.assertEqual(report["readiness"], "not evaluated; run pr plan-check")


if __name__ == "__main__":
    unittest.main()
