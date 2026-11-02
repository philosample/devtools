"""Bounded read-only native macOS Accessibility snapshots."""

from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path


def snapshot(
    pid: int,
    *,
    window_title: str | None = None,
    max_elements: int = 500,
    max_depth: int = 10,
) -> dict:
    """Read a bounded AX tree for one exact PID; never prompts or acts on the app."""
    if platform.system() != "Darwin":
        raise ValueError("native Accessibility snapshots require macOS")
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("PID must be a positive 32-bit integer")
    if type(max_elements) is not int or not 1 <= max_elements <= 2048:
        raise ValueError("max elements must be between 1 and 2048")
    if type(max_depth) is not int or not 0 <= max_depth <= 32:
        raise ValueError("max depth must be between 0 and 32")
    if window_title is not None and (
        not isinstance(window_title, str)
        or not window_title
        or len(window_title.encode("utf-8")) > 256
    ):
        raise ValueError("window title must contain 1 to 256 UTF-8 bytes")

    script = Path(__file__).with_name("ax_snapshot.swift")
    command = [
        "/usr/bin/swift",
        str(script),
        "--pid",
        str(pid),
        "--max-elements",
        str(max_elements),
        "--max-depth",
        str(max_depth),
    ]
    if window_title is not None:
        command.extend(("--window-title", window_title))
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"native AX snapshot command failed: {error}") from error
    if result.returncode:
        message = result.stderr.strip()[-2000:]
        raise ValueError(message or f"native AX snapshot exited {result.returncode}")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("native AX snapshot returned invalid JSON") from error
    if (
        not isinstance(parsed, dict)
        or parsed.get("format") != "codex-devtools-ax-snapshot-v1"
        or parsed.get("status") != "observed"
        or parsed.get("target", {}).get("pid") != pid
    ):
        raise ValueError("native AX snapshot returned an unexpected schema or PID")
    return parsed
