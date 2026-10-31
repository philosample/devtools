"""Real Node receiver / Python CLI integration, without a musical host."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from codex_devtools import harness_control as client
from codex_devtools.runtime import discover_node

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "codex_devtools/harness_mailbox.cjs"
PINS = {"artifact_sha256": "a" * 64, "components": {"driver": "b" * 64}}


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = discover_node()
        if not runtime["available"]:
            raise RuntimeError("actual Node required; zero coverage is not a pass")
        cls.node = runtime["path"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "mailbox"
        self.count = Path(self.temp.name) / "count"
        self.pins = Path(self.temp.name) / "pins.json"
        self.pins.write_text(json.dumps(PINS))

    def launch(self, mode="complete", timeout=1000, reconciliation=None):
        script = """
const {createHarnessMailbox}=require(process.argv[1]);
const fs=require('fs');
const mode=process.argv[5];
const h=createHarnessMailbox({root:process.argv[2],pins:JSON.parse(process.argv[3]),
  timeoutMs:Number(process.argv[6]),leaseMs:1000,
  reconciliation:process.argv[7] === 'none' ? undefined : process.argv[7],
  commands:{arm:{value:{type:'integer',min:1,max:4}}},
  handle:(command,args,done)=>{
    fs.appendFileSync(process.argv[4],command+'\\n');
    if(mode==='complete') setTimeout(()=>done.completed(),100);
    if(mode==='slow') setTimeout(()=>done.completed(),400);
    if(mode==='gap') {const end=Date.now()+1200; while(Date.now()<end) {} done.completed();}
    if(mode==='block') {const end=Date.now()+250; while(Date.now()<end) {} done.completed();}
    if(mode==='fail') done.failed();
    if(mode==='throw') throw Error('domain exception');
    if(mode==='clock') {const orig=Date.now; Date.now=()=>orig()+10000;}
  }});
console.log(h.descriptorPath);
process.on('SIGTERM',()=>process.exit(0));
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
                mode,
                str(timeout),
                reconciliation or "none",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop, process)
        line = process.stdout.readline().strip()
        if not line:
            process.wait(timeout=3)
            raise RuntimeError(process.stderr.read())
        return process, Path(line)

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=3)

    def command(self, descriptor, args='{"value":2}', wait="2"):
        return [
            sys.executable,
            "-m",
            "codex_devtools",
            "harness",
            "submit",
            "--descriptor",
            str(descriptor),
            "--expected-pins",
            str(self.pins),
            "--command",
            "arm",
            "--args",
            args,
            "--wait",
            wait,
        ]

    def cli(self, descriptor, **kwargs):
        return subprocess.run(
            self.command(descriptor, **kwargs),
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def calls(self):
        return self.count.read_text().splitlines() if self.count.exists() else []

    def test_completed_cli_and_readonly_discovery(self):
        _, descriptor = self.launch()
        before = set(descriptor.parent.iterdir())
        self.assertEqual(len(client.discover(self.root)), 1)
        self.assertIn("unproven", client.status(descriptor)["liveness_claim"])
        self.assertEqual(before, set(descriptor.parent.iterdir()))
        result = self.cli(descriptor)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "completed")
        self.assertEqual(self.calls(), ["arm"])
        result = self.cli(descriptor)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["sequence"], 2)
        self.assertEqual(len(self.calls()), 2)

    def test_real_two_process_concurrency_one_callback(self):
        _, descriptor = self.launch("slow")
        processes = [
            subprocess.Popen(
                self.command(descriptor),
                cwd=REPO,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = [p.communicate(timeout=5) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes), [0, 2], results)
        self.assertEqual(self.calls(), ["arm"])

    def test_enqueued_blocks_next_actor_until_explicit_completion(self):
        _, descriptor = self.launch("slow")
        result = self.cli(descriptor, wait="0.1")
        self.assertEqual(json.loads(result.stdout)["status"], "enqueued")
        self.assertEqual(result.returncode, 1)
        blocked = self.cli(descriptor)
        self.assertEqual(blocked.returncode, 2)
        time.sleep(0.5)
        self.assertEqual(self.cli(descriptor).returncode, 0)
        self.assertEqual(len(self.calls()), 2)

    def test_timeout_fences_and_late_completion_cannot_release(self):
        _, descriptor = self.launch("slow", timeout=150)
        result = self.cli(descriptor)
        self.assertEqual(json.loads(result.stdout)["status"], "unknown_effect")
        time.sleep(0.5)
        self.assertEqual(client.status(descriptor)["state"], "fenced")
        self.assertTrue((descriptor.parent / "actor").exists())
        self.assertEqual(self.cli(descriptor).returncode, 2)
        self.assertEqual(self.calls(), ["arm"])

    def test_monotonic_lease_gap_fences_even_before_handler_timeout(self):
        _, descriptor = self.launch("gap", timeout=5000)
        result = self.cli(descriptor)
        self.assertEqual(json.loads(result.stdout)["status"], "unknown_effect")
        self.assertEqual(self.calls(), ["arm"])

    def test_malformed_descriptor_is_a_client_error(self):
        _, descriptor = self.launch()
        with self.assertRaisesRegex(ValueError, "schema"):
            malformed = descriptor.parent / "other"
            malformed.mkdir(mode=0o700)
            path = malformed / "descriptor.json"
            path.write_text('{"format":"codex-harness-mailbox-v1"}')
            client.status(path)

    def test_synchronous_block_cannot_complete_after_timeout(self):
        _, descriptor = self.launch("block", timeout=150)
        result = self.cli(descriptor)
        self.assertEqual(json.loads(result.stdout)["status"], "unknown_effect")
        self.assertEqual(client.status(descriptor)["state"], "fenced")
        self.assertEqual(self.calls(), ["arm"])

    def test_terminal_ack_retention_and_replay_never_dispatch(self):
        _, descriptor = self.launch()
        first = None
        for _ in range(34):
            result = client.submit(descriptor, PINS, "arm", {"value": 1}, wait=2)
            self.assertEqual(result["status"], "completed")
            if first is None:
                first = result
        self.assertEqual(len(self.calls()), 34)
        self.assertEqual(len(list(descriptor.parent.glob("ack.*.json"))), 32)
        envelope = self.envelope(descriptor)
        envelope["sequence"] = first["sequence"]
        client._atomic(descriptor.parent / "command.json", envelope)
        time.sleep(0.1)
        self.assertEqual(client.status(descriptor)["state"], "fenced")
        self.assertEqual(len(self.calls()), 34)

    def test_wrong_pins_rejected_without_callback(self):
        _, descriptor = self.launch()
        self.pins.write_text(json.dumps({**PINS, "artifact_sha256": "c" * 64}))
        self.assertEqual(self.cli(descriptor).returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertFalse((descriptor.parent / "actor").exists())

    def envelope(self, descriptor):
        d = client.status(descriptor)
        import uuid

        request = str(uuid.uuid4())
        actor = descriptor.parent / "actor"
        actor.mkdir(mode=0o700)
        (actor / "identity.json").write_text(json.dumps({"request": request}))
        return {k: d[k] for k in ("format", "instance", "nonce", "pins")} | {
            "request": request,
            "sequence": d["sequence"] + 1,
            "command": "arm",
            "args": {"value": 2},
            "deadline_ms": time.time() * 1000 + d["timeout_ms"],
        }

    def bad_envelope(self, change):
        _, descriptor = self.launch()
        envelope = self.envelope(descriptor)
        envelope.update(change)
        client._atomic(descriptor.parent / "command.json", envelope)
        until = time.monotonic() + 2
        while (
            client.status(descriptor)["state"] != "fenced" and time.monotonic() < until
        ):
            time.sleep(0.02)
        self.assertEqual(client.status(descriptor)["state"], "fenced")
        self.assertEqual(self.calls(), [])

    def test_bad_nonce(self):
        self.bad_envelope({"nonce": "wrong"})

    def test_mixed_instance(self):
        self.bad_envelope({"instance": "wrong"})

    def test_bad_source_pin(self):
        self.bad_envelope({"pins": {**PINS, "components": {"driver": "d" * 64}}})

    def test_replay_sequence(self):
        self.bad_envelope({"sequence": 0})

    def test_unknown_command(self):
        self.bad_envelope({"command": "eval"})

    def test_out_of_bounds_args(self):
        self.bad_envelope({"args": {"value": 999}})

    def test_extra_path_arg(self):
        self.bad_envelope({"args": {"value": 2, "path": "/tmp/arbitrary"}})

    def test_expired_command(self):
        self.bad_envelope({"deadline_ms": 1})

    def test_stale_process_has_no_liveness_claim_or_automatic_restart(self):
        process, descriptor = self.launch()
        self.stop(process)
        time.sleep(1.1)
        self.assertFalse(client.status(descriptor)["lease_current"])
        self.assertEqual(self.cli(descriptor).returncode, 2)
        with self.assertRaisesRegex(RuntimeError, "EEXIST"):
            self.launch()
        _, replacement = self.launch(reconciliation="target-unloaded")
        self.assertNotEqual(descriptor, replacement)
        self.assertEqual(self.cli(descriptor).returncode, 2)
        self.assertEqual(self.cli(replacement).returncode, 0)
        self.assertEqual(self.calls(), ["arm"])

    def test_wall_clock_discontinuity_fences(self):
        _, descriptor = self.launch("clock")
        result = self.cli(descriptor)
        self.assertEqual(json.loads(result.stdout)["status"], "unknown_effect")
        self.assertEqual(client.status(descriptor)["state"], "fenced")
        self.assertEqual(self.calls(), ["arm"])

    def test_throw_and_domain_failure_are_distinguished(self):
        process, descriptor = self.launch("fail")
        result = self.cli(descriptor)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")
        self.assertEqual(client.status(descriptor)["state"], "ready")
        self.stop(process)
        _, descriptor = self.launch("throw", reconciliation="domain-reconciled")
        self.assertEqual(
            json.loads(self.cli(descriptor).stdout)["status"], "unknown_effect"
        )
        self.assertEqual(len(self.calls()), 2)

    def test_private_root_and_git_location_refused(self):
        self.root.mkdir(mode=0o755)
        with self.assertRaisesRegex(RuntimeError, "private"):
            self.launch()
        os.chmod(self.root, 0o700)
        (self.root / ".git").mkdir()
        with self.assertRaisesRegex(RuntimeError, "outside Git"):
            self.launch()


if __name__ == "__main__":
    unittest.main()
