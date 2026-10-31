import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools import deploy
from codex_devtools.cli import main


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.source.mkdir()
        self.destination.mkdir()
        self.out = self.root / "plan.json"
        self.backup = self.root / "backup"
        self.manifest = self.root / "manifest.json"

    def manifest_paths(self, paths):
        self.manifest.write_text(
            json.dumps({"format": deploy.MANIFEST_FORMAT, "files": paths})
        )

    def plan(self, paths):
        self.manifest_paths(paths)
        self.assertEqual(
            main(
                [
                    "deploy",
                    "plan",
                    str(self.manifest),
                    "--source-root",
                    str(self.source),
                    "--destination-root",
                    str(self.destination),
                    "--out",
                    str(self.out),
                ]
            ),
            0,
        )
        return json.loads(self.out.read_text())

    def verify(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            result = main(["deploy", "verify", str(self.out)])
            report = json.loads(output.getvalue())
        return result, report

    def test_plan_is_destination_read_only_and_records_both_hashes(self):
        (self.source / "device.amxd").write_bytes(b"source\0\xff\n")
        (self.destination / "device.amxd").write_bytes(b"old\r\n")
        plan = self.plan(["device.amxd"])
        self.assertEqual((self.destination / "device.amxd").read_bytes(), b"old\r\n")
        self.assertEqual(
            plan["files"][0]["source_sha256"], deploy.sha256(b"source\0\xff\n")
        )
        self.assertEqual(
            plan["files"][0]["destination_sha256"], deploy.sha256(b"old\r\n")
        )

    def test_verify_matches_new_destination_bytes_not_original_destination_baseline(
        self,
    ):
        source_bytes = b"planned new bytes"
        (self.source / "device.amxd").write_bytes(source_bytes)
        (self.destination / "device.amxd").write_bytes(b"old destination bytes")
        plan = self.plan(["device.amxd"])
        self.assertNotEqual(
            plan["files"][0]["destination_sha256"], plan["files"][0]["source_sha256"]
        )
        (self.destination / "device.amxd").write_bytes(source_bytes)
        original_plan = self.out.read_bytes()
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (
                self.source / "device.amxd",
                self.destination / "device.amxd",
                self.out,
            )
        }
        directories_before = {
            path: (
                sorted(child.name for child in path.iterdir()),
                path.stat().st_mtime_ns,
            )
            for path in (self.root, self.source, self.destination)
        }

        result, report = self.verify()

        self.assertEqual(result, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(
            report["counts"], {"drift": 0, "match": 1, "missing": 0, "total": 1}
        )
        self.assertEqual(report["files"][0]["status"], "match")
        self.assertEqual(
            report["files"][0]["current_destination_sha256"],
            report["files"][0]["expected_source_sha256"],
        )
        self.assertEqual(self.out.read_bytes(), original_plan)
        self.assertEqual(
            before,
            {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before},
        )
        self.assertEqual(
            directories_before,
            {
                path: (
                    sorted(child.name for child in path.iterdir()),
                    path.stat().st_mtime_ns,
                )
                for path in directories_before
            },
        )

    def test_verify_reports_source_and_destination_drift(self):
        (self.source / "a").write_bytes(b"planned")
        (self.destination / "a").write_bytes(b"planned")
        self.plan(["a"])
        (self.source / "a").write_bytes(b"source edit")
        result, report = self.verify()
        self.assertEqual(result, 1)
        self.assertEqual(report["files"][0]["status"], "drift")
        self.assertEqual(report["files"][0]["detail"], "source_drift")

        (self.source / "a").write_bytes(b"planned")
        (self.destination / "a").write_bytes(b"destination edit")
        result, report = self.verify()
        self.assertEqual(result, 1)
        self.assertEqual(report["files"][0]["status"], "drift")
        self.assertEqual(report["files"][0]["detail"], "destination_drift")

    def test_verify_reports_missing_source_or_destination(self):
        (self.source / "a").write_bytes(b"planned")
        self.plan(["a"])
        result, report = self.verify()
        self.assertEqual(result, 1)
        self.assertEqual(report["files"][0]["status"], "missing")
        self.assertEqual(report["files"][0]["detail"], "destination_missing")

        (self.source / "a").unlink()
        (self.destination / "a").write_bytes(b"planned")
        result, report = self.verify()
        self.assertEqual(result, 1)
        self.assertEqual(report["files"][0]["status"], "missing")
        self.assertEqual(report["files"][0]["detail"], "source_missing")

    def test_verify_rejects_symlink_paths_and_empty_or_malformed_plans(self):
        (self.source / "real").write_bytes(b"planned")
        (self.destination / "real").write_bytes(b"planned")
        self.plan(["real"])
        (self.source / "real").unlink()
        outside = self.root / "outside"
        outside.write_bytes(b"planned")
        try:
            (self.source / "real").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            deploy.verify_plan(self.out)

        (self.source / "real").unlink()
        (self.source / "real").write_bytes(b"planned")
        (self.destination / "real").unlink()
        (self.destination / "real").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            deploy.verify_plan(self.out)

        plan = json.loads(self.out.read_text())
        plan["files"] = []
        self.out.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "non-empty"):
            deploy.verify_plan(self.out)
        self.out.write_text("not json")
        with self.assertRaisesRegex(ValueError, "cannot read JSON"):
            deploy.verify_plan(self.out)

        malformed = {
            "format": deploy.PLAN_FORMAT,
            "source_root": [],
            "destination_root": str(self.destination),
            "files": [
                {"path": "a", "source_sha256": "0" * 64, "destination_sha256": None}
            ],
        }
        self.out.write_text(json.dumps(malformed))
        with self.assertRaisesRegex(ValueError, "source_root"):
            deploy.verify_plan(self.out)

    def test_plan_output_must_remain_outside_source_and_destination_roots(self):
        (self.source / "device.amxd").write_bytes(b"source")
        self.manifest_paths(["device.amxd"])
        for output in (self.source / "plan.json", self.destination / "plan.json"):
            with self.subTest(output=output):
                result = main(
                    [
                        "deploy",
                        "plan",
                        str(self.manifest),
                        "--source-root",
                        str(self.source),
                        "--destination-root",
                        str(self.destination),
                        "--out",
                        str(output),
                    ]
                )
                self.assertEqual(result, 2)
                self.assertFalse(output.exists())

    def test_plan_output_cannot_overwrite_its_manifest(self):
        (self.source / "device.amxd").write_bytes(b"source")
        self.manifest_paths(["device.amxd"])
        original = self.manifest.read_bytes()
        result = main(
            [
                "deploy",
                "plan",
                str(self.manifest),
                "--source-root",
                str(self.source),
                "--destination-root",
                str(self.destination),
                "--out",
                str(self.manifest),
            ]
        )
        self.assertEqual(result, 2)
        self.assertEqual(self.manifest.read_bytes(), original)

    def test_apply_backup_and_rollback_preserve_exact_bytes(self):
        original, deployed = b"old\0\xff\r\n", b"new\0\xf0\r\n"
        (self.source / "device.amxd").write_bytes(deployed)
        (self.destination / "device.amxd").write_bytes(original)
        self.plan(["device.amxd"])
        receipt_path = deploy.apply_plan(self.out, self.backup)
        self.assertEqual((self.destination / "device.amxd").read_bytes(), deployed)
        receipt = json.loads(receipt_path.read_text())
        backup_file = self.backup / receipt["files"][0]["backup"]
        self.assertEqual(backup_file.read_bytes(), original)
        deploy.rollback(receipt_path)
        self.assertEqual((self.destination / "device.amxd").read_bytes(), original)

    def test_created_file_is_removed_by_rollback(self):
        (self.source / "new.maxpat").write_bytes(b"new patch")
        self.plan(["new.maxpat"])
        receipt = deploy.apply_plan(self.out, self.backup)
        self.assertEqual((self.destination / "new.maxpat").read_bytes(), b"new patch")
        deploy.rollback(receipt)
        self.assertFalse((self.destination / "new.maxpat").exists())

    def test_stale_source_or_destination_refuses_before_writes(self):
        (self.source / "a").write_bytes(b"source")
        (self.destination / "a").write_bytes(b"dest")
        self.plan(["a"])
        (self.source / "a").write_bytes(b"source edit")
        with self.assertRaisesRegex(ValueError, "source changed"):
            deploy.apply_plan(self.out, self.backup)
        self.assertEqual((self.destination / "a").read_bytes(), b"dest")
        (self.source / "a").write_bytes(b"source")
        self.plan(["a"])
        (self.destination / "a").write_bytes(b"dest edit")
        with self.assertRaisesRegex(ValueError, "destination changed"):
            deploy.apply_plan(self.out, self.backup)
        self.assertEqual((self.destination / "a").read_bytes(), b"dest edit")

    def test_apply_writes_the_exact_source_bytes_that_passed_hash_check(self):
        planned_source = b"planned source bytes"
        (self.source / "a").write_bytes(planned_source)
        self.plan(["a"])
        original_digest = deploy._digest_or_none
        destination_reads = 0

        def edit_source_after_apply_recheck(path):
            nonlocal destination_reads
            digest = original_digest(path)
            if path == self.destination / "a":
                destination_reads += 1
                if destination_reads == 2:
                    (self.source / "a").write_bytes(b"changed after source hash")
            return digest

        with mock.patch.object(
            deploy, "_digest_or_none", side_effect=edit_source_after_apply_recheck
        ):
            receipt = deploy.apply_plan(self.out, self.backup)
        self.assertEqual((self.destination / "a").read_bytes(), planned_source)
        self.assertEqual(
            json.loads(receipt.read_text())["files"][0]["source_sha256"],
            deploy.sha256(planned_source),
        )

    def test_rollback_refuses_post_deployment_edit(self):
        (self.source / "a").write_bytes(b"new")
        (self.destination / "a").write_bytes(b"old")
        self.plan(["a"])
        receipt = deploy.apply_plan(self.out, self.backup)
        (self.destination / "a").write_bytes(b"user edit")
        with self.assertRaisesRegex(ValueError, "rollback incomplete"):
            deploy.rollback(receipt)
        result = json.loads(receipt.read_text())
        self.assertEqual(result["status"], "recovery_required")
        self.assertEqual((self.destination / "a").read_bytes(), b"user edit")

    def test_rejects_traversal_duplicates_overlaps_and_symlinks(self):
        for paths in (["../escape"], ["a", "a"], ["folder", "folder/child"]):
            with self.subTest(paths=paths):
                self.manifest_paths(paths)
                with self.assertRaises(ValueError):
                    deploy.make_plan(self.manifest, self.source, self.destination)
        (self.source / "real").write_bytes(b"content")
        try:
            (self.source / "link").symlink_to(self.source / "real")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.manifest_paths(["link"])
            deploy.make_plan(self.manifest, self.source, self.destination)

    def test_mid_batch_failure_rolls_back_and_keeps_receipt(self):
        (self.source / "a").write_bytes(b"new a")
        (self.source / "b").write_bytes(b"new b")
        (self.destination / "a").write_bytes(b"old a")
        self.plan(["a", "b"])
        original_atomic_write = deploy._atomic_write

        def fail_second_destination(path, data):
            if path.resolve(strict=False) == (self.destination / "b").resolve(
                strict=False
            ):
                raise OSError("injected write failure")
            return original_atomic_write(path, data)

        with (
            mock.patch.object(
                deploy, "_atomic_write", side_effect=fail_second_destination
            ),
            self.assertRaisesRegex(OSError, "injected"),
        ):
            deploy.apply_plan(self.out, self.backup)
        self.assertEqual((self.destination / "a").read_bytes(), b"old a")
        self.assertFalse((self.destination / "b").exists())
        receipt = json.loads((self.backup / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
