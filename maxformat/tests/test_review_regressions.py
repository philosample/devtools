import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from maxlayout import cli, render, route
from maxlayout.maxpat import Patcher

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
        box = doc["patcher"]["boxes"][0]["box"]
        old_id = box["id"]
        box["id"] = "named-anchor"
        for entry in doc["patcher"]["lines"]:
            for end in ("source", "destination"):
                endpoint = entry["patchline"][end]
                if endpoint[0] == old_id:
                    endpoint[0] = box["id"]
        self.source.write_text(json.dumps(doc), encoding="utf-8")
        review = self.format_patch(self.source, "--check")
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertIn("named-anchor", review.stderr)
        with patch.object(cli.route, "route_patcher") as router:
            result = cli.main([str(self.source), str(self.output), "--route-only"])
        self.assertEqual(result, 1)
        router.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_missing_box_in_nested_patchline_refuses_check_and_rewrite(self):
        doc = messy()
        parent = next(
            entry["box"]
            for entry in doc["patcher"]["boxes"]
            if "patcher" in entry["box"]
        )
        line = parent["patcher"]["lines"][0]["patchline"]
        line["destination"][0] = "obj-999999"
        self.source.write_text(json.dumps(doc), encoding="utf-8")
        for args in (("--check",), (self.output, "--route-only")):
            with self.subTest(args=args):
                result = self.format_patch(self.source, *args)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"root/{parent['id']}:", result.stderr)
                self.assertIn("missing destination 'obj-999999'", result.stderr)
                self.assertFalse(self.output.exists())

    def test_route_only_reports_a_cord_with_no_path(self):
        doc = messy()
        root = doc["patcher"]
        boxes = [
            ("obj-1", [225, 20, 30, 22]),
            ("obj-2", [60, 130, 30, 22]),
            ("obj-3", [0, 0, 150, 220]),
        ]
        root["boxes"] = [
            {
                "box": {
                    "id": box_id,
                    "maxclass": "newobj",
                    "text": "int",
                    "numinlets": 2,
                    "numoutlets": 1,
                    "patching_rect": rect,
                }
            }
            for box_id, rect in boxes
        ]
        root["lines"] = [
            {"patchline": {"source": ["obj-1", 0], "destination": ["obj-2", 0]}}
        ]
        self.source.write_text(json.dumps(doc), encoding="utf-8")
        result = self.format_patch(self.source, self.output, "--route-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "incomplete routing: root: 1 cord could not be routed", result.stderr
        )
        candidate = json.loads(self.output.read_bytes())["patcher"]
        self.assertEqual(candidate["boxes"], root["boxes"])
        self.assertEqual(candidate["lines"], root["lines"])

    def test_sealed_cord_endpoints_bypass_the_route_search(self):
        router = route.Router(Patcher(messy()["patcher"]))
        router.xs = router.ys = list(range(7))
        start = (1, 1)
        goal = (4, 4)
        for blocked in (
            {(0, 1), (2, 1), (1, 2)},
            {(3, 4), (5, 4), (4, 3), (4, 5)},
        ):
            with self.subTest(blocked=blocked):
                router.blocked = blocked
                with patch.object(
                    route.heapq, "heappop", wraps=route.heapq.heappop
                ) as pop:
                    self.assertIsNone(
                        router.search(start, goal, (("obj-1", 0), ("obj-2", 0)))
                    )
                pop.assert_not_called()

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
