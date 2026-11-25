from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_devtools import quality


class QualityBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.machine_paths = [
            Path.home() / ".local/bin/ruff",
            Path.home() / ".local/bin/ruff-0.17.3",
            Path.home() / ".local/bin/lefthook",
            Path.home() / ".local/bin/lefthook-2.2.2",
            quality.RECEIPT,
        ]
        cls.machine_state_before = {
            path: cls._signature(path) for path in cls.machine_paths
        }

    @staticmethod
    def _signature(path: Path) -> tuple[str, str | None, str | None]:
        if path.is_symlink():
            target = path.resolve(strict=False)
            digest = (
                hashlib.sha256(target.read_bytes()).hexdigest()
                if target.is_file()
                else None
            )
            return "symlink", os.readlink(path), digest
        if path.is_file():
            return "file", None, hashlib.sha256(path.read_bytes()).hexdigest()
        return "missing", None, None

    @classmethod
    def tearDownClass(cls):
        machine_state_after = {path: cls._signature(path) for path in cls.machine_paths}
        if machine_state_after != cls.machine_state_before:
            raise AssertionError(
                "quality bootstrap tests changed real installed tools or receipt: "
                f"before={cls.machine_state_before!r}, after={machine_state_after!r}"
            )

    def _fixture(self, root: Path) -> tuple[Path, Path]:
        bin_dir = root / "bin"
        pipx_home = root / "pipx-home"
        bin_dir.mkdir()
        fake_pipx = bin_dir / "pipx"
        fake_pipx.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, shutil, sys\n"
            "b = pathlib.Path(os.environ['PIPX_BIN_DIR'])\n"
            "home = pathlib.Path(os.environ['PIPX_HOME'])\n"
            "venvs = home / 'venvs'\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['environment']:\n"
            " print(os.environ['PIPX_HOME'])\n"
            "elif args[:2] == ['list', '--json']:\n"
            " names = [p.name for p in venvs.iterdir()] if venvs.exists() else []\n"
            " print(json.dumps({'venvs': {name: {} for name in names}}))\n"
            "elif args[0] == 'install':\n"
            " suffix = next(a.split('=', 1)[1] for a in args if a.startswith('--suffix='))\n"
            " tool = 'ruff' if any(a.startswith('ruff==') for a in args) else 'lefthook'\n"
            " version = '0.17.3' if tool == 'ruff' else '2.2.2'\n"
            " name = f'{tool}{suffix}'\n"
            " venv = venvs / name.replace('.', '-')\n"
            " venv.mkdir(parents=True, exist_ok=True)\n"
            " if tool == 'lefthook' and os.getenv('FAKE_LEFTHOOK_FAIL') == 'after-venv':\n"
            "  sys.exit(7)\n"
            " body = f'#!/bin/sh\\nprintf \\\"{tool} {version}\\\\n\\\"\\n' if tool == 'ruff' else f'#!/bin/sh\\nprintf \\\"{version}\\\\n\\\"\\n'\n"
            " p = b / name; p.write_text(body); p.chmod(0o755)\n"
            "elif args[0] == 'uninstall':\n"
            " package = args[1]\n"
            " shutil.rmtree(venvs / package.replace('.', '-'), ignore_errors=True)\n"
            " (b / package).unlink(missing_ok=True)\n"
        )
        fake_pipx.chmod(0o755)
        return bin_dir, pipx_home

    def _environment(
        self, root: Path, bin_dir: Path, pipx_home: Path
    ) -> dict[str, str]:
        return {
            "HOME": str(root / "home"),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PIPX_BIN_DIR": str(bin_dir),
            "PIPX_HOME": str(pipx_home),
        }

    def test_bootstrap_installs_pinned_tools_and_remove_uses_recorded_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir, pipx_home = self._fixture(root)
            other_bin = root / "other-bin"
            other_bin.mkdir()
            sentinel = other_bin / "lefthook-2.2.2"
            sentinel.write_text("#!/bin/sh\nprintf '2.2.2\\n'\n")
            sentinel.chmod(0o755)
            other_alias = other_bin / "lefthook"
            other_alias.symlink_to(sentinel)
            with (
                patch.dict(os.environ, self._environment(root, bin_dir, pipx_home)),
                patch.object(quality, "RECEIPT", root / "state/quality-tools.json"),
            ):
                self.assertEqual(quality.main("bootstrap"), 0)
                self.assertTrue((bin_dir / "lefthook").is_symlink())
                receipt = json.loads(quality.RECEIPT.read_text())
                self.assertEqual(receipt["pipx_bin_dir"], str(bin_dir.resolve()))
                self.assertEqual(receipt["pipx_home"], str(pipx_home.resolve()))
                self.assertEqual(
                    receipt["ruff_verified_path"],
                    str((bin_dir / "ruff-0.17.3").resolve()),
                )
                self.assertTrue(receipt["created_ruff"] == "true")
                self.assertTrue(receipt["created_lefthook"] == "true")
                self.assertEqual(quality.main("bootstrap"), 0)
                with patch.dict(os.environ, {"PIPX_BIN_DIR": str(other_bin)}):
                    self.assertEqual(quality.main("remove"), 2)
                    self.assertTrue(quality.RECEIPT.is_file())
                    self.assertTrue(other_alias.is_symlink())
                    self.assertTrue((bin_dir / "lefthook").is_symlink())
                self.assertEqual(quality.main("remove"), 0)
                self.assertFalse((bin_dir / "ruff-0.17.3").exists())
                self.assertFalse((bin_dir / "lefthook-2.2.2").exists())
                self.assertFalse((bin_dir / "lefthook").exists())
                self.assertTrue(other_alias.is_symlink())

    def test_bootstrap_requires_bin_path_even_with_compatible_lefthook(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "seed").mkdir()
            seed_bin, _ = self._fixture(root / "seed")
            path_dir = root / "path"
            path_dir.mkdir()
            shutil.copy2(seed_bin / "pipx", path_dir / "pipx")
            external_lefthook = path_dir / "lefthook"
            external_lefthook.write_text("#!/usr/bin/env python3\nprint('2.2.2')\n")
            external_lefthook.chmod(0o755)
            bin_dir = root / "pipx-bin"
            pipx_home = root / "pipx-home"
            receipt = root / "state/quality-tools.json"
            env = {
                "HOME": str(root / "home"),
                "PATH": f"{path_dir}:/usr/bin:/bin",
                "PIPX_BIN_DIR": str(bin_dir),
                "PIPX_HOME": str(pipx_home),
            }
            with (
                patch.dict(os.environ, env),
                patch.object(quality, "RECEIPT", receipt),
            ):
                self.assertEqual(quality.main("bootstrap"), 2)
                self.assertFalse(bin_dir.exists())
                self.assertFalse(pipx_home.exists())
                self.assertFalse(receipt.exists())
                self.assertTrue(external_lefthook.is_file())

    def test_doctor_rejects_wrong_ruff_shadowing_pinned_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir, pipx_home = self._fixture(root)
            shadow_dir = root / "shadow"
            shadow_dir.mkdir()
            shadowed_ruff = shadow_dir / "ruff-0.17.3"
            shadowed_ruff.write_text("#!/usr/bin/env python3\nprint('ruff 0.99.0')\n")
            shadowed_ruff.chmod(0o755)
            env = self._environment(root, bin_dir, pipx_home)
            env["PATH"] = f"{shadow_dir}:{env['PATH']}"
            with (
                patch.dict(os.environ, env),
                patch.object(quality, "RECEIPT", root / "state/quality-tools.json"),
            ):
                self.assertEqual(quality.main("bootstrap"), 2)
                self.assertTrue((bin_dir / "ruff-0.17.3").is_file())
                self.assertTrue(quality.RECEIPT.is_file())
                with self.assertRaisesRegex(RuntimeError, "must resolve on PATH"):
                    quality.doctor()
                self.assertEqual(
                    shadowed_ruff.read_text().splitlines()[-1], "print('ruff 0.99.0')"
                )
                self.assertEqual(quality.main("remove"), 0)
                self.assertTrue(shadowed_ruff.is_file())
                self.assertFalse(quality.RECEIPT.exists())

    def test_partial_pipx_failure_keeps_receipt_and_can_be_rolled_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir, pipx_home = self._fixture(root)
            env = self._environment(root, bin_dir, pipx_home)
            env["FAKE_LEFTHOOK_FAIL"] = "after-venv"
            with (
                patch.dict(os.environ, env),
                patch.object(quality, "RECEIPT", root / "state/quality-tools.json"),
            ):
                self.assertEqual(quality.main("bootstrap"), 2)
                receipt = json.loads(quality.RECEIPT.read_text())
                self.assertEqual(receipt["created_ruff"], "true")
                self.assertEqual(receipt["created_lefthook"], "true")
                self.assertEqual(quality.main("remove"), 0)
                self.assertFalse((pipx_home / "venvs/ruff-0-17-3").exists())
                self.assertFalse((pipx_home / "venvs/lefthook-2-2-2").exists())

    def test_doctor_failure_keeps_receipt_and_can_be_rolled_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir, pipx_home = self._fixture(root)
            with (
                patch.dict(os.environ, self._environment(root, bin_dir, pipx_home)),
                patch.object(quality, "RECEIPT", root / "state/quality-tools.json"),
            ):
                with patch.object(
                    quality, "doctor", side_effect=RuntimeError("doctor failed")
                ):
                    self.assertEqual(quality.main("bootstrap"), 2)
                receipt = json.loads(quality.RECEIPT.read_text())
                self.assertEqual(receipt["created_ruff"], "true")
                self.assertEqual(receipt["created_lefthook"], "true")
                self.assertEqual(receipt["created_lefthook_alias"], "true")
                self.assertEqual(quality.main("remove"), 0)

    def test_bootstrap_dry_run_does_not_write_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            with (
                patch.dict(
                    os.environ,
                    {
                        "HOME": str(Path(tmp) / "home"),
                        "PIPX_BIN_DIR": str(bin_dir),
                        "PATH": f"{bin_dir}:/usr/bin:/bin",
                    },
                ),
                patch(
                    "codex_devtools.quality.shutil.which",
                    side_effect=lambda command: (
                        "/usr/bin/pipx" if command == "pipx" else None
                    ),
                ),
                patch.object(
                    quality, "_pipx_home", return_value=Path(tmp) / "pipx-home"
                ),
                patch.object(
                    quality, "RECEIPT", Path(tmp) / "state/quality-tools.json"
                ),
            ):
                self.assertEqual(quality.main("bootstrap", dry_run=True), 0)
                self.assertFalse(bin_dir.exists())

    def test_bootstrap_refuses_bin_directory_outside_path_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            with (
                patch.dict(
                    os.environ,
                    {
                        "HOME": str(Path(tmp) / "home"),
                        "PIPX_BIN_DIR": str(bin_dir),
                        "PATH": "/usr/bin:/bin",
                    },
                ),
                patch(
                    "codex_devtools.quality.shutil.which",
                    side_effect=lambda command: (
                        "/usr/bin/pipx" if command == "pipx" else None
                    ),
                ),
                patch.object(
                    quality, "RECEIPT", Path(tmp) / "state/quality-tools.json"
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
    os.environ.get("DEVTOOLS_REQUIRE_HOOK_TESTS") == "1"
    or (shutil.which("lefthook") and shutil.which("ruff-0.17.3")),
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

    def test_pre_push_drops_inherited_git_context_before_running_pytest(self):
        config = Path(__file__).resolve().parents[1] / "lefthook.yml"
        shutil.copyfile(config, self.root / config.name)
        _git(self.root, "add", config.name)
        _git(
            self.root, "-c", "core.hooksPath=/dev/null", "commit", "-m", "hook fixture"
        )
        python = self.root / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(
            "#!/bin/sh\n"
            'test "$*" = "-m pytest -q" &&\n'
            'test -z "${GIT_DIR+x}${GIT_WORK_TREE+x}${GIT_INDEX_FILE+x}" ||\n'
            '{ echo "Unexpected inherited Git context" >&2; exit 1; }\n'
            ": > pytest-ran\n"
        )
        python.chmod(0o755)
        env = {
            **os.environ,
            "GIT_DIR": str(self.root / ".git"),
            "GIT_WORK_TREE": str(self.root),
            "GIT_INDEX_FILE": str(self.root / ".git" / "index"),
        }
        result = subprocess.run(
            ["lefthook", "run", "pre-push", "--no-auto-install"],
            cwd=self.root,
            env=env,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            (self.root / "pytest-ran").is_file(), "Lefthook did not invoke pytest"
        )

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
