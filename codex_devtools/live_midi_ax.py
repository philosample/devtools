"""Validate saved AX observations for the Studio Capture MIDI switches."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROWS = {
    "input_track": "IAC Driver (Studio Capture): MIDI Input: Track",
    "input_sync": "IAC Driver (Studio Capture): MIDI Input: Sync",
    "input_remote": "IAC Driver (Studio Capture): MIDI Input: Remote Control",
    "output_track": "IAC Driver (Studio Capture): MIDI Output: Track",
    "output_sync": "IAC Driver (Studio Capture): MIDI Output: Sync",
    "output_remote": "IAC Driver (Studio Capture): MIDI Output: Remote Control",
}

EXPECTED = {
    "input_sync": 0,
    "input_remote": 0,
    "output_track": 1,
    "output_sync": 0,
    "output_remote": 0,
}


def _snapshot_tree(snapshot: dict, path: Path) -> list[dict]:
    bounds = snapshot.get("bounds")
    if not isinstance(bounds, dict):
        raise ValueError(f"snapshot bounds are missing or invalid: {path}")  # noqa: TRY004 -- malformed input documents use CLI ValueError contract
    required = {
        "max_elements",
        "max_depth",
        "max_windows",
        "windows_observed",
        "window_count",
        "windows_truncated",
        "elements_observed",
        "truncated",
        "window_title_filter",
    }
    missing = sorted(required - set(bounds))
    if missing:
        raise ValueError(
            f"snapshot v1 bounds are missing required fields: {', '.join(missing)}"
        )
    for name in (
        "max_elements",
        "max_depth",
        "max_windows",
        "windows_observed",
        "window_count",
        "elements_observed",
    ):
        if type(bounds[name]) is not int:
            raise ValueError(f"snapshot bounds field must be an integer: {name}")
    for name in ("windows_truncated", "truncated"):
        if type(bounds[name]) is not bool:
            raise ValueError(f"snapshot bounds field must be a boolean: {name}")
    if not 1 <= bounds["max_elements"] <= 2048:
        raise ValueError("snapshot max_elements is outside native v1 limits")
    if not 0 <= bounds["max_depth"] <= 32:
        raise ValueError("snapshot max_depth is outside native v1 limits")
    if bounds["max_windows"] != 64:
        raise ValueError("snapshot max_windows differs from native v1 limit")
    if bounds["window_count"] < 1 or bounds["windows_observed"] < 1:
        raise ValueError("snapshot must report at least one application window")
    if bounds["windows_observed"] != min(bounds["window_count"], bounds["max_windows"]):
        raise ValueError("snapshot window counts are inconsistent")
    if bounds["windows_truncated"] != (bounds["window_count"] > bounds["max_windows"]):
        raise ValueError("snapshot windows_truncated disagrees with window counts")
    if bounds["window_title_filter"] != "Settings":
        raise ValueError(
            f"snapshot is not an exact Settings-window observation: {path}"
        )
    if bounds["windows_truncated"] or bounds["truncated"]:
        raise ValueError(f"snapshot tree is truncated: {path}")
    if (
        bounds["elements_observed"] < 1
        or bounds["elements_observed"] > bounds["max_elements"]
    ):
        raise ValueError("snapshot element count is outside native v1 bounds")

    windows = snapshot.get("windows")
    if not isinstance(windows, list) or len(windows) != bounds["windows_observed"]:
        raise ValueError("snapshot windows array disagrees with windows_observed")
    window_indices = []
    for window in windows:
        if (
            not isinstance(window, dict)
            or type(window.get("index")) is not int
            or not isinstance(window.get("title"), str)
        ):
            raise ValueError("snapshot windows array has an invalid row")
        window_indices.append(window["index"])
    if len(set(window_indices)) != len(window_indices):
        raise ValueError("snapshot windows array contains duplicate indices")
    if sorted(window_indices) != list(range(bounds["windows_observed"])):
        raise ValueError("snapshot window indices disagree with windows_observed")

    trees = snapshot.get("trees")
    if not isinstance(trees, list) or len(trees) != 1 or not isinstance(trees[0], dict):
        raise ValueError(f"snapshot must contain one filtered window tree: {path}")
    tree = trees[0]
    if (
        type(tree.get("window_index")) is not int
        or tree["window_index"] not in window_indices
        or not any(
            window["index"] == tree["window_index"] and window["title"] == "Settings"
            for window in windows
        )
    ):
        raise ValueError("snapshot tree does not identify its Settings window")
    elements = tree.get("elements")
    if not isinstance(elements, list) or len(elements) != bounds["elements_observed"]:
        raise ValueError("snapshot elements array disagrees with elements_observed")
    element_indices = []
    for element in elements:
        if (
            not isinstance(element, dict)
            or type(element.get("index")) is not int
            or type(element.get("depth")) is not int
            or not 0 <= element["depth"] <= bounds["max_depth"]
        ):
            raise ValueError("snapshot element is inconsistent with native v1 bounds")
        element_indices.append(element["index"])
    if sorted(element_indices) != list(range(bounds["elements_observed"])):
        raise ValueError("snapshot element indices disagree with elements_observed")
    return elements


def _read_snapshot(path: Path) -> tuple[dict, dict[str, int], str, str]:
    try:
        raw = path.read_bytes()
        snapshot = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read valid snapshot JSON: {path}") from error
    if not isinstance(snapshot, dict):
        raise ValueError(f"snapshot root must be an object: {path}")  # noqa: TRY004 -- input document validation uses CLI ValueError contract
    if (
        snapshot.get("format") != "codex-devtools-ax-snapshot-v1"
        or snapshot.get("status") != "observed"
        or snapshot.get("read_only") is not True
    ):
        raise ValueError(f"unsupported or non-observation AX snapshot: {path}")
    target = snapshot.get("target")
    if (
        not isinstance(target, dict)
        or type(target.get("pid")) is not int
        or target["pid"] <= 0
        or not isinstance(target.get("process_name"), str)
        or not isinstance(target.get("bundle_identifier"), str)
        or not target["bundle_identifier"]
    ):
        raise ValueError(f"snapshot target identity is incomplete: {path}")
    elements = _snapshot_tree(snapshot, path)
    try:
        observed = datetime.fromisoformat(
            snapshot["observed_at_utc"].replace("Z", "+00:00")
        )
    except (KeyError, AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"snapshot timestamp is missing or invalid: {path}") from error
    if observed.tzinfo is None or observed.utcoffset() != timezone.utc.utcoffset(
        observed
    ):
        raise ValueError(f"snapshot timestamp must be UTC: {path}")

    identities = {description: name for name, description in ROWS.items()}
    values: dict[str, int] = {}
    for element in elements:
        description = element.get("description")
        name = identities.get(description)
        if name is None:
            continue
        if name in values:
            raise ValueError(f"duplicate AX row for {description}: {path}")
        if element.get("role") != "AXCheckBox" or element.get("enabled") is not True:
            raise ValueError(
                f"AX row has unexpected role or disabled state: {description}"
            )
        value = element.get("value")
        if isinstance(value, bool):
            values[name] = int(value)
        elif type(value) is int and value in (0, 1):
            values[name] = value
        else:
            raise ValueError(f"AX row value must be 0 or 1: {description}")
    missing = sorted(set(ROWS) - set(values))
    if missing:
        raise ValueError(
            f"snapshot is missing Studio Capture switch rows: {', '.join(missing)}"
        )
    return snapshot, values, hashlib.sha256(raw).hexdigest(), observed.isoformat()


def verify_studio_capture_isolation(
    before_path: Path,
    isolated_path: Path,
    restored_path: Path,
    *,
    expected_bundle_id: str,
    max_age_seconds: float | None = None,
    now: datetime | None = None,
) -> dict:
    """Check exact switch rows and restoration in three saved native AX snapshots."""
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id.strip():
        raise ValueError("expected bundle identifier is required")
    if max_age_seconds is not None and (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or max_age_seconds <= 0
    ):
        raise ValueError("maximum age must be positive seconds")
    paths = (before_path, isolated_path, restored_path)
    snapshots = [_read_snapshot(path) for path in paths]
    documents = [item[0] for item in snapshots]
    values = [item[1] for item in snapshots]
    targets = [document["target"] for document in documents]
    pids = [target["pid"] for target in targets]
    bundle_ids = [target["bundle_identifier"] for target in targets]
    process_names = [target["process_name"] for target in targets]
    if len(set(pids)) != 1 or len(set(bundle_ids)) != 1 or len(set(process_names)) != 1:
        raise ValueError("snapshot target PID or bundle identity changed")
    if bundle_ids[0] != expected_bundle_id:
        raise ValueError("snapshot bundle identifier differs from expected target")

    observed = [datetime.fromisoformat(item[3]) for item in snapshots]
    if not observed[0] < observed[1] < observed[2]:
        raise ValueError(
            "before, isolated and restored timestamps must be strictly ordered"
        )
    age_report = None
    if max_age_seconds is not None:
        current_time = now or datetime.now(timezone.utc)
        if (
            current_time.tzinfo is None
            or current_time.utcoffset() != timezone.utc.utcoffset(current_time)
        ):
            raise ValueError("current time must be timezone-aware UTC")
        ages = [(current_time - timestamp).total_seconds() for timestamp in observed]
        if any(age < 0 or age > max_age_seconds for age in ages):
            raise ValueError(
                "one or more snapshots fall outside the requested age window"
            )
        age_report = {
            "maximum_age_seconds": max_age_seconds,
            "snapshot_age_seconds": ages,
            "passed": True,
        }

    changed_during_isolation = sorted(
        name for name in ROWS if values[0][name] != values[1][name]
    )
    checks = {
        "isolated_input_track_off": values[1]["input_track"] == 0,
        "other_switches_match_expected": all(
            row_values[name] == expected
            for row_values in values
            for name, expected in EXPECTED.items()
        ),
        "only_input_track_changed": set(changed_during_isolation) <= {"input_track"},
        "restored_exactly": values[2] == values[0],
    }
    return {
        "format": "codex-devtools-studio-capture-ax-isolation-v1",
        "valid": all(checks.values()),
        "status": "passed" if all(checks.values()) else "failed",
        "evidence_level": "saved native AX snapshot values",
        "current_state_claim": "observed at recorded timestamps only",
        "limit": "Does not prove MIDI delivery, silence, or any state after the final snapshot.",
        "target": {
            "pid": pids[0],
            "process_name": process_names[0],
            "bundle_identifier": bundle_ids[0],
        },
        "observed_at_utc": [item[3] for item in snapshots],
        "freshness_gate": age_report or {"passed": None, "requested": False},
        "switches": {
            name: {
                "description": description,
                "before": values[0][name],
                "isolated": values[1][name],
                "restored": values[2][name],
            }
            for name, description in ROWS.items()
        },
        "changed_during_isolation": changed_during_isolation,
        "checks": checks,
        "snapshot_sha256": [item[2] for item in snapshots],
    }
