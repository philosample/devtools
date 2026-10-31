import json
import contextlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools.cli import _manifest, main
from codex_devtools.maxfiles import decode_file, extract_archive, graph_report, parse_mxf


def chunk(tag, payload):
    return tag + struct.pack(">I", len(payload) + 8) + payload


def archive(items):
    """Build a valid synthetic MXF from (name, payload) entries."""
    offset = 16
    payload = bytearray()
    directory = []
    for name, data in items:
        name_b = name.encode("utf-8") + b"\0"
        directory_record = b"".join((
            chunk(b"fnam", name_b),
            chunk(b"of32", struct.pack(">I", offset)),
            chunk(b"sz32", struct.pack(">I", len(data))),
        ))
        directory.append(chunk(b"dire", directory_record))
        payload.extend(data)
        offset += len(data)
    body_end = offset
    directory_chunk = chunk(b"dlst", b"".join(directory))
    return b"mx@c" + struct.pack(">III", 16, 0, body_end) + payload + directory_chunk


def amxd(body):
    return b"ampf" + bytes(20) + b"ptch" + struct.pack("<I", len(body)) + body


def valid_patch():
    return {"patcher": {"boxes": [
        {"box": {"id": "src", "maxclass": "newobj", "numoutlets": 1, "numinlets": 0}},
        {"box": {"id": "sink", "maxclass": "newobj", "numoutlets": 0, "numinlets": 1}},
    ], "lines": [{"patchline": {"source": ["src", 0], "destination": ["sink", 0]}}]}}


class ArchiveTests(unittest.TestCase):
    def test_raw_mxf_amxd_and_plain_json(self):
        root = json.dumps(valid_patch()).encode()
        raw = archive([("device.maxpat", root), ("factory.bin", b"\x00asset\xff")])
        self.assertEqual([e["name"] for e in parse_mxf(raw)], ["device.maxpat", "factory.bin"])
        self.assertEqual(decode_file(raw)[0], "mxf")
        self.assertEqual(decode_file(amxd(raw))[0], "amxd")
        self.assertEqual(decode_file(root)[0], "json")

    def test_amxd_wrapper_size_is_bounded(self):
        wrapped = amxd(archive([("root.maxpat", json.dumps(valid_patch()).encode())]))
        self.assertEqual(decode_file(wrapped)[0], "amxd")
        with self.assertRaisesRegex(ValueError, "size does not match"):
            decode_file(wrapped[:-1])

    def test_truncation_and_boundaries(self):
        raw = archive([("root.maxpat", b"{}")])
        with self.assertRaises(ValueError):
            parse_mxf(raw[:-1])
        broken = bytearray(raw)
        struct.pack_into(">I", broken, 12, len(raw) + 1)
        with self.assertRaisesRegex(ValueError, "boundary"):
            parse_mxf(bytes(broken))
        with self.assertRaises(ValueError):
            parse_mxf(b"mx@c" + bytes(4) + bytes(4))

    def test_traversal_and_duplicate_names_rejected(self):
        patch = json.dumps(valid_patch()).encode()
        for entries in (("../escape", patch), ("folder\\escape", patch), ("C:escape", patch)):
            with self.assertRaisesRegex(ValueError, "unsafe"):
                parse_mxf(archive([entries]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_mxf(archive([("same", b"a"), ("same", b"b")]))

    def test_extract_preserves_bytes_and_refuses_overwrite(self):
        original = b"{\"patcher\":{}}\0\r\n"
        opaque = bytes(range(256))
        raw = archive([("device.maxpat", original), ("factory.bin", opaque)])
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / "device.mxf", Path(tmp) / "extracted"
            source.write_bytes(raw)
            extract_archive(source, out)
            self.assertEqual((out / "device.maxpat").read_bytes(), original)
            self.assertEqual((out / "factory.bin").read_bytes(), opaque)
            (out / "unrelated.txt").write_text("keep")
            with self.assertRaises(FileExistsError):
                extract_archive(source, out)
            self.assertEqual((out / "unrelated.txt").read_text(), "keep")


class GraphAndManifestTests(unittest.TestCase):
    def test_invalid_ports_and_recursive_patchers(self):
        patch = valid_patch()
        patch["patcher"]["lines"][0]["patchline"]["source"][1] = 1
        patch["patcher"]["boxes"].append({"box": {
            "id": "nested", "numinlets": 0, "numoutlets": 0,
            "patcher": {"boxes": [
                {"box": {"id": "x", "numoutlets": 0, "numinlets": 0}},
                {"box": {"id": "x", "numoutlets": 0, "numinlets": 0}},
            ], "lines": []},
        }})
        report = graph_report(patch)
        self.assertTrue(any("invalid source port" in error for error in report["errors"]))
        self.assertTrue(any("duplicate ID" in error for error in report["errors"]))
        self.assertEqual(report["counts"]["patchers"], 2)

    def test_manifest_is_deterministic_and_excludes_output_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.txt").write_text("B")
            (root / "a.txt").write_text("A")
            (root / ".git").mkdir()
            (root / ".git" / "secret").write_text("excluded")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "bytecode.pyc").write_bytes(b"excluded")
            output = root / "manifest.json"
            first = _manifest(root, output)
            output.write_text("old manifest")
            second = _manifest(root, output)
            self.assertEqual(first, second)
            self.assertEqual([entry["path"] for entry in first["files"]], ["a.txt", "b.txt"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["max", "validate", str(root / "a.txt")]), 2)


if __name__ == "__main__":
    unittest.main()
