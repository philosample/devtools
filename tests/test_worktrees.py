"""Worktree ownership and guarded cleanup regressions."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from codex_devtools import worktrees
from codex_devtools.cli import main


def git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


class WorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "source repo"
        self.repo.mkdir()
        git("init", "-b", "main", cwd=self.repo)
        git("config", "user.email", "test@example.invalid", cwd=self.repo)
        git("config", "user.name", "Test", cwd=self.repo)
        (self.repo / "README.md").write_text("fixture\n")
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        git("add", "README.md", ".gitignore", cwd=self.repo)
        git("commit", "-m", "initial", cwd=self.repo)
        git("remote", "add", "origin", "git@github.com:example/repo.git", cwd=self.repo)
        self.worktree = self.root / "owned worktree"
        self.registry = self.root / "state" / "worktrees.json"
        env = patch.dict(
            os.environ, {"CODEX_DEVTOOLS_WORKTREE_REGISTRY": str(self.registry)}
        )
        env.start()
        self.addCleanup(env.stop)

    def start(self) -> dict:
        return worktrees.start(
            self.repo,
            self.worktree,
            "codex/fixture",
            "main",
            "chat-1",
            "STORY-1",
            "issue #1",
        )

    def test_start_update_release_and_audit(self) -> None:
        entry = self.start()
        self.assertEqual(entry["worktree"], str(self.worktree.resolve()))
        self.assertEqual(entry["branch"], "codex/fixture")
        self.assertFalse(entry["released"])
        updated = worktrees.update(
            self.worktree, "chat-1", pr="12", plan="issue #1 / PR #12"
        )
        self.assertEqual(updated["pr"], "12")
        self.assertTrue(worktrees.audit()[0]["clean"])
        self.assertTrue(worktrees.release(self.worktree, "chat-1")["released"])
        with self.assertRaisesRegex(ValueError, "released"):
            worktrees.update(self.worktree, "chat-1", pr="13")

    def test_wrong_owner_and_changed_head_refuse_release(self) -> None:
        self.start()
        worktrees.update(self.worktree, "chat-1", pr="12")
        with self.assertRaisesRegex(ValueError, "owner chat"):
            worktrees.release(self.worktree, "chat-2")
        (self.worktree / "new.txt").write_text("new\n")
        git("add", "new.txt", cwd=self.worktree)
        git("commit", "-m", "advance", cwd=self.worktree)
        with self.assertRaisesRegex(ValueError, "changed"):
            worktrees.release(self.worktree, "chat-1")
        worktrees.update(self.worktree, "chat-1")
        self.assertTrue(worktrees.release(self.worktree, "chat-1")["released"])

    def test_malformed_registry_is_preserved(self) -> None:
        self.registry.parent.mkdir()
        self.registry.write_text("{broken")
        original = self.registry.read_bytes()
        with self.assertRaisesRegex(ValueError, "invalid worktree registry"):
            worktrees.audit()
        self.assertEqual(self.registry.read_bytes(), original)

    def test_primary_checkout_cannot_be_registered(self) -> None:
        with self.assertRaisesRegex(ValueError, "primary repository checkout"):
            worktrees.register(self.repo, self.repo, "chat-1", "STORY-1", "issue #1")
        self.assertFalse(self.registry.exists())

    def test_cli_audit_returns_json(self) -> None:
        self.start()
        output = StringIO()
        with redirect_stdout(output):
            code = main(["worktree", "audit"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())[0]["story"], "STORY-1")

    def test_missing_worktree_audit_does_not_remove_record(self) -> None:
        self.start()
        git("worktree", "remove", str(self.worktree), cwd=self.repo)
        audit = worktrees.audit()
        self.assertFalse(audit[0]["present"])
        self.assertEqual(len(json.loads(self.registry.read_text())["entries"]), 1)

    def test_cleanup_requires_exact_merged_head_and_clean_tree(self) -> None:
        entry = self.start()
        worktrees.update(self.worktree, "chat-1", pr="12")
        with self.assertRaisesRegex(ValueError, "not released"):
            worktrees.cleanup(self.worktree, "chat-1")
        worktrees.release(self.worktree, "chat-1")
        real_run = worktrees._run

        def gh_result(*args: str, cwd=None) -> str:
            if args[0] == "gh":
                return json.dumps({"state": "MERGED", "headRefOid": entry["head"]})
            return real_run(*args, cwd=cwd)

        with patch.object(worktrees, "_run", side_effect=gh_result):
            (self.worktree / "untracked.txt").write_text("keep")
            with self.assertRaisesRegex(ValueError, "changes"):
                worktrees.cleanup(self.worktree, "chat-1")
            (self.worktree / "untracked.txt").unlink()
            (self.worktree / "ignored.txt").write_text("keep")
            with self.assertRaisesRegex(ValueError, "changes"):
                worktrees.cleanup(self.worktree, "chat-1")
            (self.worktree / "ignored.txt").unlink()
            with (
                patch.object(
                    worktrees,
                    "_run",
                    side_effect=lambda *args, cwd=None: (
                        json.dumps({"state": "MERGED", "headRefOid": "0" * 40})
                        if args[0] == "gh"
                        else real_run(*args, cwd=cwd)
                    ),
                ),
                self.assertRaisesRegex(ValueError, "exact worktree head"),
            ):
                worktrees.cleanup(self.worktree, "chat-1")
            result = worktrees.cleanup(self.worktree, "chat-1")
        self.assertEqual(result["merged_pr"], "12")
        self.assertFalse(self.worktree.exists())
        self.assertEqual(worktrees.audit(), [])
        self.assertNotIn("codex/fixture", git("branch", "--list", cwd=self.repo))


if __name__ == "__main__":
    unittest.main()
