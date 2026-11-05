"""Actual Node/Python operator retirement tests; no host or musical consumer."""

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_devtools import harness_control as client
from codex_devtools import harness_reconcile as reconcile
from codex_devtools.runtime import discover_node

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "codex_devtools/harness_mailbox.cjs"
PINS = {"artifact_sha256": "a" * 64, "components": {"driver": "b" * 64}}


class ReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = discover_node()
        if not runtime["available"]:
            raise RuntimeError("actual Node required")
        cls.node = runtime["path"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.root = self.directory / "mailbox"
        self.pins = self.directory / "expected.json"
        self.pins.write_text(json.dumps(PINS))
        self.pins.chmod(0o600)
        self.output = self.directory / "evidence"
        self.count = self.directory / "count"

    def launch(self, *, timeout_ms=100, lease_ms=1000):
        script = """
const {createHarnessMailbox}=require(process.argv[1]);
const fs=require('fs');
const mailbox=createHarnessMailbox({root:process.argv[2],pins:JSON.parse(process.argv[3]),
 timeoutMs:Number(process.argv[5]),leaseMs:Number(process.argv[6]),commands:{arm:{}},
 handle(){fs.appendFileSync(process.argv[4],'callback\\n');}});
console.log(mailbox.descriptorPath);
"""
        process = subprocess.Popen(
            [
                self.node,
                "-e",
                script,
                str(HELPER),
                str(self.root),
                json.dumps(PINS),
                str(self.count),
                str(timeout_ms),
                str(lease_ms),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop, process)
        descriptor = Path(process.stdout.readline().strip())
        self.assertTrue(
            descriptor.is_file(), process.stderr.read() if process.poll() else ""
        )
        return process, descriptor

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()  # Only this test's own stub; never a user target.
        process.communicate(timeout=3)

    def fenced(self):
        process, descriptor = self.launch()
        ack = client.submit(descriptor, PINS, "arm", {}, wait=2)
        self.assertEqual(ack["status"], "unknown_effect")
        until = time.monotonic() + 2
        while (
            client.status(descriptor)["state"] != "fenced" and time.monotonic() < until
        ):
            time.sleep(0.01)
        self.stop(process)  # Emulate an actual operator unload of our inert stub.
        return descriptor

    def command(self, descriptor):
        return [
            sys.executable,
            "-m",
            "codex_devtools",
            "harness",
            "reconcile",
            "--descriptor",
            str(descriptor),
            "--expected-pins",
            str(self.pins),
            "--assert-target-unloaded",
            "--reason",
            "Test stub unloaded",
            "--operator-evidence",
            "test fixture process terminated and waited",
            "--evidence-output",
            str(self.output),
        ]

    def run_cli(self, descriptor):
        return subprocess.run(
            self.command(descriptor),
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )

    def call(self, descriptor):
        return reconcile.reconcile(
            descriptor,
            self.pins,
            self.output,
            assert_target_unloaded=True,
            reason="test stub unloaded",
            operator_evidence="fixture process waited",
        )

    def test_preserves_unknown_effect_and_all_bytes_then_allows_fresh_registration(
        self,
    ):
        descriptor = self.fenced()
        identity, before = reconcile._snapshot(self.root, descriptor, PINS)
        result = self.run_cli(descriptor)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "retired")
        self.assertEqual(report["retirement_basis"], "fenced")
        self.assertEqual(report["marker_callback_count"], 0)
        self.assertEqual(self.count.read_text(), "callback\n")
        self.assertFalse((self.root / "owner").exists())
        self.assertFalse((self.root / "reconciliation-actor").exists())
        self.assertEqual(client.status(descriptor)["state"], "fenced")
        for name, data in before.items():
            self.assertEqual((self.output / "before" / name).read_bytes(), data)
            if not name.startswith("owner/"):
                self.assertEqual((self.root / name).read_bytes(), data)
        self.assertTrue((descriptor.parent / "command.json").exists())
        self.assertTrue((descriptor.parent / "actor").is_dir())
        old_acks = list(descriptor.parent.glob("ack.*.json"))
        self.assertEqual(len(old_acks), 1)
        self.assertEqual(
            json.loads(old_acks[0].read_text())["status"], "unknown_effect"
        )
        marker = report["marker"]
        self.assertNotEqual(marker["instance"], identity["instance"])
        self.assertEqual(marker["pins"], PINS)
        self.assertEqual(marker["state"], "closed")
        self.assertEqual(marker["sequence"], 0)
        runner = json.loads((self.output / "runner.json").read_text())
        self.assertEqual(runner["callback_count"], 0)
        self.assertEqual(json.loads(Path(runner["descriptor"]).read_text()), marker)
        process, fresh = self.launch()
        self.assertNotEqual(fresh, descriptor)
        self.assertEqual(client.status(fresh)["state"], "ready")
        self.stop(process)
        self.assertEqual(
            hashlib.sha256(HELPER.read_bytes()).hexdigest(), reconcile.HELPER_SHA256
        )

    def test_cli_requires_assertion_reason_and_witness(self):
        descriptor = self.fenced()
        for option in ("--assert-target-unloaded", "--reason", "--operator-evidence"):
            args = self.command(descriptor)
            index = args.index(option)
            del args[index : index + (1 if option == "--assert-target-unloaded" else 2)]
            result = subprocess.run(
                args, cwd=REPO, capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 2)
        for reason, witness in (("", "witness"), ("reason", " ")):
            with self.assertRaises(ValueError):
                reconcile.reconcile(
                    descriptor,
                    self.pins,
                    self.output,
                    assert_target_unloaded=True,
                    reason=reason,
                    operator_evidence=witness,
                )
        self.assertFalse(self.output.exists())
        self.assertTrue((self.root / "owner").exists())

    def test_expired_ready_without_pending_can_retire_and_allow_fresh_registration(
        self,
    ):
        process, ready = self.launch()
        self.stop(process)
        value = json.loads(ready.read_text())
        value["expires_at_ms"] = 0
        ready.write_text(json.dumps(value))
        self.assertFalse(client.status(ready)["lease_current"])
        result = self.run_cli(ready)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["retirement_basis"], "expired_ready_no_pending")
        self.assertEqual(report["marker_callback_count"], 0)
        self.assertFalse(self.count.exists())
        self.assertFalse((self.root / "owner").exists())
        self.assertEqual(client.status(ready)["state"], "ready")
        runner = json.loads((self.output / "runner.json").read_text())
        self.assertEqual(runner["retirement_basis"], "expired_ready_no_pending")
        self.assertEqual(runner["callback_count"], 0)
        fresh_process, fresh = self.launch()
        self.assertNotEqual(fresh, ready)
        self.assertEqual(client.status(fresh)["state"], "ready")
        self.stop(fresh_process)

    def test_refuses_live_ready_owner(self):
        process, live_ready = self.launch()
        self.assertTrue(client.status(live_ready)["lease_current"])
        self.assertNotEqual(self.run_cli(live_ready).returncode, 0)
        self.assertTrue((self.root / "owner").exists())
        self.assertFalse(self.output.exists())
        self.stop(process)

    def test_refuses_expired_pending_owner(self):
        process, pending = self.launch(timeout_ms=60000)
        result = client.submit(pending, PINS, "arm", {}, wait=0.05)
        self.assertEqual(result["status"], "enqueued")
        self.assertEqual(client.status(pending)["state"], "pending")
        self.stop(process)
        descriptor = json.loads(pending.read_text())
        descriptor["expires_at_ms"] = 0
        pending.write_text(json.dumps(descriptor))
        self.assertNotEqual(self.run_cli(pending).returncode, 0)
        self.assertEqual(client.status(pending)["state"], "pending")
        self.assertTrue((self.root / "owner").exists())
        self.assertFalse(self.output.exists())

    def test_refuses_expired_ready_with_unresolved_command_file(self):
        process, orphaned = self.launch()
        self.stop(process)
        descriptor = json.loads(orphaned.read_text())
        descriptor["expires_at_ms"] = 0
        orphaned.write_text(json.dumps(descriptor))
        command = orphaned.parent / "command.json"
        command.write_text("{}")
        command.chmod(0o600)
        self.assertNotEqual(self.run_cli(orphaned).returncode, 0)
        self.assertTrue((self.root / "owner").exists())
        self.assertFalse(self.output.exists())

    def test_wrong_pins_owner_or_nonce_still_fail_closed_for_fenced_owner(self):
        ready = self.fenced()
        self.pins.write_text(json.dumps({**PINS, "artifact_sha256": "c" * 64}))
        self.assertNotEqual(self.run_cli(ready).returncode, 0)
        self.pins.write_text(json.dumps(PINS))
        owner = self.root / "owner/identity.json"
        original = owner.read_text()
        for key, wrong in (
            ("nonce", "c" * 64),
            ("instance", "00000000-0000-4000-8000-000000000000"),
            ("pins", {**PINS, "artifact_sha256": "c" * 64}),
        ):
            changed = json.loads(original)
            changed[key] = wrong
            owner.write_text(json.dumps(changed))
            self.assertNotEqual(self.run_cli(ready).returncode, 0)
        owner.write_text(original)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.root / "reconciliation-actor").exists())

    def test_refuses_symlink_nonprivate_and_git_paths_without_mutation(self):
        descriptor = self.fenced()
        original = descriptor.read_bytes()
        for target in (self.pins, descriptor, self.root / "owner/identity.json"):
            with self.subTest(target=target):
                target.chmod(0o644)
                self.assertNotEqual(self.run_cli(descriptor).returncode, 0)
                target.chmod(0o600)
        for target in (descriptor.parent, self.root, self.output.parent):
            target.chmod(0o755)
            self.assertNotEqual(self.run_cli(descriptor).returncode, 0)
            target.chmod(0o700)
        original_pins = self.pins.read_bytes()
        self.pins.unlink()
        actual = self.directory / "pins-real.json"
        actual.write_bytes(original_pins)
        actual.chmod(0o600)
        self.pins.symlink_to(actual)
        self.assertNotEqual(self.run_cli(descriptor).returncode, 0)
        self.pins.unlink()
        self.pins.write_bytes(original_pins)
        self.pins.chmod(0o600)
        (self.directory / ".git").mkdir()
        self.assertNotEqual(self.run_cli(descriptor).returncode, 0)
        (self.directory / ".git").rmdir()
        (descriptor.parent / "unexpected").symlink_to(actual)
        self.assertNotEqual(self.run_cli(descriptor).returncode, 0)
        (descriptor.parent / "unexpected").unlink()
        self.assertEqual(descriptor.read_bytes(), original)
        self.assertFalse(self.output.exists())

    def test_existing_evidence_and_existing_operator_actor_are_never_cleared(self):
        descriptor = self.fenced()
        self.output.mkdir(mode=0o700)
        (self.output / "sentinel").write_text("preserve")
        self.assertNotEqual(self.run_cli(descriptor).returncode, 0)
        self.assertEqual((self.output / "sentinel").read_text(), "preserve")
        self.output = self.directory / "new-evidence"
        actor = self.root / "reconciliation-actor"
        actor.mkdir(mode=0o700)
        (actor / "identity.json").write_text("exclusive existing operator")
        self.assertNotEqual(self.run_cli(descriptor).returncode, 0)
        self.assertEqual(
            (actor / "identity.json").read_text(), "exclusive existing operator"
        )
        self.assertFalse(self.output.exists())

    def test_two_python_operators_admit_at_most_one(self):
        descriptor = self.fenced()
        args = self.command(descriptor)
        other = args.copy()
        other[-1] = str(self.directory / "evidence-other")
        processes = [
            subprocess.Popen(
                command,
                cwd=REPO,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for command in (args, other)
        ]
        results = [(p.communicate(timeout=10), p.returncode) for p in processes]
        self.assertEqual(sorted(code for _, code in results), [0, 2])
        self.assertEqual(self.count.read_text(), "callback\n")
        self.assertEqual(len(list(self.root.glob("*/descriptor.json"))), 2)

    def test_changed_snapshot_and_runner_error_retain_actor_and_evidence(self):
        descriptor = self.fenced()
        original = reconcile._snapshot
        calls = 0

        def changed(*args):
            nonlocal calls
            calls += 1
            identity, files = original(*args)
            if calls == 2:
                files["owner/identity.json"] += b" "
            return identity, files

        with (
            patch.object(reconcile, "_snapshot", side_effect=changed),
            self.assertRaisesRegex(ValueError, "actor retained"),
        ):
            self.call(descriptor)
        self.assertEqual(
            json.loads((self.output / "outcome.json").read_text())["status"], "unknown"
        )
        self.assertTrue((self.output / "before/owner/identity.json").is_file())
        self.assertTrue((self.root / "owner").exists())
        self.assertTrue((self.root / "reconciliation-actor").exists())

    def test_actual_runner_rechecks_owner_before_mutation(self):
        descriptor = self.fenced()
        actual_run = subprocess.run

        def changed_owner(command, **kwargs):
            owner = self.root / "owner/identity.json"
            value = json.loads(owner.read_text())
            value["nonce"] = "d" * 64
            owner.write_text(json.dumps(value))
            return actual_run(command, **kwargs)

        with (
            patch.object(
                reconcile,
                "discover_node",
                return_value={"available": True, "path": self.node},
            ),
            patch.object(reconcile.subprocess, "run", side_effect=changed_owner),
            self.assertRaisesRegex(ValueError, "actor retained"),
        ):
            self.call(descriptor)
        self.assertTrue((self.root / "owner").exists())
        self.assertFalse((self.output / "marker.json").exists())
        self.assertEqual(self.count.read_text(), "callback\n")
        self.assertEqual(client.status(descriptor)["state"], "fenced")

    def test_actual_runner_rejects_empty_actor_directory_arriving_after_snapshot(self):
        process, descriptor = self.launch()
        self.stop(process)
        value = json.loads(descriptor.read_text())
        value["expires_at_ms"] = 0
        descriptor.write_text(json.dumps(value))
        descriptor_before = descriptor.read_bytes()
        owner_before = (self.root / "owner/identity.json").read_bytes()
        actual_run = subprocess.run

        def add_empty_actor_before_runner(command, **kwargs):
            (descriptor.parent / "actor").mkdir(mode=0o700)
            return actual_run(command, **kwargs)

        with (
            patch.object(
                reconcile,
                "discover_node",
                return_value={"available": True, "path": self.node},
            ),
            patch.object(
                reconcile.subprocess,
                "run",
                side_effect=add_empty_actor_before_runner,
            ),
            self.assertRaisesRegex(
                ValueError,
                "reconciliation runner refused or failed; inspect evidence; actor retained",
            ),
        ):
            self.call(descriptor)
        self.assertTrue((descriptor.parent / "actor").is_dir())
        self.assertTrue((self.root / "owner").is_dir())
        self.assertEqual(descriptor.read_bytes(), descriptor_before)
        self.assertEqual((self.root / "owner/identity.json").read_bytes(), owner_before)
        self.assertFalse((self.output / "marker.json").exists())
        self.assertFalse(self.count.exists())
        self.assertIn(
            "expired READY owner has unresolved command/actor",
            (self.output / "node.stderr").read_text(),
        )
        outcome = json.loads((self.output / "outcome.json").read_text())
        self.assertEqual(outcome["status"], "unknown")
        self.assertTrue((self.root / "reconciliation-actor").is_dir())

    def test_uncertain_runner_owner_gap_restores_exclusive_fence(self):
        descriptor = self.fenced()

        def owner_gap(*_args, **_kwargs):
            (self.root / "owner/identity.json").unlink()
            (self.root / "owner").rmdir()
            return subprocess.CompletedProcess(
                [], 1, "", "simulated runner failure after owner removal"
            )

        with (
            patch.object(
                reconcile,
                "discover_node",
                return_value={"available": True, "path": self.node},
            ),
            patch.object(reconcile.subprocess, "run", side_effect=owner_gap),
            self.assertRaisesRegex(ValueError, "actor retained"),
        ):
            self.call(descriptor)
        owner = json.loads((self.root / "owner/identity.json").read_text())
        self.assertEqual(owner["nonce"], client.status(descriptor)["nonce"])
        outcome = json.loads((self.output / "outcome.json").read_text())
        self.assertEqual(outcome["status"], "unknown")
        self.assertIn("restored", outcome["owner_fence"])
        self.assertTrue(outcome["after_sha256"])
        self.assertTrue((self.root / "reconciliation-actor").exists())

    def test_duplicate_json_and_wrong_user_are_refused(self):
        descriptor = self.fenced()
        for target in (descriptor, self.pins, self.root / "owner/identity.json"):
            original = target.read_bytes()
            target.write_bytes(b'{"duplicate":1,"duplicate":2,' + original[1:])
            self.assertNotEqual(self.run_cli(descriptor).returncode, 0)
            target.write_bytes(original)
        with (
            patch.object(reconcile.os, "getuid", return_value=-1),
            self.assertRaises(ValueError),
        ):
            self.call(descriptor)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.root / "reconciliation-actor").exists())

    def test_symlink_mailbox_ancestor_is_refused(self):
        descriptor = self.fenced()
        alias = self.directory / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertNotEqual(
            self.run_cli(alias / descriptor.parent.name / "descriptor.json").returncode,
            0,
        )
        self.assertFalse(self.output.exists())
        self.assertTrue((self.root / "owner").is_dir())


if __name__ == "__main__":
    unittest.main()
