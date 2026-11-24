"""Exercise source manifests from both a clean checkout and a dev worktree."""

import subprocess
import sys
from pathlib import Path


def test_cli_manifest_ignores_worktree_metadata_and_local_setup(tmp_path):
    manifests = []
    for name in ("clean", "worktree"):
        root = tmp_path / name
        root.mkdir()
        (root / "source.txt").write_text("source\n", encoding="utf-8")
        if name == "worktree":
            (root / ".git").write_text(
                "gitdir: /path/to/worktrees/dev\n", encoding="utf-8"
            )
            (root / ".DS_Store").write_bytes(b"local Finder metadata")
            for directory in (".venv", "venv"):
                env = root / directory
                env.mkdir()
                (env / "pyvenv.cfg").write_text(
                    "home = /local/python\n", encoding="utf-8"
                )

        manifest = root / "manifest.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "codex_devtools",
                "hash",
                "manifest",
                str(root),
                "--out",
                str(manifest),
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
        )
        manifests.append(manifest.read_bytes())

    assert manifests[0] == manifests[1]
