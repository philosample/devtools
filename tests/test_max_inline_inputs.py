"""Static inline input preflight; never execute Max, JS or process queries."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from codex_devtools.cli import main
from codex_devtools.max_inline_inputs import inline_input_report
from codex_devtools.maxfiles import graph_report


def fixture(count=1, declared=1, ordinal=0):
    return {
        "patcher": {
            "boxes": [
                {
                    "box": {
                        "id": "control",
                        "maxclass": "newobj",
                        "text": "pcontrol",
                        "numinlets": 1,
                        "numoutlets": 1,
                    }
                },
                {
                    "box": {
                        "id": "child",
                        "maxclass": "newobj",
                        "text": "p child",
                        "numinlets": declared,
                        "numoutlets": 0,
                        "patcher": {
                            "boxes": [
                                {
                                    "box": {
                                        "id": f"input-{i}",
                                        "maxclass": "inlet",
                                        "numinlets": 0,
                                        "numoutlets": 1,
                                    }
                                }
                                for i in range(count)
                            ],
                            "lines": [],
                        },
                    }
                },
            ],
            "lines": [
                {
                    "patchline": {
                        "source": ["control", 0],
                        "destination": ["child", ordinal],
                    }
                }
            ],
        }
    }


def target(patch):
    return patch["patcher"]["boxes"][1]["box"]


class InlineInputTests(unittest.TestCase):
    def report(self, patch):
        before = copy.deepcopy(patch)
        result = inline_input_report(patch)
        self.assertEqual(patch, before, "preflight must not mutate source data")
        return result

    def test_actual_dedicated_inlets_and_target_ordinal(self):
        for count, ordinal in ((1, 0), (2, 0), (2, 1)):
            with self.subTest(count=count, ordinal=ordinal):
                report = self.report(fixture(count, count, ordinal))
                self.assertEqual(report["errors"], [])
                self.assertEqual(report["excluded"], [])
                self.assertEqual(report["checked"][0]["actual_numinlets"], count)
                self.assertEqual(report["checked"][0]["target_ordinal"], ordinal)
                self.assertEqual(
                    report["checked"][0]["inlet_box_ids"],
                    [f"input-{i}" for i in range(count)],
                )
        patch = fixture()
        target(patch)["text"] = "patcher child"
        self.assertEqual(self.report(patch)["errors"], [])
        target(patch)["patcher"]["boxes"][0]["box"]["outlettype"] = ["signal"]
        self.assertEqual(
            self.report(patch)["errors"], []
        )  # Presence, not type compatibility.

    def test_declared_only_missing_actual_inlet_and_wrong_ordinal(self):
        patch = fixture(count=0)
        self.assertEqual(graph_report(patch)["errors"], [])  # Historical blind spot.
        self.assertIn(
            "has no actual dedicated inlet", " ".join(self.report(patch)["errors"])
        )
        patch = fixture(count=1, declared=2, ordinal=1)
        self.assertEqual(graph_report(patch)["errors"], [])
        self.assertIn("ordinal 1 has no actual", " ".join(self.report(patch)["errors"]))

    def test_stale_declaration_and_strict_ordinals(self):
        for count, declared, ordinal in (
            (2, 1, 0),
            (1, 0, 0),
            (1, True, 0),
            (1, 1, True),
            (1, 1, -1),
        ):
            with self.subTest(count=count, declared=declared, ordinal=ordinal):
                self.assertTrue(
                    self.report(fixture(count, declared, ordinal))["errors"]
                )
        patch = fixture()
        patch["patcher"]["lines"][0]["patchline"]["source"][1] = 1
        self.assertIn("pcontrol output ordinal", " ".join(self.report(patch)["errors"]))

    def test_nested_patchers_are_walked_but_nested_inlets_do_not_count_for_parent(self):
        inner = fixture()["patcher"]
        patch = fixture(count=0)
        target(patch)["patcher"]["boxes"].append(
            {
                "box": {
                    "id": "nested",
                    "maxclass": "newobj",
                    "text": "p nested",
                    "numinlets": 0,
                    "numoutlets": 0,
                    "patcher": inner,
                }
            }
        )
        report = self.report(patch)
        self.assertEqual(
            [r["path"] for r in report["checked"]], ["root", "root/p child/p nested"]
        )
        self.assertEqual([r["actual_numinlets"] for r in report["checked"]], [0, 1])
        self.assertEqual(len(report["errors"]), 2)

    def test_unsupported_alternate_and_signal_input_forms_fail_explicitly(self):
        for maxclass, text in (
            ("newobj", "inlet"),
            ("newobj", "inlet @comment x"),
            ("newobj", "inlet~"),
            ("newobj", "mc.in~ 1"),
            ("inlet~", ""),
            ("in~", ""),
            ("mc.in~", ""),
        ):
            with self.subTest(maxclass=maxclass, text=text):
                patch = fixture()
                target(patch)["patcher"]["boxes"].append(
                    {"box": {"id": "ambiguous", "maxclass": maxclass, "text": text}}
                )
                report = self.report(patch)
                self.assertIsNone(report["checked"][0]["actual_numinlets"])
                self.assertIn(
                    "unsupported inlet representation", " ".join(report["errors"])
                )

    def test_missing_malformed_and_non_box_child_interfaces_fail_explicitly(self):
        for child in (
            None,
            {},
            {"boxes": None},
            {"classnamespace": "dsp.gen", "boxes": []},
            {"boxes": [None]},
            {"boxes": [{"box": {"id": "missing-class"}}]},
        ):
            with self.subTest(child=child):
                patch = fixture()
                target(patch)["patcher"] = child
                self.assertTrue(self.report(patch)["errors"])
        for identity in (None, [], "", "input-0"):
            patch = fixture()
            target(patch)["patcher"]["boxes"].append(
                {"box": {"id": identity, "maxclass": "inlet"}}
            )
            self.assertIn("inlet IDs", " ".join(self.report(patch)["errors"]))

    def test_bpatcher_external_and_dynamic_js_are_reported_outside_scope(self):
        for maxclass, text in (
            ("bpatcher", ""),
            ("newobj", "external_module"),
            ("newobj", "js dynamic.js"),
        ):
            with self.subTest(maxclass=maxclass, text=text):
                patch = fixture()
                target(patch).update(maxclass=maxclass, text=text)
                report = self.report(patch)
                self.assertEqual(report["errors"], [])
                self.assertEqual(report["checked"], [])
                self.assertIn("outside", report["excluded"][0]["reason"])
        patch = fixture()
        patch["patcher"]["boxes"][0]["box"]["text"] = "some_other_object"
        self.assertEqual(
            self.report(patch), {"checked": [], "excluded": [], "errors": []}
        )

    def test_cli_default_json_and_text_report_parity_no_source_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.maxpat"
            patch = fixture(count=0)
            raw = json.dumps(patch).encode()
            path.write_bytes(raw)
            graph = graph_report(patch)
            expected = {"path": str(path), "format": "json", "entry_count": 0, **graph}
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["max", "validate", str(path), "--json"]), 0)
            self.assertEqual(
                out.getvalue(),
                json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["max", "validate", str(path)]), 0)
            self.assertEqual(out.getvalue(), "json: 2 patchers, 2 boxes, 1 lines\nOK\n")
            self.assertEqual(path.read_bytes(), raw)

    def test_cli_opt_in_exit_codes_and_additive_json_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.maxpat"
            for count, expected_code in ((0, 1), (1, 0)):
                path.write_text(json.dumps(fixture(count=count)))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(
                        main(
                            [
                                "max",
                                "validate",
                                str(path),
                                "--native-inline-inputs",
                                "--json",
                            ]
                        ),
                        expected_code,
                    )
                report = json.loads(out.getvalue())
                self.assertEqual(
                    report["native_inline_inputs"]["checked"][0]["actual_numinlets"],
                    count,
                )
                self.assertEqual(
                    report["errors"], report["native_inline_inputs"]["errors"]
                )
                text = io.StringIO()
                with contextlib.redirect_stdout(text):
                    self.assertEqual(
                        main(["max", "validate", str(path), "--native-inline-inputs"]),
                        expected_code,
                    )
                self.assertIn(
                    "native inline inputs: 1 checked cords, 0 excluded cords",
                    text.getvalue(),
                )
                self.assertEqual(text.getvalue().endswith("OK\n"), expected_code == 0)
            path.write_bytes(b"not JSON")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    main(["max", "validate", str(path), "--native-inline-inputs"]), 2
                )


if __name__ == "__main__":
    unittest.main()
