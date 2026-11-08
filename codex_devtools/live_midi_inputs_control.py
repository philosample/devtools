"""Complete Live MIDI input inventory and guarded, explicit single-press actions."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .live_midi_ax import ROWS as STUDIO_CAPTURE_ROWS
from .live_midi_sync_control import (
    _replace_owned_json,
    _safe_new_path,
    _utc_timestamp,
    _write_new_json,
)

FORMAT = "codex-devtools-live-midi-inputs-baseline-v1"
RECEIPT_FORMAT = "codex-devtools-live-midi-inputs-receipt-v1"
FLAGS = tuple(
    STUDIO_CAPTURE_ROWS[f"input_{flag}"].rpartition(": MIDI Input: ")[2]
    for flag in ("track", "sync", "remote")
)
MAX_SECONDS = 180
MAX_CHECKBOXES = 512


def inventory_hash(value: dict) -> str:
    """Hash inventory and provenance, excluding only the digest itself."""
    payload = {key: item for key, item in value.items() if key != "inventory_sha256"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_inventory(value: object, *, pid: int, bundle: str, count: int) -> dict:
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        raise ValueError("unsupported complete input inventory schema")
    if value.get("read_only") is not True or value.get("status") != "observed":
        raise ValueError("inventory must be a read-only complete observation")
    target = value.get("target", {})
    if (
        not isinstance(target, dict)
        or type(target.get("pid")) is not int
        or target.get("pid") != pid
        or target.get("bundle_identifier") != bundle
        or not isinstance(target.get("process_name"), str)
        or not target["process_name"]
        or not isinstance(target.get("launch_date"), str)
        or not target["launch_date"]
    ):
        raise ValueError("input inventory process identity changed or is incomplete")
    _utc_timestamp(value.get("observed_at_utc"), "observation time")
    bounds = value.get("bounds", {})
    if (
        not isinstance(bounds, dict)
        or bounds.get("max_elements") != 1500
        or bounds.get("max_depth") != 12
        or bounds.get("max_windows") != 64
        or bounds.get("truncated") is not False
        or type(bounds.get("elements_observed")) is not int
        or not 1 <= bounds["elements_observed"] <= 1500
        or type(bounds.get("windows_observed")) is not int
        or not 1 <= bounds["windows_observed"] <= 64
    ):
        raise ValueError("input inventory is incomplete or out of bounds")
    window = value.get("settings_window", {})
    if (
        not isinstance(window, dict)
        or window.get("title") != "Settings"
        or not isinstance(window.get("identity"), str)
        or not window["identity"]
    ):
        raise ValueError("unique Settings window identity is missing")
    checks = value.get("checkboxes")
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_CHECKBOXES:
        raise ValueError("complete checkbox inventory is missing or exceeds bounds")
    identities = set()
    descriptions = set()
    groups = {}
    order = []
    previous_index = -1
    for row in checks:
        if not isinstance(row, dict):
            raise ValueError("invalid checkbox record")  # noqa: TRY004 -- malformed JSON uses the CLI ValueError contract
        identity = row.get("identity")
        description = row.get("description")
        index = row.get("index")
        if (
            not isinstance(identity, str)
            or not identity
            or identity in identities
            or type(index) is not int
            or index <= previous_index
            or index >= bounds["elements_observed"]
            or not isinstance(description, str)
            or not description
            or description in descriptions
            or row.get("role") != "AXCheckBox"
            or type(row.get("enabled")) is not bool
            or type(row.get("value")) is not int
            or row["value"] not in (0, 1)
        ):
            raise ValueError("unknown, duplicate or invalid checkbox identity/state")
        identities.add(identity)
        descriptions.add(description)
        previous_index = index
        if ": MIDI Input:" not in description:
            continue
        name, separator, flag = description.rpartition(": MIDI Input: ")
        if (
            not separator
            or not name.strip()
            or flag not in FLAGS
            or name != name.strip()
        ):
            raise ValueError("ambiguous input checkbox description")
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(flag)
        if not row["enabled"] and row["value"]:
            raise ValueError("enabled input flag is disabled and cannot be isolated")
    if len(groups) != count or any(
        set(flags) != set(FLAGS) or len(flags) != 3 for flags in groups.values()
    ):
        raise ValueError("missing, partial, duplicate or unexpected input groups")
    if (
        type(value.get("expected_input_count")) is not int
        or value.get("expected_input_count") != count
        or value.get("input_order") != order
    ):
        raise ValueError("input row count/order differs from exact inventory")
    if value.get("inventory_sha256") != inventory_hash(value):
        raise ValueError("inventory hash differs from baseline content")
    return value


def _fresh(baseline: dict, max_age: float) -> None:
    if (
        isinstance(max_age, bool)
        or not isinstance(max_age, (int, float))
        or not math.isfinite(max_age)
        or not 0 < max_age <= 300
    ):
        raise ValueError("baseline age limit must be positive and at most 300 seconds")
    age = (
        datetime.now(timezone.utc)
        - _utc_timestamp(baseline["observed_at_utc"], "baseline time")
    ).total_seconds()
    if not -5 <= age <= max_age:
        raise ValueError("baseline is stale or from the future")


def _same_settings_bounds(original: object, current: object) -> bool:
    """Compare selected Settings-tree bounds, ignoring total app window count."""
    if not isinstance(original, dict) or not isinstance(current, dict):
        return False
    for bounds in (original, current):
        count = bounds.get("windows_observed")
        if type(count) is not int or not 1 <= count <= 64:
            return False
    return {
        key: value for key, value in original.items() if key != "windows_observed"
    } == {key: value for key, value in current.items() if key != "windows_observed"}


def reconcile(baseline: dict, current: dict, expected: dict[str, int]) -> None:
    """Require the complete original inventory with only known value changes."""
    for key in (
        "target",
        "settings_window",
        "input_order",
        "expected_input_count",
    ):
        if baseline[key] != current[key]:
            raise ValueError(f"inventory changed: {key}")
    if not _same_settings_bounds(baseline.get("bounds"), current.get("bounds")):
        raise ValueError("inventory changed: bounds")
    if len(baseline["checkboxes"]) != len(current["checkboxes"]):
        raise ValueError("checkbox inventory changed")
    for original, fresh in zip(
        baseline["checkboxes"], current["checkboxes"], strict=True
    ):
        identity = original["identity"]
        if {k: v for k, v in original.items() if k != "value"} != {
            k: v for k, v in fresh.items() if k != "value"
        } or fresh["value"] != expected[identity]:
            raise ValueError("checkbox identity, order, enabled state or value changed")


def historical_restore_expected(baseline: dict, current: dict) -> dict[str, int]:
    """Permit only original-on MIDI inputs to be Off in a historical restore."""
    expected = {row["identity"]: row["value"] for row in baseline["checkboxes"]}
    for row, fresh in zip(baseline["checkboxes"], current["checkboxes"], strict=True):
        identity = row["identity"]
        if ": MIDI Input: " in row["description"] and row["value"] == 1:
            if fresh["value"] not in (0, 1):
                raise ValueError("historical restore input value is invalid")
            expected[identity] = fresh["value"]
    reconcile(baseline, current, expected)
    return expected


def transition_plan(baseline: dict, current: dict, operation: str) -> list[dict]:
    if operation not in {"isolate", "restore"}:
        raise ValueError("action must be isolate or restore")
    expected = {row["identity"]: row["value"] for row in baseline["checkboxes"]}
    if operation == "restore":
        for row, fresh in zip(
            baseline["checkboxes"], current["checkboxes"], strict=True
        ):
            if ": MIDI Input: " in row["description"] and row["value"] == 1:
                expected[row["identity"]] = fresh["value"]
    reconcile(baseline, current, expected)
    actions = []
    for row, fresh in zip(baseline["checkboxes"], current["checkboxes"], strict=True):
        if ": MIDI Input: " in row["description"]:
            desired = 0 if operation == "isolate" else row["value"]
            if fresh["value"] != desired:
                actions.append(
                    {
                        "identity": row["identity"],
                        "description": row["description"],
                        "previous_value": fresh["value"],
                        "desired_value": desired,
                    }
                )
    return actions


def _native(
    pid: int,
    bundle: str,
    count: int,
    *,
    operation: str | None = None,
    baseline_path: Path | None = None,
    baseline_hash: str = "",
    expected: dict | None = None,
    action: dict | None = None,
    historical_restore: bool = False,
    deadline_unix_seconds: float | None = None,
    max_age: float = 300,
    timeout: float = MAX_SECONDS,
) -> dict:
    if action is not None and (
        operation not in {"isolate", "restore"}
        or baseline_path is None
        or expected is None
        or len(baseline_hash) != 64
        or isinstance(deadline_unix_seconds, bool)
        or not isinstance(deadline_unix_seconds, (int, float))
        or not math.isfinite(deadline_unix_seconds)
        or deadline_unix_seconds <= 0
    ):
        raise ValueError(
            "native press requires explicit action and validated baseline/state"
        )
    if (
        action is not None
        and not time.time() < deadline_unix_seconds <= time.time() + MAX_SECONDS
    ):
        raise ValueError(
            "absolute press deadline is expired or exceeds the action budget"
        )
    command = [
        "/usr/bin/swift",
        str(Path(__file__).with_name("live_midi_sync.swift")),
        "--pid",
        str(pid),
        "--bundle-id",
        bundle,
        "--expected-count",
        str(count),
        "--input-inventory",
        "press" if action else "inspect",
    ]
    with tempfile.TemporaryDirectory(prefix="live-midi-inputs-") as directory:
        if action:
            state = Path(directory) / "expected.json"
            state_data = json.dumps(expected).encode()
            state.write_bytes(state_data)
            command += [
                "--operation",
                operation,
                "--baseline",
                str(baseline_path),
                "--baseline-sha256",
                baseline_hash,
                "--max-baseline-age-seconds",
                str(max_age),
                "--input-expected",
                str(state),
                "--input-identity",
                action["identity"],
                "--input-desired",
                str(action["desired_value"]),
                "--deadline-unix-seconds",
                str(deadline_unix_seconds),
            ]
            if historical_restore:
                command += [
                    "--historical-current-inventory",
                    str(state),
                    "--historical-current-sha256",
                    hashlib.sha256(state_data).hexdigest(),
                ]
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout
        )
    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(
            "native helper returned no valid result; outcome unknown"
        ) from error
    if not isinstance(report, dict):
        raise ValueError("native helper result must be a JSON object; outcome unknown")  # noqa: TRY004 -- malformed native JSON uses the CLI ValueError contract
    if result.returncode != 0:
        raise ValueError(
            f"native helper stopped; outcome unknown: {report.get('error', 'unavailable')}"
        )
    return report


def run_live_midi_inputs(
    pid: int,
    *,
    expected_bundle_id: str,
    expected_count: int,
    action: str | None = None,
    baseline_path: Path | None = None,
    historical_current_path: Path | None = None,
    output_path: Path | None = None,
    execute: bool = False,
    max_baseline_age_seconds: float = 300,
) -> dict:
    if platform.system() != "Darwin":
        raise ValueError("Live MIDI input AX controls require macOS")
    if (
        type(pid) is not int
        or not 1 <= pid <= 2147483647
        or not isinstance(expected_bundle_id, str)
        or not expected_bundle_id.strip()
        or type(expected_count) is not int
        or not 1 <= expected_count <= 128
    ):
        raise ValueError("explicit PID, bundle and input row count 1–128 are required")

    def observe(**kwargs):
        result = _native(pid, expected_bundle_id, expected_count, **kwargs)
        result["inventory_sha256"] = inventory_hash(result)
        return validate_inventory(
            result, pid=pid, bundle=expected_bundle_id, count=expected_count
        )

    if not execute:
        if action is not None or baseline_path is not None:
            raise ValueError("actions and baselines require explicit --execute")
        if output_path is not None:
            _safe_new_path(output_path, "baseline")
        result = observe()
        if output_path is not None:
            _write_new_json(output_path, result)
        return result
    if (
        action not in {"isolate", "restore"}
        or baseline_path is None
        or output_path is None
    ):
        raise ValueError(
            "execute requires isolate/restore, original baseline and new receipt"
        )
    if historical_current_path is not None and action != "restore":
        raise ValueError("a historical current inventory is valid only for restore")
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "format": RECEIPT_FORMAT,
        "operation": action,
        "status": "stopped",
        "read_only": False,
        "actions": [],
        "manual_inspection_required": False,
        "phase": "baseline-validation",
    }
    started = time.monotonic()
    deadline_unix_seconds = time.time() + MAX_SECONDS
    receipt_identity = _write_new_json(output_path, report)
    try:
        if (
            not baseline_path.is_absolute()
            or baseline_path.is_symlink()
            or not baseline_path.is_file()
            or baseline_path.stat().st_size > 1_000_000
        ):
            raise ValueError(
                "baseline must be an absolute regular file, never a symlink"
            )
        raw = baseline_path.read_bytes()
        baseline = validate_inventory(
            json.loads(raw), pid=pid, bundle=expected_bundle_id, count=expected_count
        )
        baseline_hash = hashlib.sha256(raw).hexdigest()
        report.update(
            baseline_sha256=baseline_hash, target=baseline["target"], phase="preflight"
        )
        historical_current = None
        historical_current_hash = ""
        if historical_current_path is None:
            _fresh(baseline, max_baseline_age_seconds)
        else:
            if (
                not historical_current_path.is_absolute()
                or historical_current_path.is_symlink()
                or not historical_current_path.is_file()
                or historical_current_path.stat().st_size > 1_000_000
            ):
                raise ValueError(
                    "historical current inventory must be an absolute regular file, never a symlink"
                )
            current_raw = historical_current_path.read_bytes()
            historical_current = validate_inventory(
                json.loads(current_raw),
                pid=pid,
                bundle=expected_bundle_id,
                count=expected_count,
            )
            _fresh(historical_current, max_baseline_age_seconds)
            historical_current_hash = hashlib.sha256(current_raw).hexdigest()
            historical_restore_expected(baseline, historical_current)
            report.update(
                scope="historical-restore",
                current_observation_sha256=historical_current_hash,
            )
        current = observe()
        if historical_current is not None:
            initial_values = {
                row["identity"]: row["value"]
                for row in historical_current["checkboxes"]
            }
            reconcile(historical_current, current, initial_values)
        actions = transition_plan(baseline, current, action)
        report["actions"] = [
            row | {"attempted": False, "outcome": "not_attempted", "actual_value": None}
            for row in actions
        ]
        for row in report["actions"]:
            remaining = MAX_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise ValueError("bounded action time exceeded")
            _fresh(
                current if historical_current is not None else baseline,
                max_baseline_age_seconds,
            )
            report["phase"] = "press"
            row.update(attempted=True, outcome="unknown")
            report["manual_inspection_required"] = True
            receipt_identity = _replace_owned_json(
                output_path, receipt_identity, report
            )
            remaining = MAX_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise ValueError(
                    "bounded action time exceeded during prepress receipt persistence"
                )
            observed = observe(
                operation=action,
                baseline_path=baseline_path,
                baseline_hash=baseline_hash,
                expected=current,
                action=row,
                historical_restore=historical_current is not None,
                deadline_unix_seconds=deadline_unix_seconds,
                max_age=max_baseline_age_seconds,
                timeout=remaining,
            )
            expected = {
                item["identity"]: item["value"] for item in current["checkboxes"]
            }
            expected[row["identity"]] = row["desired_value"]
            reconcile(baseline, observed, expected)
            row.update(outcome="known", actual_value=row["desired_value"])
            current = observed
            receipt_identity = _replace_owned_json(
                output_path, receipt_identity, report
            )
        final = observe(timeout=max(0.01, MAX_SECONDS - (time.monotonic() - started)))
        reconcile(
            baseline,
            final,
            {item["identity"]: item["value"] for item in current["checkboxes"]},
        )
        if action == "restore" and transition_plan(baseline, final, "restore"):
            raise ValueError("exact restoration is incomplete")
        _fresh(
            final if historical_current is not None else baseline,
            max_baseline_age_seconds,
        )
        report.update(
            status="complete",
            phase="complete",
            observed=final,
            manual_inspection_required=False,
        )
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        report["manual_inspection_required"] = any(
            row["attempted"] for row in report["actions"]
        )
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    _replace_owned_json(output_path, receipt_identity, report)
    return report
