from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_devtools import quality


class QualityBootstrapTests(unittest.TestCase):
    def test_bootstrap_installs_pinned_tools_and_remove_reverses_only_its_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_pipx = bin_dir / "pipx"
            fake_pipx.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                f"b = pathlib.Path({str(bin_dir)!r})\n"
                "args = sys.argv[1:]\n"
                "if args[0] == 'install':\n"
                " suffix = next(a.split('=', 1)[1] for a in args if a.startswith('--suffix='))\n"
                " tool = 'ruff' if any(a.startswith('ruff==') for a in args) else 'lefthook'\n"
                " version = '0.17.3' if tool == 'ruff' else '2.2.2'\n"
                " name = f'{tool}{suffix}'\n"
                " body = f'#!/bin/sh\\nprintf \\\"{tool} {version}\\\\n\\\"\\n' if tool == 'ruff' else f'#!/bin/sh\\nprintf \\\"{version}\\\\n\\\"\\n'\n"
                " p = b / name; p.write_text(body); p.chmod(0o755)\n"
                "elif args[0] == 'uninstall':\n"
                " p = b / args[1]; p.unlink(missing_ok=True)\n"
            )
            fake_pipx.chmod(0o755)
            isolated_path = f"{bin_dir}:/usr/bin:/bin"
            with (
                patch.dict(
                    os.environ,
                    {"PATH": isolated_path, "PIPX_BIN_DIR": str(bin_dir)},
                ),
                patch.object(quality, "RECEIPT", root / "state/quality-tools.json"),
            ):
                self.assertEqual(quality.main("bootstrap"), 0)
                self.assertTrue((bin_dir / "lefthook").is_symlink())
                receipt = quality.RECEIPT.read_text()
                self.assertIn('"created_ruff": "true"', receipt)
                self.assertIn('"created_lefthook": "true"', receipt)
                self.assertEqual(quality.main("bootstrap"), 0)
                self.assertIn(
                    '"created_lefthook_alias": "true"', quality.RECEIPT.read_text()
                )
                self.assertEqual(quality.main("remove"), 0)
                self.assertFalse((bin_dir / "ruff-0.17.3").exists())
                self.assertFalse((bin_dir / "lefthook-2.2.2").exists())
                self.assertFalse((bin_dir / "lefthook").exists())

    def test_bootstrap_dry_run_does_not_write_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            with (
                patch.dict(
                    os.environ,
                    {"PIPX_BIN_DIR": str(bin_dir), "PATH": f"{bin_dir}:/usr/bin:/bin"},
                ),
                patch(
                    "codex_devtools.quality.shutil.which",
                    side_effect=lambda command: (
                        "/usr/bin/pipx" if command == "pipx" else None
                    ),
                ),
            ):
                self.assertEqual(quality.main("bootstrap", dry_run=True), 0)
                self.assertFalse(bin_dir.exists())

    def test_bootstrap_refuses_bin_directory_outside_path_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            with (
                patch.dict(
                    os.environ, {"PIPX_BIN_DIR": str(bin_dir), "PATH": "/usr/bin:/bin"}
                ),
                patch(
                    "codex_devtools.quality.shutil.which",
                    side_effect=lambda command: (
                        "/usr/bin/pipx" if command == "pipx" else None
                    ),
                ),
            ):
                self.assertEqual(quality.main("bootstrap"), 2)
                self.assertFalse(bin_dir.exists())


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=check
    )


@unittest.skipUnless(
    shutil.which("lefthook") and shutil.which("ruff-0.17.3"),
    "pinned Ruff and Lefthook are required",
)
class QualityHookIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._write_config()
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.name", "Quality Test")
        _git(self.root, "config", "user.email", "quality@example.invalid")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_config(self):
        (self.root / "ruff.toml").write_text(
            'target-version = "py310"\nline-length = 88\nunsafe-fixes = false\n'
        )
        (self.root / "lefthook.yml").write_text(
            "min_version: 2.2.2\n\n"
            "pre-commit:\n  parallel: true\n  commands:\n"
            '    ruff-check:\n      glob: "*.py"\n'
            "      run: ruff-0.17.3 check --force-exclude -- {staged_files}\n"
            '    ruff-format:\n      glob: "*.py"\n'
            "      run: ruff-0.17.3 format --check --force-exclude -- {staged_files}\n"
        )

    def _run_hook(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["lefthook", "run", "pre-commit"],
            cwd=self.root,
            check=False,
            text=True,
            capture_output=True,
        )

    def _reset_index(self):
        _git(self.root, "reset", "-q")

    def test_space_no_match_failure_and_partially_staged_files(self):
        spaced = self.root / "good file.py"
        spaced.write_text("value = 1\n")
        _git(self.root, "add", "--", spaced.name)
        result = self._run_hook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self._reset_index()
        (self.root / "notes.txt").write_text("not Python\n")
        _git(self.root, "add", "--", "notes.txt")
        result = self._run_hook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self._reset_index()
        broken = self.root / "broken.py"
        broken.write_text("print(missing_name)\n")
        _git(self.root, "add", "--", broken.name)
        index_before = _git(self.root, "show", ":broken.py").stdout
        worktree_before = broken.read_text()
        result = self._run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(_git(self.root, "show", ":broken.py").stdout, index_before)
        self.assertEqual(broken.read_text(), worktree_before)

        self._reset_index()
        partial = self.root / "partial.py"
        partial.write_text("value = 1\n")
        _git(self.root, "add", "--", partial.name)
        staged_before = _git(self.root, "show", ":partial.py").stdout
        partial.write_text("value=1\n")
        worktree_before = partial.read_text()
        result = self._run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(_git(self.root, "show", ":partial.py").stdout, staged_before)
        self.assertEqual(partial.read_text(), worktree_before)


if __name__ == "__main__":
    unittest.main()
