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

    def test_real_cli_selects_one_reference_among_repeated_product_refs(self):
        product = self.root / "INTTR Performance.amxd"
        product.write_bytes(b"product")
        product_ref = (
            f'<FileRef><RelativePathType Value="1"/>'
            f'<RelativePath Value="{product.name}"/><Path Value="{product}"/>'
            "</FileRef>"
        )
        harness_ref = (
            f'<FileRef><RelativePathType Value="1"/>'
            f'<RelativePath Value="{self.old.name}"/><Path Value="{self.old}"/>'
            "</FileRef>"
        )
        xml = (
            '<?xml version="1.0"?><Ableton><LiveSet><Unrelated Value="keep"/>'
            + harness_ref
            + product_ref
            + product_ref
            + "</LiveSet></Ableton>"
        ).encode()
        self.source.write_bytes(gzip.compress(xml, mtime=0))
        args = [
            "ableton",
            "copy-with-device-ref",
            str(self.source),
            str(self.destination),
            "--source-sha256",
            self.digest(self.source),
            "--old-amxd",
            str(self.old),
            "--old-sha256",
            self.digest(self.old),
            "--new-amxd",
            str(self.new),
            "--new-sha256",
            self.digest(self.new),
        ]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(args), 0)
        preview = json.loads(output.getvalue())
        self.assertFalse(self.destination.exists())
        self.assertEqual(preview["state"], "ready")
        self.assertEqual(preview["xml_attribute_edits"], 2)
        self.assertEqual(
            preview["selected_file_ref"],
            {
                "index": 0,
                "old_path": str(self.old),
                "new_path": str(self.new),
            },
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(args + ["--execute"]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["executed"])
        self.assertEqual(result["output_sha256"], preview["output_sha256"])
        expected = xml.replace(
            f'<RelativePath Value="{self.old.name}"/>'.encode(),
            f'<RelativePath Value="{html.escape(self.new.name)}"/>'.encode(),
        ).replace(
            f'<Path Value="{self.old}"/>'.encode(),
            f'<Path Value="{html.escape(str(self.new))}"/>'.encode(),
        )
        self.assertEqual(gzip.decompress(self.destination.read_bytes()), expected)

    def test_cli_replaces_only_explicitly_declared_identical_references(self):
        product = self.root / "Product.amxd"
        product.write_bytes(b"product")

        def file_ref(path, relative, identifier):
            return (
                f'<FileRef Id="{identifier}"><RelativePathType Value="1"/>'
                f'<RelativePath Value="{html.escape(relative, quote=True)}"/>'
                f'<Path Value="{html.escape(str(path), quote=True)}"/>'
                "</FileRef>"
            ).encode()

        first = file_ref(self.old, self.old.name, "first")
        repeated_product = file_ref(product, product.name, "product")
        second = file_ref(self.old, self.old.name, "second")
        xml = (
            b'<?xml version="1.0"?><Ableton><LiveSet><Unrelated Value="keep"/>'
            + first
            + repeated_product
            + second
            + repeated_product
            + b"</LiveSet></Ableton>"
        )
        self.source.write_bytes(gzip.compress(xml, mtime=0))
        args = [
            "ableton",
            "copy-with-device-ref",
            str(self.source),
            str(self.destination),
            "--source-sha256",
            self.digest(self.source),
            "--old-amxd",
            str(self.old),
            "--old-sha256",
            self.digest(self.old),
            "--new-amxd",
            str(self.new),
            "--new-sha256",
            self.digest(self.new),
            "--expected-occurrences",
            "2",
        ]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(args), 0)
        preview = json.loads(output.getvalue())
        self.assertFalse(self.destination.exists())
        self.assertEqual(preview["expected_file_ref_occurrences"], 2)
        self.assertEqual(preview["actual_file_ref_occurrences"], 2)
        self.assertEqual(preview["xml_attribute_edits"], 4)
        self.assertEqual(
            [ref["index"] for ref in preview["selected_file_refs"]], [0, 2]
        )
        self.assertNotIn("selected_file_ref", preview)
        for ref in preview["selected_file_refs"]:
            self.assertEqual(ref["old_path"], str(self.old))
            self.assertEqual(ref["new_path"], str(self.new))
            self.assertEqual(ref["old_relative_path"], self.old.name)
            self.assertEqual(ref["new_relative_path"], self.new.name)
            self.assertEqual(
                bytes.fromhex(ref["old_relative_path_attribute_bytes_hex"]),
                self.old.name.encode(),
            )
            self.assertEqual(
                bytes.fromhex(ref["new_relative_path_attribute_bytes_hex"]),
                self.new.name.replace("&", "&amp;").encode(),
            )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(args + ["--execute"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["selected_file_refs"], preview["selected_file_refs"])
        expected = xml
        for _ in range(2):
            expected = expected.replace(
                f'<RelativePath Value="{self.old.name}"/>'.encode(),
                f'<RelativePath Value="{self.new.name.replace("&", "&amp;")}"/>'.encode(),
                1,
            )
            expected = expected.replace(
                f'<Path Value="{self.old}"/>'.encode(),
                f'<Path Value="{html.escape(str(self.new), quote=True)}"/>'.encode(),
                1,
            )
        self.assertEqual(gzip.decompress(self.destination.read_bytes()), expected)
        self.assertEqual(expected.count(repeated_product), 2)

    def test_declared_occurrence_count_must_match_exactly_before_write(self):
        for expected_occurrences in (0, 2, 17, True):
            with self.subTest(expected_occurrences=expected_occurrences):
                with self.assertRaises(ValueError):
                    self.call(execute=True, expected_occurrences=expected_occurrences)
                self.assertFalse(self.destination.exists())

        twice = self.xml.replace(
            b"</LiveSet>",
            self.xml[
                self.xml.index(b"<FileRef") : self.xml.index(b"</FileRef>")
                + len(b"</FileRef>")
            ]
            + b"</LiveSet>",
        )
        self.source.write_bytes(gzip.compress(twice, mtime=0))
        with self.assertRaisesRegex(ValueError, "declared 1, found 2"):
            self.call(source_sha256=self.digest(self.source), execute=True)
        self.assertFalse(self.destination.exists())

    def test_exact_old_path_must_match_once(self):
        wrong_path = self.xml.replace(
            f'Path Value="{self.old}"'.encode(), b'Path Value="/other/wrong.amxd"'
        )
        duplicate = self.xml.replace(
            b"</LiveSet>",
            self.xml[
                self.xml.index(b"<FileRef") : self.xml.index(b"</FileRef>")
                + len(b"</FileRef>")
            ]
            + b"</LiveSet>",
        )
        for xml in (wrong_path, duplicate):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                self.source.write_bytes(gzip.compress(xml, mtime=0))
                self.call(source_sha256=self.digest(self.source), execute=True)
            self.assertFalse(self.destination.exists())


class DiagnosticSiblingBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "Owned Project"
        self.project.mkdir()
        self.source = self.project / "Source.als"
        self.destination = self.project / "Diagnostic Copy.als"
        self.old = self.root / "old-bundle" / "Harness.amxd"
        self.new = self.root / "new-bundle" / "Harness.amxd"
        self.product = self.root / "Product.amxd"
        for file, data in (
            (self.old, b"old"),
            (self.new, b"new"),
            (self.product, b"product"),
        ):
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        self.relative = "../../../private/tmp/old-bundle/Harness.amxd"
        self.write_source(self.relative)
        self.kwargs = {
            "source_sha256": ReferenceCopyTests.digest(self.source),
            "old_amxd": self.old,
            "old_sha256": ReferenceCopyTests.digest(self.old),
            "new_amxd": self.new,
            "new_sha256": ReferenceCopyTests.digest(self.new),
            "diagnostic_sibling_bundle": True,
        }

    def write_source(self, relative, *, kind="1", extra=b""):
        self.product_ref = (
            f'<FileRef><RelativePathType Value="1"/><RelativePath Value="Product.amxd"/>'
            f'<Path Value="{self.product}"/></FileRef>'
        ).encode()
        self.harness_ref = (
            f'<FileRef Id="7"><RelativePathType Value="{kind}"/>'
            f'<RelativePath Value="{relative}"/><Path Value="{self.old}"/>'
            '<Type Value="2"/><Other Value="preserve"/></FileRef>'
        ).encode()
        self.xml = (
            b'<?xml version="1.0"?><Ableton><LiveSet><Unrelated Value="keep"/>'
            + self.product_ref
            + self.harness_ref
            + self.product_ref
            + extra
            + b"</LiveSet></Ableton>"
        )
        self.source.write_bytes(gzip.compress(self.xml, mtime=0))

    def call(self, **changes):
        return copy_with_device_ref(
            self.source, self.destination, **(self.kwargs | changes)
        )

    def pin_source(self):
        self.kwargs["source_sha256"] = ReferenceCopyTests.digest(self.source)

    def write_duplicate_source(self, first_relative, second_relative):
        def harness_ref(relative, identifier):
            return (
                f'<FileRef Id="{identifier}"><RelativePathType Value="1"/>'
                f'<RelativePath Value="{relative}"/><Path Value="{self.old}"/>'
                '<Type Value="2"/></FileRef>'
            ).encode()

        self.first_harness_ref = harness_ref(first_relative, "first")
        self.second_harness_ref = harness_ref(second_relative, "second")
        self.xml = (
            b'<?xml version="1.0"?><Ableton><LiveSet><Unrelated Value="keep"/>'
            + self.first_harness_ref
            + self.product_ref
            + self.second_harness_ref
            + self.product_ref
            + b"</LiveSet></Ableton>"
        )
        self.source.write_bytes(gzip.compress(self.xml, mtime=0))
        self.pin_source()

    def assert_refused(self, **changes):
        with self.assertRaises((ValueError, OSError)):
            self.call(execute=True, **changes)
        self.assertFalse(self.destination.exists())

    def test_unknown_prefix_default_refuses_and_diagnostic_copies_only_two_attributes(
        self,
    ):
        original = self.source.read_bytes()
        self.assertNotEqual(self.relative, os.path.relpath(self.old, self.project))
        self.assert_refused(diagnostic_sibling_bundle=False)
        preview = self.call()
        self.assertEqual(preview["relative_path_policy"], "diagnostic_sibling_bundle")
        self.assertFalse(self.destination.exists())
        result = self.call(execute=True)
        replacement = self.relative.replace("old-bundle/", "new-bundle/")
        expected = self.xml.replace(
            f'<RelativePath Value="{self.relative}"/>'.encode(),
            f'<RelativePath Value="{replacement}"/>'.encode(),
        ).replace(
            f'<Path Value="{self.old}"/>'.encode(),
            f'<Path Value="{self.new}"/>'.encode(),
        )
        self.assertEqual(gzip.decompress(self.destination.read_bytes()), expected)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(result["output_sha256"], preview["output_sha256"])
        self.assertEqual(result["xml_attribute_edits"], 2)
        self.assertEqual(result["selected_file_ref"]["index"], 1)
        self.assertEqual(expected.count(self.product_ref), 2)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        transform = result["relative_path_transform"]
        prefix = "../../../private/tmp/"
        for key, value in (
            ("old_relative_path", self.relative),
            ("new_relative_path", replacement),
            ("preserved_prefix", prefix),
        ):
            self.assertEqual(transform[key], value)
            self.assertEqual(
                bytes.fromhex(transform[key + "_bytes_hex"]), value.encode()
            )
        self.assertIn("relative base/resolution unverified", result["evidence_limit"])
        self.assertIn(
            "native exact component/manifest pins required before effects",
            result["evidence_limit"],
        )
        self.assertIn("no Live load/dependency/recall proof", result["evidence_limit"])

    def test_duplicate_refs_validate_and_preserve_each_diagnostic_prefix(self):
        first_prefix = "../../../private/tmp/"
        second_prefix = "folder with spaces/v1.2/"
        first_relative = first_prefix + "old-bundle/Harness.amxd"
        second_relative = second_prefix + "old-bundle/Harness.amxd"
        self.write_duplicate_source(first_relative, second_relative)

        preview = self.call(expected_occurrences=2)
        self.assertFalse(self.destination.exists())
        self.assertEqual(preview["expected_file_ref_occurrences"], 2)
        self.assertEqual(preview["actual_file_ref_occurrences"], 2)
        self.assertEqual(preview["xml_attribute_edits"], 4)
        self.assertEqual(
            [row["index"] for row in preview["selected_file_refs"]], [0, 2]
        )
        self.assertEqual(len(preview["relative_path_transforms"]), 2)
        for ref, transform, old_relative, prefix in zip(
            preview["selected_file_refs"],
            preview["relative_path_transforms"],
            (first_relative, second_relative),
            (first_prefix, second_prefix),
            strict=True,
        ):
            expected_relative = prefix + "new-bundle/Harness.amxd"
            self.assertEqual(ref["old_relative_path"], old_relative)
            self.assertEqual(ref["new_relative_path"], expected_relative)
            self.assertEqual(transform["index"], ref["index"])
            self.assertEqual(transform["preserved_prefix"], prefix)
            self.assertEqual(
                bytes.fromhex(ref["old_relative_path_attribute_bytes_hex"]),
                old_relative.encode(),
            )
            self.assertEqual(
                bytes.fromhex(ref["new_relative_path_attribute_bytes_hex"]),
                expected_relative.encode(),
            )
            self.assertEqual(
                bytes.fromhex(transform["preserved_prefix_bytes_hex"]), prefix.encode()
            )

        result = self.call(execute=True, expected_occurrences=2)
        expected = self.xml
        for old_relative, prefix in (
            (first_relative, first_prefix),
            (second_relative, second_prefix),
        ):
            new_relative = prefix + "new-bundle/Harness.amxd"
            expected = expected.replace(
                f'<RelativePath Value="{old_relative}"/>'.encode(),
                f'<RelativePath Value="{new_relative}"/>'.encode(),
                1,
            ).replace(
                f'<Path Value="{self.old}"/>'.encode(),
                f'<Path Value="{self.new}"/>'.encode(),
                1,
            )
        self.assertEqual(gzip.decompress(self.destination.read_bytes()), expected)
        self.assertEqual(result["output_sha256"], preview["output_sha256"])
        self.assertEqual(expected.count(self.product_ref), 2)

    def test_mixed_unsupported_duplicate_relative_path_refuses_before_write(self):
        self.write_duplicate_source(
            "../../../private/tmp/old-bundle/Harness.amxd",
            "../../../private/tmp/other-bundle/Harness.amxd",
        )
        with self.assertRaisesRegex(ValueError, "unsupported diagnostic relative path"):
            self.call(execute=True, expected_occurrences=2)
        self.assertFalse(self.destination.exists())

    def test_literal_zero_or_bounded_parent_prefix_is_preserved(self):
        for prefix in ("", "../", "../" * 32, "folder with spaces/v1.2/"):
            with self.subTest(prefix=prefix):
                self.write_source(prefix + "old-bundle/Harness.amxd")
                self.pin_source()
                transform = self.call()["relative_path_transform"]
                self.assertEqual(transform["preserved_prefix"], prefix)
                self.assertEqual(
                    transform["new_relative_path"], prefix + "new-bundle/Harness.amxd"
                )
        self.assertFalse(self.destination.exists())

    def test_non_sibling_or_different_basename_refuses(self):
        for relative in (
            "different-grandparent/new-bundle/Harness.amxd",
            "new-bundle/Different.amxd",
        ):
            with self.subTest(relative=relative):
                candidate = self.root / relative
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(b"new")
                self.assert_refused(new_amxd=candidate)
        self.assert_refused(
            new_amxd=self.old, new_sha256=ReferenceCopyTests.digest(self.old)
        )
        directory = self.root / "directory-bundle" / "Harness.amxd"
        directory.mkdir(parents=True)
        self.assert_refused(new_amxd=directory)

    def test_malformed_ambiguous_or_unbounded_relative_refuses(self):
        invalid = (
            "/old-bundle/Harness.amxd",
            "C:/old-bundle/Harness.amxd",
            r"..\old-bundle\Harness.amxd",
            "../old-bundle/Harness.amxd\x00",
            "./old-bundle/Harness.amxd",
            "x/../old-bundle/Harness.amxd",
            ".././old-bundle/Harness.amxd",
            "../old-bundle//Harness.amxd",
            "../old-bundle/Harness.amxd/",
            "../old-bundle/Harness.amxd/.",
            "../old-bundle/Harness.amxd.",
            "../old-bundle/Harness.amxd ",
            "../other-old-bundle/Harness.amxd",
            "../wrong/Harness.amxd",
            "../old-bundle/Wrong.amxd",
            "../old-bundle/Harness.amxd/surplus",
            "../old-bundle/old-bundle/Harness.amxd",
            "../Harness.amxd/old-bundle/Harness.amxd",
            "../new-bundle/old-bundle/Harness.amxd",
            "../bad%2fname/old-bundle/Harness.amxd",
            "../é/old-bundle/Harness.amxd",
            "../x./old-bundle/Harness.amxd",
            "../" * 33 + "old-bundle/Harness.amxd",
            "a/" * 63 + "old-bundle/Harness.amxd",
            "a" * 4096 + "/old-bundle/Harness.amxd",
            "../../../private/tmp/&#111;ld-bundle/Harness.amxd",
            "../x&#10;y/old-bundle/Harness.amxd",
        )
        for relative in invalid:
            with self.subTest(relative=relative):
                self.write_source(relative)
                self.pin_source()
                self.assert_refused()

    def test_wrong_type_duplicate_or_unsupported_attribute_refuses(self):
        for kind in ("", "0", "2", "3"):
            with self.subTest(kind=kind):
                self.write_source(self.relative, kind=kind)
                self.pin_source()
                self.assert_refused()
        self.write_source(self.relative)
        duplicate = self.harness_ref
        self.write_source(self.relative, extra=duplicate)
        self.pin_source()
        self.assert_refused()
        self.write_source(self.relative)
        self.xml = self.xml.replace(
            f'<RelativePath Value="{self.relative}"/>'.encode(),
            f"<RelativePath Value='{self.relative}'/>".encode(),
        )
        self.source.write_bytes(gzip.compress(self.xml, mtime=0))
        self.pin_source()
        self.assert_refused()

    def test_missing_old_reference_and_non_sibling_set_destinations_refuse(self):
        wrong = self.xml.replace(
            f'<Path Value="{self.old}"/>'.encode(),
            b'<Path Value="/missing/Harness.amxd"/>',
        )
        self.source.write_bytes(gzip.compress(wrong, mtime=0))
        self.pin_source()
        self.assert_refused()
        self.write_source(self.relative)
        self.pin_source()
        for destination in (
            self.source,
            self.root / "Outside.als",
            self.project / "Wrong.txt",
        ):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                copy_with_device_ref(
                    self.source, destination, **self.kwargs, execute=True
                )
        self.assertFalse(self.destination.exists())

    def test_stale_and_invalid_hash_pins_refuse(self):
        for key in ("source_sha256", "old_sha256", "new_sha256"):
            for value in ("0" * 64, "invalid"):
                with self.subTest(key=key, value=value):
                    self.assert_refused(**{key: value})

    def test_noncanonical_inputs_and_alias_destination_refuse(self):
        alias = self.root / "alias"
        alias.symlink_to(self.old.parent, target_is_directory=True)
        self.assert_refused(old_amxd=alias / self.old.name)
        alias.unlink()
        alias.symlink_to(self.new.parent, target_is_directory=True)
        self.assert_refused(new_amxd=alias / self.new.name)
        source_alias = self.project / "Alias.als"
        source_alias.symlink_to(self.source)
        with self.assertRaises(ValueError):
            copy_with_device_ref(
                source_alias, self.destination, **self.kwargs, execute=True
            )
        parent_alias = self.root / "project-alias"
        parent_alias.symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(ValueError):
            copy_with_device_ref(
                self.source,
                parent_alias / self.destination.name,
                **self.kwargs,
                execute=True,
            )
        self.assertFalse(self.destination.exists())

    def test_no_overwrite_and_exclusive_creation_are_retained(self):
        self.destination.write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            self.call(execute=True)
        self.assertEqual(self.destination.read_bytes(), b"preserve")
        self.destination.unlink()
        self.destination.symlink_to(self.root / "missing")
        with self.assertRaises(ValueError):
            self.call(execute=True)
        self.assertTrue(self.destination.is_symlink())
        self.destination.unlink()
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

    def test_each_pinned_input_is_revalidated_before_creating_output(self):
        from codex_devtools.ableton_copy import (
            _local_file_report,
            inspect_set_device_refs,
        )

        for path in (self.source, self.old, self.new):
            original = path.read_bytes()
            with self.subTest(path=path):
                if path == self.source:

                    def inspect_then_change(selected, path=path, original=original):
                        report = inspect_set_device_refs(selected)
                        path.write_bytes(original + b"changed")
                        return report

                    with patch(
                        "codex_devtools.ableton_copy.inspect_set_device_refs",
                        side_effect=inspect_then_change,
                    ):
                        self.assert_refused()
                else:

                    def report_then_change(selected, path=path, original=original):
                        report = _local_file_report(selected)
                        if selected == str(path):
                            path.write_bytes(original + b"changed")
                        return report

                    with patch(
                        "codex_devtools.ableton_copy._local_file_report",
                        side_effect=report_then_change,
                    ):
                        self.assert_refused()
                path.write_bytes(original)

    def test_canonical_source_is_revalidated_even_when_alias_bytes_match(self):
        from codex_devtools.ableton_copy import inspect_set_device_refs

        original = self.source.read_bytes()
        moved = self.project / "Moved.als"

        def inspect_then_alias(selected):
            report = inspect_set_device_refs(selected)
            self.source.rename(moved)
            self.source.symlink_to(moved)
            return report

        with patch(
            "codex_devtools.ableton_copy.inspect_set_device_refs",
            side_effect=inspect_then_alias,
        ):
            self.assert_refused()
        self.assertEqual(moved.read_bytes(), original)

    def test_symlink_alias_counterexample_does_not_claim_resolution(self):
        # Even canonical target siblings do not prove the unknown relative base:
        # this base reaches old via a bundle symlink and has no matching new.
        base = self.root / "unknown-base"
        base.mkdir()
        (base / self.old.parent.name).symlink_to(
            self.old.parent, target_is_directory=True
        )
        relative = f"{self.old.parent.name}/{self.old.name}"
        self.write_source(relative)
        self.pin_source()
        self.assertEqual((base / relative).resolve(strict=True), self.old)
        result = self.call(execute=True)
        transformed = result["relative_path_transform"]["new_relative_path"]
        self.assertFalse((base / transformed).exists())
        self.assertNotEqual((base / transformed).resolve(), self.new)
        self.assertIn("resolution unverified", result["evidence_limit"])
        self.assertIn("syntactically", result["relative_path_transform"]["base_policy"])

    def test_real_cli_preview_default_refusal_and_explicit_execute(self):
        import subprocess
        import sys

        args = [
            sys.executable,
            "-m",
            "codex_devtools",
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
        default = subprocess.run(args, capture_output=True, text=True, check=False)
        self.assertEqual(default.returncode, 2, default.stderr)
        self.assertFalse(self.destination.exists())
        for execute in (False, True):
            invocation = subprocess.run(
                args
                + ["--diagnostic-sibling-bundle"]
                + (["--execute"] if execute else []),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(invocation.returncode, 0, invocation.stderr)
            receipt = json.loads(invocation.stdout)
            self.assertEqual(receipt["executed"], execute)
            self.assertEqual(
                receipt["relative_path_policy"], "diagnostic_sibling_bundle"
            )
            self.assertEqual(self.destination.exists(), execute)
