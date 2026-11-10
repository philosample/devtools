import contextlib
import gzip
import hashlib
import html
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_devtools.ableton_copy import copy_with_device_ref
from codex_devtools.cli import main


class ReferenceCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "Source.als"
        self.destination = self.root / "Fresh.als"
        self.old = self.root / "old.amxd"
        self.new = self.root / "new & reviewed.amxd"
        self.old.write_bytes(b"old")
        self.new.write_bytes(b"new")
        self.xml = (
            '<?xml version="1.0"?><Ableton><LiveSet><Unrelated Value="unchanged"/>'
            '<FileRef Id="7"><RelativePathType Value="1"/><RelativePath Value="old.amxd"/>'
            f'<Path Value="{self.old}"/><Type Value="2"/></FileRef></LiveSet></Ableton>'
        ).encode()
        self.source.write_bytes(gzip.compress(self.xml, mtime=0))
        self.kwargs = {
            "source_sha256": self.digest(self.source),
            "old_amxd": self.old,
            "old_sha256": self.digest(self.old),
            "new_amxd": self.new,
            "new_sha256": self.digest(self.new),
        }

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def call(self, **changes):
        return copy_with_device_ref(
            self.source, self.destination, **(self.kwargs | changes)
        )

    def test_preview_then_copy_preserves_all_other_xml_bytes_and_original(self):
        original = self.source.read_bytes()
        preview = self.call()
        self.assertEqual(preview["state"], "ready")
        self.assertFalse(self.destination.exists())
        result = self.call(execute=True)
        self.assertTrue(result["executed"])
        self.assertEqual(preview["output_sha256"], result["output_sha256"])
        expected = self.xml.replace(
            b'Value="old.amxd"', f'Value="{html.escape(self.new.name)}"'.encode()
        )
        expected = expected.replace(
            str(self.old).encode(), html.escape(str(self.new)).encode()
        )
        self.assertEqual(gzip.decompress(self.destination.read_bytes()), expected)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)

    def test_stale_pins_never_create_output(self):
        for key in ("source_sha256", "old_sha256", "new_sha256"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.call(execute=True, **{key: "0" * 64})
        self.assertFalse(self.destination.exists())

    def test_existing_destination_and_aliases_are_preserved(self):
        self.destination.write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            self.call(execute=True)
        self.assertEqual(self.destination.read_bytes(), b"preserve")
        for destination in (self.source, self.root.parent / "escape.als"):
            with self.assertRaises(ValueError):
                copy_with_device_ref(
                    self.source, destination, **self.kwargs, execute=True
                )
        self.destination.unlink()
        self.destination.symlink_to(self.root / "missing")
        with self.assertRaises(ValueError):
            self.call(execute=True)
        self.assertTrue(self.destination.is_symlink())

    def test_multiple_or_unsupported_refs_refuse_without_writing(self):
        for xml in (
            self.xml.replace(
                b"</LiveSet>",
                b'<FileRef><Path Value="extra.amxd"/></FileRef></LiveSet>',
            ),
            self.xml.replace(
                b'RelativePathType Value="1"', b'RelativePathType Value="3"'
            ),
            self.xml.replace(b'Value="old.amxd"', b'Value="wrong.amxd"'),
        ):
            self.source.write_bytes(gzip.compress(xml, mtime=0))
            with self.assertRaises(ValueError):
                self.call(source_sha256=self.digest(self.source), execute=True)
            self.assertFalse(self.destination.exists())

    def test_concurrent_destination_creation_is_not_overwritten(self):
        original_open = os.open

        def raced(path, flags, *args, **kwargs):
            if Path(path) == self.destination:
                self.destination.write_bytes(b"other writer")
            return original_open(path, flags, *args, **kwargs)

        with (
            patch("codex_devtools.ableton_copy.os.open", side_effect=raced),
            self.assertRaises(FileExistsError),
        ):
            self.call(execute=True)
        self.assertEqual(self.destination.read_bytes(), b"other writer")

    def test_real_cli_preview_and_execute(self):
        args = [
            "ableton",
            "copy-with-device-ref",
            str(self.source),
            str(self.destination),
            "--source-sha256",
            self.kwargs["source_sha256"],
            "--old-amxd",
            str(self.old),
            "--old-sha256",
            self.kwargs["old_sha256"],
            "--new-amxd",
            str(self.new),
            "--new-sha256",
            self.kwargs["new_sha256"],
        ]
        for execute in (False, True):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(args + (["--execute"] if execute else []))
            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["executed"], execute)
            self.assertEqual(self.destination.exists(), execute)
