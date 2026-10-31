"""Read-only discovery of an existing Node.js runtime."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


DEFAULT_LIVE_APP = Path("/Applications/Ableton Live 12 Suite.app")
LIVE_NODE_SUFFIX = Path(
    "Contents/App-Resources/Max/Max.app/Contents/Resources/C74/packages/"
    "Node for Max/source/bin/osx/node/node"
)
MAX_NODE_SUFFIX = Path(
    "Contents/Resources/C74/packages/Node for Max/source/bin/osx/node/node"
)
NODE_VERSION_TIMEOUT_SECONDS = 5


def _candidate_from_app(app_path: str | Path) -> Path:
    app = Path(app_path).expanduser()
    if app.name == "Max.app":
        return app / MAX_NODE_SUFFIX
    return app / LIVE_NODE_SUFFIX


def _try_node(path: Path, provenance: str, timeout: float) -> tuple[dict[str, Any] | None, dict[str, str]]:
    resolved = path.expanduser().resolve(strict=False)
    path_text = str(resolved)
    if not resolved.is_file():
        return None, {"provenance": provenance, "path": path_text, "status": "unavailable",
                      "error": "Node executable does not exist"}
    if not os.access(resolved, os.X_OK):
        return None, {"provenance": provenance, "path": path_text, "status": "unavailable",
                      "error": "Node file is not executable"}
    try:
        result = subprocess.run(
            [path_text, "--version"], capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return None, {"provenance": provenance, "path": path_text, "status": "unavailable",
                      "error": f"node --version timed out after {timeout:g} seconds"}
    except OSError as exc:
        return None, {"provenance": provenance, "path": path_text, "status": "unavailable",
                      "error": f"could not execute Node: {exc}"}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail[:240]}" if detail else ""
        return None, {"provenance": provenance, "path": path_text, "status": "unavailable",
                      "error": f"node --version exited with status {result.returncode}{suffix}"}
    version = result.stdout.strip().splitlines()
    if not version:
        return None, {"provenance": provenance, "path": path_text, "status": "unavailable",
                      "error": "node --version returned no version string"}
    selected = {"path": path_text, "version": version[0], "provenance": provenance}
    return selected, {"provenance": provenance, "path": path_text, "status": "selected"}


def discover_node(app_path: str | Path | None = None, *, search_path: str | None = None) -> dict[str, Any]:
    """Find Node on PATH first, then in the default or explicitly selected Max app.

    The selected executable is invoked only with ``--version`` and a fixed,
    short timeout. No files, package configuration, apps, or routing are changed.
    ``app_path`` may name an Ableton Live app bundle or a Max.app bundle.
    """
    attempts: list[dict[str, str]] = []
    path_node = shutil.which("node", path=search_path)
    if path_node:
        selected, attempt = _try_node(Path(path_node), "PATH", NODE_VERSION_TIMEOUT_SECONDS)
        attempts.append(attempt)
        if selected:
            return {"runtime": "node", "available": True, **selected, "attempts": attempts}
    else:
        attempts.append({"provenance": "PATH", "status": "unavailable", "error": "Node was not found on PATH"})

    app = Path(app_path).expanduser() if app_path is not None else DEFAULT_LIVE_APP
    provenance = "Ableton Live bundled Max Node" if app_path is None else "explicit app bundled Max Node"
    bundled_node = _candidate_from_app(app)
    selected, attempt = _try_node(bundled_node, provenance, NODE_VERSION_TIMEOUT_SECONDS)
    attempt["app"] = str(app.resolve(strict=False))
    attempts.append(attempt)
    if selected:
        selected["app"] = str(app.resolve(strict=False))
        return {"runtime": "node", "available": True, **selected, "attempts": attempts}

    reasons = "; ".join(f"{item['provenance']}: {item['error']}" for item in attempts)
    return {"runtime": "node", "available": False, "path": None, "version": None,
            "provenance": None, "attempts": attempts,
            "error": f"No usable Node.js runtime found ({reasons})"}

