import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_devtools import ax_snapshot
from codex_devtools.cli import main


def native_result(pid: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        [],
        0,
        json.dumps(
            {
                "format": "codex-devtools-ax-snapshot-v1",
                "status": "observed",
                "target": {"pid": pid},
            }
        ),
        "",
    )


class AxSnapshotTests(unittest.TestCase):
    def test_no_value_from_children_attribute_is_classified_as_leaf(self):
        source = (
            Path(ax_snapshot.__file__)
            .with_name("ax_snapshot.swift")
            .read_text(encoding="utf-8")
        )
        self.assertIn(
            "func childrenErrorIndicatesLeaf(_ error: Int32?) -> Bool {\n"
            "    error == AXError.noValue.rawValue\n"
            "}",
            source,
        )
        self.assertIn(
            "childrenError != nil && !childrenErrorIndicatesLeaf(childrenError)",
            source,
        )

    def test_cli_serializes_native_snapshot(self):
        expected = {
            "format": "codex-devtools-ax-snapshot-v1",
            "status": "observed",
            "target": {"pid": 84769},
        }
        output = io.StringIO()
        with (
            patch("codex_devtools.ax_snapshot.snapshot", return_value=expected) as read,
            redirect_stdout(output),
        ):
            self.assertEqual(
                main(
                    [
                        "ax",
                        "snapshot",
                        "--pid",
                        "84769",
                        "--window-title",
                        "Settings",
                    ]
                ),
                0,
            )
        self.assertEqual(json.loads(output.getvalue()), expected)
        read.assert_called_once_with(
            84769,
            window_title="Settings",
            max_elements=500,
            max_depth=10,
        )

    def test_exact_pid_and_bounds_are_passed_to_native_reader(self):
        with (
            patch.object(ax_snapshot.platform, "system", return_value="Darwin"),
            patch.object(
                ax_snapshot.subprocess, "run", return_value=native_result(84769)
            ) as run,
        ):
            result = ax_snapshot.snapshot(
                84769,
                window_title="Settings",
                max_elements=120,
                max_depth=6,
            )
        self.assertEqual(result["target"]["pid"], 84769)
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], "/usr/bin/swift")
        self.assertIn("ax_snapshot.swift", args[0][1])
        self.assertEqual(
            args[0][2:],
            [
                "--pid",
                "84769",
                "--max-elements",
                "120",
                "--max-depth",
                "6",
                "--window-title",
                "Settings",
            ],
        )
        self.assertEqual(
            kwargs,
            {"check": False, "capture_output": True, "text": True, "timeout": 30},
        )

    def test_rejects_invalid_pid_or_bounds_before_native_call(self):
        with (
            patch.object(ax_snapshot.platform, "system", return_value="Darwin"),
            patch.object(ax_snapshot.subprocess, "run") as run,
        ):
            for arguments in (
                {"pid": 0},
                {"pid": 2_147_483_648},
                {"pid": 123, "max_elements": 0},
                {"pid": 123, "max_elements": 2049},
                {"pid": 123, "max_depth": -1},
                {"pid": 123, "max_depth": 33},
                {"pid": 123, "window_title": ""},
            ):
                with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                    ax_snapshot.snapshot(**arguments)
        run.assert_not_called()

    def test_non_macos_is_rejected_without_launching_native_reader(self):
        with (
            patch.object(ax_snapshot.platform, "system", return_value="Linux"),
            patch.object(ax_snapshot.subprocess, "run") as run,
            self.assertRaisesRegex(ValueError, "require macOS"),
        ):
            ax_snapshot.snapshot(123)
        run.assert_not_called()

    def test_rejects_native_pid_mismatch_and_invalid_json(self):
        for stdout in (
            json.dumps(
                {
                    "format": "codex-devtools-ax-snapshot-v1",
                    "status": "observed",
                    "target": {"pid": 321},
                }
            ),
            "not json",
        ):
            with (
                patch.object(ax_snapshot.platform, "system", return_value="Darwin"),
                patch.object(
                    ax_snapshot.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout, ""),
                ),
                self.assertRaises(ValueError),
            ):
                ax_snapshot.snapshot(123)


if __name__ == "__main__":
    unittest.main()
