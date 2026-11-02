"""Local ownership records and guarded cleanup for Git worktrees."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

FORMAT = "codex-devtools-worktrees-v1"


def registry_path() -> Path:
    override = os.environ.get("CODEX_DEVTOOLS_WORKTREE_REGISTRY")
    if override:
        return Path(override).expanduser().resolve()
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state / "codex-devtools" / "worktrees.json"


def _run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        raise ValueError(
            f"{' '.join(args[:3])} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _read(path: Path) -> dict:
    if not path.exists():
        return {"format": FORMAT, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"invalid worktree registry: {path}") from exc
    if (
        not isinstance(data, dict)
        or data.get("format") != FORMAT
        or not isinstance(data.get("entries"), dict)
    ):
        raise ValueError(f"invalid worktree registry schema: {path}")
    for key, entry in data["entries"].items():
        if (
            not isinstance(key, str)
            or not isinstance(entry, dict)
            or entry.get("worktree") != key
        ):
            raise ValueError(f"invalid worktree registry entry: {key}")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".worktrees-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.with_suffix(path.suffix + ".lock")).open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _worktrees(repository: Path) -> dict[str, dict]:
    raw = _run("git", "-C", str(repository), "worktree", "list", "--porcelain")
    found = {}
    for index, block in enumerate(raw.split("\n\n")):
        fields = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value
        if "worktree" in fields:
            fields["primary"] = index == 0
            found[str(Path(fields["worktree"]).resolve())] = fields
    return found


def _identity(repository: Path, worktree: Path) -> tuple[str, str]:
    found = _worktrees(repository).get(str(worktree))
    if not found or "prunable" in found or "locked" in found:
        raise ValueError(
            f"worktree is not available and unlocked in repository: {worktree}"
        )
    if found["primary"]:
        raise ValueError("primary repository checkout cannot be registered")
    branch = found.get("branch", "")
    if not branch.startswith("refs/heads/"):
        raise ValueError("ownership registry requires a named local branch")
    head = _run("git", "-C", str(worktree), "rev-parse", "HEAD")
    return branch.removeprefix("refs/heads/"), head


def _entry(data: dict, worktree: Path, owner_chat: str) -> dict:
    entry = data["entries"].get(str(worktree))
    if not entry:
        raise ValueError(f"worktree is not registered: {worktree}")
    if entry["owner_chat"] != owner_chat:
        raise ValueError("owner chat does not match registry")
    return entry


def register(
    repository: Path,
    worktree: Path,
    owner_chat: str,
    story: str,
    plan: str,
    pr: str = "",
) -> dict:
    repository, worktree = repository.resolve(), worktree.resolve()
    if not owner_chat.strip() or not story.strip() or not plan.strip():
        raise ValueError("owner chat, story and plan are required")
    branch, head = _identity(repository, worktree)
    path = registry_path()
    with _locked(path):
        data = _read(path)
        if str(worktree) in data["entries"]:
            raise ValueError(f"worktree is already registered: {worktree}")
        entry = {
            "repository": str(repository),
            "worktree": str(worktree),
            "branch": branch,
            "head": head,
            "owner_chat": owner_chat,
            "story": story,
            "plan": plan,
            "pr": pr,
            "released": False,
        }
        data["entries"][str(worktree)] = entry
        _write(path, data)
    return entry


def start(
    repository: Path,
    worktree: Path,
    branch: str,
    base: str,
    owner_chat: str,
    story: str,
    plan: str,
) -> dict:
    repository, worktree = repository.resolve(), worktree.expanduser().absolute()
    if worktree.exists():
        raise ValueError(f"worktree path already exists: {worktree}")
    if not owner_chat.strip() or not story.strip() or not plan.strip():
        raise ValueError("owner chat, story and plan are required")
    _run(
        "git",
        "-C",
        str(repository),
        "worktree",
        "add",
        "-b",
        branch,
        str(worktree),
        base,
    )
    return register(repository, worktree, owner_chat, story, plan)


def update(
    worktree: Path, owner_chat: str, pr: str | None = None, plan: str | None = None
) -> dict:
    worktree = worktree.resolve()
    path = registry_path()
    with _locked(path):
        data = _read(path)
        entry = _entry(data, worktree, owner_chat)
        if entry["released"]:
            raise ValueError("released worktree cannot be updated")
        branch, head = _identity(Path(entry["repository"]), worktree)
        if branch != entry["branch"]:
            raise ValueError("worktree branch changed")
        entry["head"] = head
        if pr is not None:
            entry["pr"] = pr
        if plan is not None:
            if not plan.strip():
                raise ValueError("plan cannot be empty")
            entry["plan"] = plan
        _write(path, data)
        return entry.copy()


def release(worktree: Path, owner_chat: str) -> dict:
    worktree = worktree.resolve()
    path = registry_path()
    with _locked(path):
        data = _read(path)
        entry = _entry(data, worktree, owner_chat)
        branch, head = _identity(Path(entry["repository"]), worktree)
        if branch != entry["branch"] or head != entry["head"]:
            raise ValueError("worktree changed since last registry update")
        if not entry["pr"]:
            raise ValueError("PR is required before releasing worktree")
        entry["released"] = True
        _write(path, data)
        return entry.copy()


def audit() -> list[dict]:
    data = _read(registry_path())
    result = []
    for entry in data["entries"].values():
        row = entry.copy()
        try:
            branch, head = _identity(Path(entry["repository"]), Path(entry["worktree"]))
            row["present"] = True
            row["head_matches"] = head == entry["head"] and branch == entry["branch"]
            row["clean"] = not _status(Path(entry["worktree"]))
        except ValueError:
            row.update(present=False, head_matches=False, clean=False)
        result.append(row)
    return sorted(result, key=lambda row: row["worktree"])


def _github_repo(repository: Path) -> str:
    remote = _run("git", "-C", str(repository), "remote", "get-url", "origin")
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote)
    if not match:
        raise ValueError("origin is not a GitHub repository")
    return f"{match.group(1)}/{match.group(2)}"


def _status(worktree: Path) -> str:
    return _run(
        "git",
        "-C",
        str(worktree),
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--ignored=matching",
    )


def cleanup(worktree: Path, owner_chat: str) -> dict:
    worktree = worktree.resolve()
    path = registry_path()
    with _locked(path):
        data = _read(path)
        entry = _entry(data, worktree, owner_chat)
        if not entry["released"]:
            raise ValueError("owner has not released worktree")
        repository = Path(entry["repository"])
        branch, head = _identity(repository, worktree)
        if branch != entry["branch"] or head != entry["head"]:
            raise ValueError("worktree branch or head changed since release")
        if _status(worktree):
            raise ValueError("worktree has tracked or untracked changes")
        if not re.fullmatch(r"[1-9][0-9]*", entry["pr"]):
            raise ValueError("a numeric GitHub PR is required for cleanup")
        review = json.loads(
            _run(
                "gh",
                "pr",
                "view",
                entry["pr"],
                "--repo",
                _github_repo(repository),
                "--json",
                "state,headRefOid",
            )
        )
        if review.get("state") != "MERGED" or review.get("headRefOid") != head:
            raise ValueError("PR is not merged at this exact worktree head")
        _run("git", "-C", str(repository), "worktree", "remove", str(worktree))
        _run("git", "-C", str(repository), "branch", "-D", branch)
        del data["entries"][str(worktree)]
        _write(path, data)
    return {
        "removed_worktree": str(worktree),
        "removed_branch": branch,
        "merged_pr": entry["pr"],
        "head": head,
    }
