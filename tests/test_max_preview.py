import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from codex_devtools.cli import main
from codex_devtools.max_preview import inspect_patch, preview, render_svg


def patch_fixture():
    return {"patcher": {
        "boxes": [
            {"box": {
                "id": "dial", "maxclass": "live.dial", "presentation": 1,
                "presentation_rect": [10, 20, 100, 40], "patching_rect": [300, 400, 30, 30],
                "saved_attribute_attributes": {"valueof": {"parameter_longname": "Cutoff"}},
            }},
            {"box": {
                "id": "js", "maxclass": "newobj", "text": "js preview.js & <tag>", "presentation": 1,
                "presentation_rect": [80, 30, 100, 40], "patching_rect": [350, 400, 40, 30],
            }},
            {"box": {"id": "hidden", "maxclass": "comment", "text": "hidden", "presentation": 0, "patching_rect": [600, 700, 25, 10]}},
            {"box": {"id": "broken", "maxclass": "newobj", "text": "bad rect", "presentation": 1, "presentation_rect": [1, 2, -3, 8]}},
            {"box": {
                "id": "nested", "maxclass": "newobj", "text": "p child", "patching_rect": [1, 2, 100, 50],
                "patcher": {"boxes": [
                    {"box": {"id": "a", "maxclass": "newobj", "text": "loadbang", "patching_rect": [5, 6, 40, 20]}},
                    {"box": {"id": "b", "maxclass": "message", "text": "bang", "patching_rect": [30, 10, 30, 20]}},
                ], "lines": []},
            }},
        ],
        "lines": [],
    }}


class MaxPreviewTests(unittest.TestCase):
    def test_presentation_geometry_bounds_overlap_and_nested_patching_mode(self):
        report = inspect_patch(patch_fixture())
        self.assertEqual([patcher["path"] for patcher in report], ["root", "root/p child"])
        root, nested = report
        self.assertEqual(root["mode"], "presentation")
        self.assertEqual(root["bounds"], {"x": 10.0, "y": 20.0, "width": 170.0, "height": 50.0})
        self.assertEqual(root["box_count"], 2)
        self.assertEqual(root["boxes"][0]["label"], "Cutoff")
        self.assertEqual(root["boxes"][0]["label_source"], "parameter_longname")
        self.assertEqual(root["boxes"][1]["label"], "js preview.js & <tag>")
        self.assertEqual(root["overlap_count"], 1)
        self.assertEqual(root["overlaps"][0]["intersection"], {"x": 80.0, "y": 30.0, "width": 30.0, "height": 30.0})
        self.assertEqual(root["invalid_rects"], [{"index": 3, "id": "broken", "reason": "missing or invalid presentation_rect"}])
        self.assertEqual(nested["mode"], "patching")
        self.assertEqual(nested["box_count"], 2)
        self.assertEqual(nested["overlap_count"], 1)

    def test_svg_is_deterministic_and_escapes_source_labels(self):
        patchers = inspect_patch(patch_fixture())
        first = render_svg(patchers, "example.maxpat", "a" * 64)
        second = render_svg(patchers, "example.maxpat", "a" * 64)
        self.assertEqual(first, second)
        ET.fromstring(first)
        document = first.decode("utf-8")
        self.assertIn("js preview.js &amp; &lt;tag&gt;", document)
        self.assertNotIn("<script>", document)
        self.assertIn("static geometry schematic", document)

    def test_cli_exports_hashed_svg_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "fixture.maxpat"
            output = root / "fixture.svg"
            source.write_text(json.dumps(patch_fixture()), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["max", "preview", str(source), "--out", str(output)]), 0)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["source"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(result["svg"]["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            original = output.read_bytes()
            with self.assertRaises(FileExistsError):
                preview(source, output)
            self.assertEqual(output.read_bytes(), original)

    def test_inspection_without_output_writes_no_files(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "fixture.maxpat"
            source.write_text(json.dumps(patch_fixture()), encoding="utf-8")
            result = preview(source)
            self.assertNotIn("svg", result)
            self.assertEqual(list(Path(folder).iterdir()), [source])

    def test_nonfinite_derived_bounds_fail_closed(self):
        patch = {"patcher": {"boxes": [
            {"box": {"id": "left", "maxclass": "comment", "patching_rect": [-1e308, 0, 0, 1]}},
            {"box": {"id": "right", "maxclass": "comment", "patching_rect": [1e308, 0, 0, 1]}},
        ], "lines": []}}
        with self.assertRaisesRegex(ValueError, "exceed finite numeric range"):
            inspect_patch(patch)


if __name__ == "__main__":
    unittest.main()
