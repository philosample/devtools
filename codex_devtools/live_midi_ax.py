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
    bounds = snapshot.get("bounds")
    if (
        not isinstance(bounds, dict)
        or bounds.get("truncated") is not False
        or bounds.get("windows_truncated", False) is not False
        or bounds.get("window_title_filter") != "Settings"
    ):
        raise ValueError(
            f"snapshot must be an untruncated Settings-window observation: {path}"
        )
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
    trees = snapshot.get("trees")
    if not isinstance(trees, list) or not trees:
        raise ValueError(f"snapshot has no inspected window tree: {path}")
    for tree in trees:
        if not isinstance(tree, dict) or not isinstance(tree.get("elements"), list):
            raise ValueError(f"snapshot tree schema is invalid: {path}")  # noqa: TRY004 -- input document validation uses CLI ValueError contract
        for element in tree["elements"]:
            if not isinstance(element, dict):
                raise ValueError(f"snapshot element schema is invalid: {path}")  # noqa: TRY004 -- input document validation uses CLI ValueError contract
            description = element.get("description")
            name = identities.get(description)
            if name is None:
                continue
            if name in values:
                raise ValueError(f"duplicate AX row for {description}: {path}")
            if (
                element.get("role") != "AXCheckBox"
                or element.get("enabled") is not True
            ):
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
