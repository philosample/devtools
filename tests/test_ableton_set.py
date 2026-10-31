from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from xml.sax.saxutils import quoteattr

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools.ableton_set import inspect_set_device_refs
from codex_devtools.cli import main


def als_xml(paths: list[tuple[str, str | None]]) -> bytes:
    refs = []
    for path, relative in paths:
        relative_xml = "" if relative is None else f"<RelativePath Value={quoteattr(relative)}/><RelativePathType Value=\"1\"/>"
        refs.append(
            "<Device><FileRef Id=\"0\">"
            f"{relative_xml}<Path Value={quoteattr(path)}/><Type Value=\"2\"/>"
            "</FileRef></Device>"
        )
    body = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Ableton MajorVersion=\"5\" MinorVersion=\"12.0_test\" Creator=\"Ableton Live\">"
        f"<LiveSet><Tracks>{''.join(refs)}</Tracks></LiveSet></Ableton>"
    )
    return body.encode("utf-8")


def write_als(path: Path, xml: bytes | str) -> None:
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    path.write_bytes(gzip.compress(xml, mtime=0))


class AbletonSetDeviceReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.set_path = self.root / "selected.als"
        self.device_dir = self.root / "candidate"
        self.device_dir.mkdir()
        self.device = self.device_dir / "Probe.amxd"
        self.device.write_bytes(b"chosen AMXD bytes")

    def tearDown(self):
        self.temp.cleanup()

    def inspect(self, paths, **kwargs):
        write_als(self.set_path, als_xml(paths))
        return inspect_set_device_refs(self.set_path, **kwargs)

    def test_observed_fileref_shape_hashes_exact_reference_and_deduplicates(self):
        raw_path = str(self.device)
        set_data = gzip.compress(als_xml([
            (raw_path, "../../candidate/Probe.amxd"),
            (raw_path, "../../candidate/Probe.amxd"),
            ("/Library/Some Effect.adv", "../../Library/Some Effect.adv"),
        ]), mtime=0)
        self.set_path.write_bytes(set_data)
        expected_sha = hashlib.sha256(self.device.read_bytes()).hexdigest()
        result = inspect_set_device_refs(
            self.set_path, expected_amxd_path=self.device, expected_amxd_sha256=expected_sha
        )
        self.assertEqual(result["schema"], "codex-devtools-ableton-set-device-refs-v1")
        self.assertEqual(result["evidence_level"], "saved_set_file_references_only")
        self.assertEqual(result["set"]["sha256"], hashlib.sha256(set_data).hexdigest())
        self.assertEqual(result["file_ref_count"], 3)
        self.assertEqual(result["unique_file_ref_path_count"], 2)
        self.assertEqual(len(result["amxd_references"]), 1)
        ref = result["amxd_references"][0]
        self.assertEqual(ref["raw_path"], raw_path)
        self.assertEqual(ref["occurrences"], 2)
        self.assertEqual(ref["relative_paths"], ["../../candidate/Probe.amxd"])
        self.assertEqual(ref["local_file"]["resolved_path"], str(self.device.resolve()))
        self.assertEqual(ref["local_file"]["sha256"], expected_sha)
        self.assertEqual(result["expected_amxd_assertion"]["status"], "passed")

    def test_same_basename_old_reference_fails_exact_path_even_when_hash_matches(self):
        old_dir = self.root / "old-candidate"
        old_dir.mkdir()
        old_same_basename = old_dir / self.device.name
        old_same_basename.write_bytes(self.device.read_bytes())
        result = self.inspect([(str(old_same_basename), "../../old-candidate/Probe.amxd")])
        self.assertEqual(result["amxd_references"][0]["local_file"]["sha256"],
                         hashlib.sha256(self.device.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, "exact path"):
            inspect_set_device_refs(
                self.set_path,
                expected_amxd_path=self.device,
                expected_amxd_sha256=hashlib.sha256(self.device.read_bytes()).hexdigest(),
            )

    def test_expected_assertion_rejects_ambiguous_missing_and_hash_mismatch(self):
        other = self.root / "other.amxd"
        other.write_bytes(b"other device")
        self.set_als = self.set_path
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.inspect([(str(self.device), "device.amxd"), (str(other), "other.amxd")],
                         expected_amxd_path=self.device,
                         expected_amxd_sha256=hashlib.sha256(self.device.read_bytes()).hexdigest())

        missing = self.root / "missing.amxd"
        with self.assertRaisesRegex(ValueError, "not a readable local file"):
            self.inspect([(str(missing), "missing.amxd")], expected_amxd_path=self.device,
                         expected_amxd_sha256=hashlib.sha256(self.device.read_bytes()).hexdigest())

        with self.assertRaisesRegex(ValueError, "supplied digest"):
            self.inspect([(str(self.device), "device.amxd")], expected_amxd_path=self.device,
                         expected_amxd_sha256="0" * 64)

    def test_missing_target_is_reported_without_claiming_host_failure(self):
        missing = self.root / "missing.amxd"
        result = self.inspect([(str(missing), "missing.amxd")])
        self.assertEqual(result["amxd_references"][0]["local_file"]["status"], "missing")
        self.assertFalse(result["amxd_references"][0]["local_file"]["exists"])
        self.assertIsNone(result["amxd_references"][0]["local_file"]["sha256"])
        self.assertIn("do not prove the active Live instance", result["evidence_limit"])

    def test_rejects_unknown_schema_missing_amxd_malformed_gzip_xml_and_dtd(self):
        write_als(self.set_path, b"<Unknown><LiveSet/></Unknown>")
        with self.assertRaisesRegex(ValueError, "unsupported Set XML root"):
            inspect_set_device_refs(self.set_path)
        write_als(self.set_path, b"<Ableton><LiveSet><Device/></LiveSet></Ableton>")
        with self.assertRaisesRegex(ValueError, "no FileRef entries"):
            inspect_set_device_refs(self.set_path)
        write_als(self.set_path, b"<Ableton><LiveSet><FileRef><Path Value=\"/x.adv\"/></FileRef></LiveSet></Ableton>")
        with self.assertRaisesRegex(ValueError, "no .amxd FileRef"):
            inspect_set_device_refs(self.set_path)
        self.set_path.write_bytes(b"not gzip")
        with self.assertRaisesRegex(ValueError, "not a gzip stream"):
            inspect_set_device_refs(self.set_path)
        self.set_path.write_bytes(gzip.compress(b"<Ableton><LiveSet>", mtime=0))
        with self.assertRaisesRegex(ValueError, "malformed"):
            inspect_set_device_refs(self.set_path)
        dtd = b'<!DOCTYPE Ableton [<!ENTITY x "expanded">]><Ableton><LiveSet>&x;</LiveSet></Ableton>'
        write_als(self.set_path, dtd)
        with self.assertRaisesRegex(ValueError, "DTD and entity"):
            inspect_set_device_refs(self.set_path)

    def test_rejects_decompression_limit_and_bad_expectation_pair(self):
        import codex_devtools.ableton_set as ableton_set
        write_als(self.set_path, als_xml([(str(self.device), "device.amxd")]))
        original = ableton_set.MAX_SET_XML_BYTES
        try:
            ableton_set.MAX_SET_XML_BYTES = 16
            with self.assertRaisesRegex(ValueError, "decompressed Set XML exceeds"):
                inspect_set_device_refs(self.set_path)
        finally:
            ableton_set.MAX_SET_XML_BYTES = original
        with self.assertRaisesRegex(ValueError, "must be supplied together"):
            inspect_set_device_refs(self.set_path, expected_amxd_path=self.device)
        with self.assertRaisesRegex(ValueError, "64 hexadecimal"):
            inspect_set_device_refs(self.set_path, expected_amxd_path=self.device, expected_amxd_sha256="bad")

    def test_reference_limit_counts_sibling_entries(self):
        import codex_devtools.ableton_set as ableton_set
        original = ableton_set.MAX_REFERENCES
        try:
            ableton_set.MAX_REFERENCES = 1
            write_als(self.set_path, als_xml([
                (str(self.device), "device.amxd"),
                (str(self.device), "device.amxd"),
            ]))
            with self.assertRaisesRegex(ValueError, "1-FileRef limit"):
                inspect_set_device_refs(self.set_path)
        finally:
            ableton_set.MAX_REFERENCES = original

    def test_cli_returns_nonzero_for_stale_same_basename_reference(self):
        old_dir = self.root / "stale"
        old_dir.mkdir()
        old = old_dir / self.device.name
        old.write_bytes(b"stale bytes")
        self.inspect([(str(old), "../../stale/Probe.amxd")])
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["ableton", "inspect-set-device-refs", str(self.set_path),
                         "--expect-amxd-path", str(self.device),
                         "--expect-amxd-sha256", hashlib.sha256(self.device.read_bytes()).hexdigest()])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("exact path", stderr.getvalue())

    def test_cli_emits_machine_readable_success_for_exact_reference(self):
        self.inspect([(str(self.device), "device.amxd")])
        expected_sha = hashlib.sha256(self.device.read_bytes()).hexdigest()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["ableton", "inspect-set-device-refs", str(self.set_path),
                         "--expect-amxd-path", str(self.device),
                         "--expect-amxd-sha256", expected_sha])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        result = __import__("json").loads(stdout.getvalue())
        self.assertEqual(result["expected_amxd_assertion"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
