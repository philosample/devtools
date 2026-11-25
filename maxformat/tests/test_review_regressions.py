import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from maxlayout import cli, render

from tests.helpers import MESSY, ROOT, messy


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)
        self.source = self.directory / "source.maxpat"
        self.source.write_bytes(Path(MESSY).read_bytes())
        self.output = self.directory / "candidate.maxpat"

    def format_patch(self, *args):
        return subprocess.run(
            [sys.executable, str(Path(ROOT) / "maxformat.py"), *map(str, args)],
            capture_output=True,
            text=True,
            check=False,
        )

    def preview(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "maxlayout.render", *map(str, args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_rejects_source_as_output_even_through_a_file_alias(self):
        alias = self.directory / "alias.maxpat"
        os.link(self.source, alias)
        before = self.source.read_bytes()
        symlink = self.directory / "symlink.maxpat"
        symlink.symlink_to(self.source)
        for output in (self.source, alias, symlink):
            with self.subTest(output=output):
                result = self.format_patch(self.source, output, "--route-only")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("input file", result.stderr)
                self.assertEqual(self.source.read_bytes(), before)

    def test_reports_existing_named_ids_and_skips_routing_on_refusal(self):
        doc = messy()
        doc["patcher"]["boxes"][0]["box"]["id"] = "named-anchor"
        self.source.write_text(json.dumps(doc), encoding="utf-8")
        review = self.format_patch(self.source, "--check")
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertIn("named-anchor", review.stderr)
        with patch.object(cli.route, "route_patcher") as router:
            result = cli.main([str(self.source), str(self.output), "--route-only"])
        self.assertEqual(result, 1)
        router.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_unknown_subpatcher_writes_no_preview(self):
        output = self.directory / "preview.svg"
        result = self.preview(self.source, "--subpatcher", "missing", "-o", output)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("missing", result.stderr)
        self.assertFalse(output.exists())

    def test_ambiguous_subpatcher_requires_full_path(self):
        doc = messy()
        nested = doc["patcher"]["boxes"]
        parent = next(entry["box"] for entry in nested if "patcher" in entry["box"])
        sibling = copy.deepcopy(parent)
        sibling["id"] = "obj-991"
        nested.append({"box": sibling})
        for box in (parent, sibling):
            child = copy.deepcopy({"id": "obj-992", "patcher": box["patcher"]})
            box["patcher"]["boxes"].append({"box": child})
        self.source.write_text(json.dumps(doc), encoding="utf-8")
        output = self.directory / "preview.svg"
        result = self.preview(self.source, "--subpatcher", "obj-992", "-o", output)
        selected = f"root/{parent['id']}:{parent['text']}/obj-992:newobj"
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn(selected, result.stderr)
        self.assertFalse(output.exists())
        result = self.preview(self.source, "--subpatcher", selected, "-o", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())

    def test_preview_protects_the_source(self):
        source = self.source.read_bytes()
        result = self.preview(self.source, "-o", self.source)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.source.read_bytes(), source)

    def test_timed_out_png_conversion_removes_scratch_directory(self):
        scratch = []

        def converter(args, **kwargs):
            scratch.append(Path(args[args.index("-o") + 1]))
            raise subprocess.TimeoutExpired(args, 20)

        with (
            patch.object(render.shutil, "which", return_value="/usr/bin/qlmanage"),
            patch.object(render.subprocess, "run", side_effect=converter),
        ):
            self.assertFalse(render.to_png(self.source, self.output))
        self.assertEqual(len(scratch), 1)
        self.assertFalse(scratch[0].exists())

    def test_successful_png_conversion_preserves_existing_svg(self):
        svg, png = cli.preview_paths(self.output)
        svg.write_text("existing preview", encoding="utf-8")

        def converter(temporary_svg, output):
            self.assertNotEqual(svg, temporary_svg)
            output.write_bytes(b"png")
            return True

        with patch.object(cli.render, "to_png", side_effect=converter):
            cli.save_png(messy(), self.output)
        self.assertEqual(svg.read_text(encoding="utf-8"), "existing preview")
        self.assertEqual(png.read_bytes(), b"png")
