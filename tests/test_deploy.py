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
        self.manifest.write_text(json.dumps({"format": deploy.MANIFEST_FORMAT, "files": paths}))

    def plan(self, paths):
        self.manifest_paths(paths)
        self.assertEqual(main([
            "deploy", "plan", str(self.manifest), "--source-root", str(self.source),
            "--destination-root", str(self.destination), "--out", str(self.out),
        ]), 0)
        return json.loads(self.out.read_text())

    def test_plan_is_destination_read_only_and_records_both_hashes(self):
        (self.source / "device.amxd").write_bytes(b"source\0\xff\n")
        (self.destination / "device.amxd").write_bytes(b"old\r\n")
        plan = self.plan(["device.amxd"])
        self.assertEqual((self.destination / "device.amxd").read_bytes(), b"old\r\n")
        self.assertEqual(plan["files"][0]["source_sha256"], deploy.sha256(b"source\0\xff\n"))
        self.assertEqual(plan["files"][0]["destination_sha256"], deploy.sha256(b"old\r\n"))

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
            if path.resolve(strict=False) == (self.destination / "b").resolve(strict=False):
                raise OSError("injected write failure")
            return original_atomic_write(path, data)

        with mock.patch.object(deploy, "_atomic_write", side_effect=fail_second_destination):
            with self.assertRaisesRegex(OSError, "injected"):
                deploy.apply_plan(self.out, self.backup)
        self.assertEqual((self.destination / "a").read_bytes(), b"old a")
        self.assertFalse((self.destination / "b").exists())
        receipt = json.loads((self.backup / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
