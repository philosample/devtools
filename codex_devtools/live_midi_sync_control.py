"""Inspect and explicitly isolate/restore Live MIDI Output Sync checkboxes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

FORMAT_V1 = "codex-devtools-live-midi-sync-baseline-v1"
FORMAT = "codex-devtools-live-midi-sync-baseline-v2"
RECEIPT_FORMAT = "codex-devtools-live-midi-sync-receipt-v1"
OUTPUT_SUFFIX = ": MIDI Output: Sync"
MAX_ELEMENTS = 1500
MAX_DEPTH = 12
MAX_WINDOWS = 64
MAX_BASELINE_AGE_SECONDS = 300.0
MAX_CHECKBOXES = 512
NATIVE_TIMEOUT_SECONDS = 180
FOCUS_DIAGNOSTICS_FORMAT = "codex-devtools-live-midi-sync-focus-diagnostics-v1"


def _utc_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a UTC timestamp")  # noqa: TRY004 -- malformed JSON uses the CLI ValueError contract
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be a UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be a UTC timestamp")
    return parsed


def _complete_settings_tree(snapshot: dict) -> tuple[dict, dict, list[dict]]:
    if (
        snapshot.get("format") != "codex-devtools-ax-snapshot-v1"
        or snapshot.get("status") != "observed"
        or snapshot.get("read_only") is not True
    ):
        raise ValueError("native AX observation format/status is unsupported")
    target = snapshot.get("target")
    if (
        not isinstance(target, dict)
        or type(target.get("pid")) is not int
        or target["pid"] <= 0
        or not isinstance(target.get("process_name"), str)
        or not target["process_name"].strip()
        or not isinstance(target.get("bundle_identifier"), str)
        or not target["bundle_identifier"]
    ):
        raise ValueError("native AX target identity is incomplete")
    bounds = snapshot.get("bounds")
    fields = {
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
    if not isinstance(bounds, dict) or fields - set(bounds):
        raise ValueError("native AX snapshot bounds are incomplete")
    int_fields = (
        "max_elements",
        "max_depth",
        "max_windows",
        "windows_observed",
        "window_count",
        "elements_observed",
    )
    if any(type(bounds.get(name)) is not int for name in int_fields):
        raise ValueError("native AX snapshot has invalid bounds integers")
    if any(
        type(bounds.get(name)) is not bool
        for name in ("truncated", "windows_truncated")
    ):
        raise ValueError("native AX snapshot has invalid truncation flags")
    if (
        bounds["max_elements"] != MAX_ELEMENTS
        or bounds["max_depth"] != MAX_DEPTH
        or bounds["max_windows"] != MAX_WINDOWS
        or bounds["max_elements"] < bounds["elements_observed"]
        or bounds["max_depth"] < 0
        or bounds["window_title_filter"] != "Settings"
        or bounds["truncated"]
        or bounds["windows_truncated"]
        or bounds["window_count"] != bounds["windows_observed"]
        or not 1 <= bounds["window_count"] <= MAX_WINDOWS
        or bounds["elements_observed"] < 1
    ):
        raise ValueError(
            "native AX Settings observation is incomplete or out of bounds"
        )
    windows = snapshot.get("windows")
    if not isinstance(windows, list) or len(windows) != bounds["windows_observed"]:
        raise ValueError("native AX windows list differs from observed bounds")
    if any(
        not isinstance(window, dict)
        or type(window.get("index")) is not int
        or not isinstance(window.get("title"), str)
        or not window["title"]
        for window in windows
    ):
        raise ValueError("native AX windows list has an invalid row")
    if sorted(window["index"] for window in windows) != list(range(len(windows))):
        raise ValueError("native AX window indices are not complete and unique")
    settings = [window for window in windows if window["title"] == "Settings"]
    if len(settings) != 1:
        raise ValueError(
            "native AX observation must identify exactly one Settings window"
        )
    trees = snapshot.get("trees")
    if not isinstance(trees, list) or len(trees) != 1 or not isinstance(trees[0], dict):
        raise ValueError("native AX observation must contain exactly one Settings tree")
    tree = trees[0]
    if tree.get("window_index") != settings[0]["index"]:
        raise ValueError("native AX tree does not belong to the unique Settings window")
    elements = tree.get("elements")
    if not isinstance(elements, list) or len(elements) != bounds["elements_observed"]:
        raise ValueError("native AX elements list differs from observed bounds")
    if any(
        not isinstance(element, dict)
        or type(element.get("index")) is not int
        or type(element.get("depth")) is not int
        or not 0 <= element["depth"] <= bounds["max_depth"]
        for element in elements
    ):
        raise ValueError("native AX tree contains invalid element records")
    if sorted(element["index"] for element in elements) != list(range(len(elements))):
        raise ValueError("native AX element indices are not complete and unique")
    return target, bounds, elements


def baseline_from_snapshot(
    snapshot: dict, *, expected_pid: int, expected_bundle_id: str, expected_count: int
) -> dict:
    if type(expected_pid) is not int or expected_pid <= 0:
        raise ValueError("PID must be a positive integer")
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id.strip():
        raise ValueError("expected bundle identifier is required")
    if type(expected_count) is not int or not 1 <= expected_count <= 128:
        raise ValueError("expected output count must be between 1 and 128")
    target, bounds, elements = _complete_settings_tree(snapshot)
    if target["pid"] != expected_pid:
        raise ValueError("native AX PID differs from the caller's exact PID")
    if target["bundle_identifier"] != expected_bundle_id:
        raise ValueError("native AX bundle identifier differs from the expected app")
    observed = _utc_timestamp(
        snapshot.get("observed_at_utc"), "snapshot observed_at_utc"
    )
    launch_date = target.get("launch_date")
    _utc_timestamp(launch_date, "target launch_date")
    settings_window_identity = next(
        window.get("identity")
        for window in snapshot["windows"]
        if window["title"] == "Settings"
    )
    if (
        not isinstance(launch_date, str)
        or not launch_date
        or not isinstance(settings_window_identity, str)
        or not settings_window_identity
    ):
        raise ValueError("snapshot lacks exact process generation or Settings identity")
    outputs = {}
    checkboxes = []
    checkbox_identities = set()
    checkbox_descriptions = set()
    for element in elements:
        role = element.get("role")
        errors = element.get("attribute_errors", {})
        if (
            not isinstance(errors, dict)
            or errors.get("role") is not None
            or not isinstance(role, str)
        ):
            raise ValueError(
                "an AX element role is unreadable; checkbox inventory is uncertain"
            )
        if role == "AXCheckBox":
            description = element.get("description")
            identity = element.get("identity")
            index = element.get("index")
            enabled = element.get("enabled")
            value = element.get("value")
            if isinstance(value, bool):
                value = int(value)
            if (
                errors.get("description") is not None
                or errors.get("enabled") is not None
                or errors.get("value") is not None
                or not isinstance(description, str)
                or not description
                or not isinstance(identity, str)
                or not identity
                or identity in checkbox_identities
                or description in checkbox_descriptions
                or type(index) is not int
                or type(enabled) is not bool
                or type(value) is not int
                or value not in (0, 1)
            ):
                raise ValueError(
                    "a Settings checkbox is unreadable, duplicated or malformed"
                )
            if len(checkboxes) >= MAX_CHECKBOXES:
                raise ValueError("Settings checkbox inventory exceeds its bound")
            checkbox_identities.add(identity)
            checkbox_descriptions.add(description)
            checkboxes.append(
                {
                    "index": index,
                    "identity": identity,
                    "description": description,
                    "role": role,
                    "enabled": enabled,
                    "value": value,
                }
            )
        description = element.get("description")
        description_error = errors.get("description")
        if role == "AXCheckBox" and description_error is not None:
            raise ValueError(
                "a checkbox description is unreadable; output inventory is uncertain"
            )
        if not isinstance(description, str) or not description.endswith(OUTPUT_SUFFIX):
            continue
        name = description[: -len(OUTPUT_SUFFIX)].strip()
        if not name:
            raise ValueError("output Sync row has an empty device name")
        if name in outputs:
            raise ValueError(f"duplicate output Sync name: {name}")
        value = element.get("value")
        if isinstance(value, bool):
            value = int(value)
        if (
            role != "AXCheckBox"
            or element.get("enabled") is not True
            or type(value) is not int
            or value not in (0, 1)
        ):
            raise ValueError(
                f"output Sync row is not a readable enabled checkbox: {name}"
            )
        outputs[name] = {
            "name": name,
            "description": description,
            "role": role,
            "enabled": True,
            "value": value,
            "index": element["index"],
            "identity": element["identity"],
        }
    if not checkboxes:
        raise ValueError("complete Settings checkbox inventory is missing")
    if len(outputs) != expected_count:
        raise ValueError(
            f"output Sync row count {len(outputs)} differs from expected {expected_count}"
        )
    return {
        "format": FORMAT,
        "status": "observed",
        "read_only": True,
        "observed_at_utc": observed.isoformat().replace("+00:00", "Z"),
        "target": {
            "pid": target["pid"],
            "process_name": target["process_name"],
            "bundle_identifier": target["bundle_identifier"],
            "launch_date": launch_date,
        },
        "settings_window": {
            "title": "Settings",
            "index": settings_index(snapshot),
            "identity": settings_window_identity,
        },
        "expected_output_count": expected_count,
        "bounds": dict(bounds),
        "outputs": [outputs[name] for name in sorted(outputs)],
        "checkboxes": checkboxes,
    }


def settings_index(snapshot: dict) -> int:
    return next(
        window["index"]
        for window in snapshot["windows"]
        if window["title"] == "Settings"
    )


def validate_baseline(
    value: object,
    *,
    expected_pid: int,
    expected_bundle_id: str,
    expected_count: int,
    max_age_seconds: float = 300.0,
    now: datetime | None = None,
    allow_stale: bool = False,
) -> dict:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("format"), str)
        or value.get("format") not in (FORMAT_V1, FORMAT)
    ):
        raise ValueError("baseline format is unsupported")
    if value.get("status") != "observed" or value.get("read_only") is not True:
        raise ValueError("baseline is not a read-only observation")
    if type(expected_count) is not int or expected_count < 1:
        raise ValueError("expected output count must be positive")
    target = value.get("target")
    if (
        not isinstance(target, dict)
        or target.get("pid") != expected_pid
        or target.get("bundle_identifier") != expected_bundle_id
    ):
        raise ValueError(
            "baseline PID or bundle identity differs from requested target"
        )
    if value.get("expected_output_count") != expected_count:
        raise ValueError("baseline expected count differs from caller's count")
    version_two = value.get("format") == FORMAT
    timestamp = _utc_timestamp(value.get("observed_at_utc"), "baseline observed_at_utc")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() != timedelta(0):
        raise ValueError("current time must be timezone-aware UTC")
    age = (current - timestamp).total_seconds()
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or max_age_seconds <= 0
        or max_age_seconds > MAX_BASELINE_AGE_SECONDS
    ):
        raise ValueError(
            "maximum baseline age must be positive and at most 300 seconds"
        )
    if age < -5 or (not allow_stale and age > max_age_seconds):
        raise ValueError("baseline is outside the allowed freshness window")
    window = value.get("settings_window")
    if (
        not isinstance(window, dict)
        or window.get("title") != "Settings"
        or type(window.get("index")) is not int
    ):
        raise ValueError("baseline does not identify the unique Settings window")
    bounds = value.get("bounds")
    required_bounds = {
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
    numeric_bounds = (
        "max_elements",
        "max_depth",
        "max_windows",
        "windows_observed",
        "window_count",
        "elements_observed",
    )
    if (
        not isinstance(bounds, dict)
        or required_bounds - set(bounds)
        or any(type(bounds.get(key)) is not int for key in numeric_bounds)
        or type(bounds.get("windows_truncated")) is not bool
        or type(bounds.get("truncated")) is not bool
        or bounds.get("window_title_filter") != "Settings"
        or bounds.get("truncated") is not False
        or bounds.get("windows_truncated") is not False
        or bounds.get("window_count") != bounds.get("windows_observed")
        or window.get("index") >= bounds.get("window_count", 0)
        or bounds.get("max_windows") != MAX_WINDOWS
        or not 1 <= bounds.get("window_count", 0) <= MAX_WINDOWS
        or bounds.get("max_elements") != MAX_ELEMENTS
        or bounds.get("max_depth") != MAX_DEPTH
        or type(bounds.get("elements_observed")) is not int
        or not 1 <= bounds["elements_observed"] <= bounds["max_elements"]
    ):
        raise ValueError("baseline is not a complete bounded Settings observation")
    outputs = value.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != expected_count:
        raise ValueError("baseline output rows differ from caller's expected count")
    result = {}
    for row in outputs:
        if not isinstance(row, dict):
            raise ValueError("baseline contains an invalid output row")  # noqa: TRY004 -- malformed JSON uses the CLI ValueError contract
        name = row.get("name")
        description = row.get("description")
        current_value = row.get("value")
        if (
            not isinstance(name, str)
            or not name.strip()
            or description != f"{name}: MIDI Output: Sync"
            or row.get("role") != "AXCheckBox"
            or row.get("enabled") is not True
            or type(current_value) is not int
            or current_value not in (0, 1)
            or name in result
        ):
            raise ValueError("baseline contains an invalid or duplicate output row")
        result[name] = current_value
    if len(result) != expected_count:
        raise ValueError("baseline output identity count is inconsistent")
    if version_two:
        checkboxes = _validate_checkbox_inventory(value)
        output_rows = {row["description"]: row for row in outputs}
        checkbox_outputs = {
            row["description"]: row
            for row in checkboxes.values()
            if row["description"].endswith(OUTPUT_SUFFIX)
        }
        if set(output_rows) != set(checkbox_outputs):
            raise ValueError(
                "output rows do not cover the complete Sync checkbox inventory"
            )
        for row in outputs:
            checkbox = checkboxes.get(row.get("identity"))
            if (
                checkbox is None
                or type(row.get("index")) is not int
                or row["index"] != checkbox["index"]
                or checkbox["description"] != row["description"]
                or checkbox["value"] != row["value"]
            ):
                raise ValueError("output rows differ from complete checkbox inventory")
    return value


def _validate_checkbox_inventory(value: dict) -> dict[str, dict]:
    target = value.get("target")
    window = value.get("settings_window")
    if (
        not isinstance(target, dict)
        or not isinstance(target.get("launch_date"), str)
        or not target["launch_date"]
        or not isinstance(window, dict)
        or not isinstance(window.get("identity"), str)
        or not window["identity"]
    ):
        raise ValueError(
            "complete process generation and Settings identity are required"
        )
    _utc_timestamp(target["launch_date"], "target launch_date")
    rows = value.get("checkboxes")
    bounds = value.get("bounds")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CHECKBOXES:
        raise ValueError(
            "complete Settings checkbox inventory is missing or over bound"
        )
    if not isinstance(bounds, dict):
        raise ValueError("Settings checkbox inventory bounds are missing")  # noqa: TRY004 -- malformed JSON uses the CLI ValueError contract
    previous_index = -1
    identities = set()
    descriptions = set()
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Settings checkbox inventory contains an invalid row")  # noqa: TRY004 -- malformed JSON uses the CLI ValueError contract
        identity = row.get("identity")
        index = row.get("index")
        description = row.get("description")
        state = row.get("value")
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
            or type(state) is not int
            or state not in (0, 1)
        ):
            raise ValueError("Settings checkbox identity, order or state is invalid")
        identities.add(identity)
        descriptions.add(description)
        previous_index = index
        indexed[identity] = row
    return indexed


def historical_restore_expected(original: dict, current: dict) -> dict[str, int]:
    if original.get("format") != FORMAT or current.get("format") != FORMAT:
        raise ValueError("historical restore requires complete v2 Sync inventories")
    for key in ("target", "bounds", "expected_output_count"):
        if original[key] != current[key]:
            raise ValueError(f"historical current inventory changed: {key}")
    original_window = original.get("settings_window")
    current_window = current.get("settings_window")
    if (
        not isinstance(original_window, dict)
        or not isinstance(current_window, dict)
        or type(original_window.get("index")) is not int
        or type(current_window.get("index")) is not int
        or not 0 <= original_window["index"] < original["bounds"]["window_count"]
        or not 0 <= current_window["index"] < current["bounds"]["window_count"]
        or {key: value for key, value in original_window.items() if key != "index"}
        != {key: value for key, value in current_window.items() if key != "index"}
    ):
        raise ValueError("historical current inventory changed: settings_window")
    original_time = _utc_timestamp(
        original.get("observed_at_utc"), "original observation time"
    )
    current_time = _utc_timestamp(
        current.get("observed_at_utc"), "current observation time"
    )
    if current_time < original_time - timedelta(seconds=5):
        raise ValueError(
            "historical current observation predates the original baseline"
        )
    before = _validate_checkbox_inventory(original)
    after = _validate_checkbox_inventory(current)
    if [row["identity"] for row in original["checkboxes"]] != [
        row["identity"] for row in current["checkboxes"]
    ]:
        raise ValueError("historical current checkbox identity/order changed")
    original_outputs = {row["description"]: row["value"] for row in original["outputs"]}
    current_outputs = {row["description"]: row for row in current["outputs"]}
    expected = {}
    for identity, row in before.items():
        fresh = after[identity]
        for key in ("index", "identity", "description", "role", "enabled"):
            if row[key] != fresh[key]:
                raise ValueError("historical current checkbox identity/order changed")
        old_value = row["value"]
        new_value = fresh["value"]
        original_output = original_outputs.get(row["description"])
        if original_output is None:
            if new_value != old_value:
                raise ValueError(
                    "historical current inventory differs outside output Sync"
                )
        elif old_value == 1:
            if new_value not in (0, 1):
                raise ValueError("originally-on output Sync value is invalid")
        elif new_value != 0:
            raise ValueError("originally-off output Sync changed")
        expected[identity] = new_value
    output_rows = {row["description"]: row for row in original["outputs"]}
    if set(current_outputs) != set(output_rows):
        raise ValueError("historical current output Sync identities changed")
    for description, row in current_outputs.items():
        original_row = output_rows.get(description)
        if original_row is None or row["name"] != original_row["name"]:
            raise ValueError("historical current output Sync identities changed")
        allowed = (0, 1) if original_row["value"] == 1 else (0,)
        if row["value"] not in allowed:
            raise ValueError(
                "historical current output Sync value is outside restore states"
            )
        identity = row_identity_by_description(current, description)
        if after[identity]["value"] != row["value"]:
            raise ValueError(
                "historical current output Sync rows disagree with checkbox inventory"
            )
    return expected


def row_identity_by_description(inventory: dict, description: str) -> str:
    return next(
        row["identity"]
        for row in inventory["checkboxes"]
        if row["description"] == description
    )


def transition_plan(
    operation: str, original: dict[str, int], current: dict[str, int]
) -> list[dict]:
    if operation not in {"isolate", "restore"}:
        raise ValueError("operation must be isolate or restore")
    if original.keys() != current.keys():
        raise ValueError("current output identities differ from the baseline")
    if any(type(value) is not int or value not in (0, 1) for value in current.values()):
        raise ValueError("current output values must be exact 0/1 integers")
    actions = []
    for name in sorted(original):
        before = original[name]
        observed = current[name]
        if operation == "isolate":
            if observed != before:
                raise ValueError(
                    "current outputs changed from baseline before isolation"
                )
            desired = 0
        else:
            if observed not in ({0, before} if before else {0}):
                raise ValueError(
                    "current outputs are outside partial-isolation restore states"
                )
            desired = before
        if observed != desired:
            actions.append(
                {"name": name, "previous_value": observed, "desired_value": desired}
            )
    return actions


def _safe_new_path(path: Path, label: str) -> Path:
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise ValueError(f"{label} must be an absolute normalized path")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise ValueError(
            f"{label} parent path is unavailable: {path.parent}"
        ) from error
    if not parent.is_dir():
        raise ValueError(f"{label} parent path must be an existing directory")
    path = parent / path.name
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ValueError(f"{label} path cannot be inspected: {path}") from error
    else:
        raise ValueError(f"{label} already exists")
    return path


def _write_new_json(path: Path, report: dict) -> tuple[int, int]:
    path = _safe_new_path(path, "output")
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        details = path.lstat()
    except OSError as error:
        raise ValueError(f"cannot create output file {path}: {error}") from error
    return details.st_dev, details.st_ino


def _replace_owned_json(
    path: Path, identity: tuple[int, int], report: dict
) -> tuple[int, int]:
    try:
        current = path.lstat()
    except OSError as error:
        raise ValueError(f"reserved receipt disappeared: {path}") from error
    if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != identity:
        raise ValueError("reserved receipt identity changed during native action")
    fd, temporary = tempfile.mkstemp(prefix=".live-midi-sync-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        current = path.lstat()
        if (
            stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
        ):
            raise ValueError("reserved receipt identity changed before receipt update")
        os.replace(temporary, path)
        updated = path.lstat()
        return updated.st_dev, updated.st_ino
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _baseline_from_file(path: Path, **kwargs) -> tuple[dict, str]:
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise ValueError("baseline must be an absolute normalized path")
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ValueError("baseline must be a regular file, not a symlink")
        if path.stat().st_size > 1_000_000:
            raise ValueError("baseline exceeds the 1 MB safety bound")
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read valid baseline JSON: {path}") from error
    baseline = validate_baseline(value, **kwargs)
    return baseline, hashlib.sha256(raw).hexdigest()


def _initial_receipt(operation: str, baseline: dict, baseline_hash: str) -> dict:
    actions = []
    for row in baseline["outputs"]:
        could_change = row["value"] == 1
        actions.append(
            {
                "name": row["name"],
                "description": row["description"],
                "baseline_value": row["value"],
                "desired_value": 0 if operation == "isolate" else row["value"],
                "attempted": None if could_change else False,
                "outcome": "unknown" if could_change else "not_attempted",
                "actual_value": None,
            }
        )
    return {
        "format": RECEIPT_FORMAT,
        "status": "pending",
        "phase": "native-helper-pending",
        "operation": operation,
        "read_only": False,
        "target": baseline["target"],
        "baseline_sha256": baseline_hash,
        "expected_output_count": baseline["expected_output_count"],
        "actions": actions,
        "manual_inspection_required": any(
            row["outcome"] == "unknown" for row in actions
        ),
        "error": "native helper has not produced a final receipt; affected outputs are uncertain",
    }


def _focus_fact_valid(
    fact: object,
    *,
    value_type: type,
    unavailable_reasons: set[str],
    error_reasons: set[str] | frozenset[str] = frozenset(),
) -> bool:
    if not isinstance(fact, dict) or not isinstance(fact.get("status"), str):
        return False
    if fact["status"] == "observed":
        value = fact.get("value")
        if set(fact) != {"status", "value"}:
            return False
        if value_type is int:
            return type(value) is int and 1 <= value <= 2_147_483_647
        if value_type is bool:
            return type(value) is bool
        if value_type is str:
            return isinstance(value, str) and 0 < len(value) <= 128
        return False
    if fact["status"] == "unavailable":
        reason = fact.get("reason")
        return (
            set(fact) == {"status", "reason"}
            and isinstance(reason, str)
            and reason in unavailable_reasons
        )
    if fact["status"] == "error":
        if set(fact) == {"status", "error_code"}:
            return type(fact.get("error_code")) is int and fact["error_code"] != 0
        reason = fact.get("reason")
        return (
            set(fact) == {"status", "reason"}
            and isinstance(reason, str)
            and reason in error_reasons
        )
    return False


def _focus_diagnostics_valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "format",
        "captured_after_guard_refusal",
        "process_is_active",
        "frontmost_pid",
        "focused_window",
    }:
        return False
    if (
        value.get("format") != FOCUS_DIAGNOSTICS_FORMAT
        or value.get("captured_after_guard_refusal") is not True
        or not _focus_fact_valid(
            value.get("process_is_active"),
            value_type=bool,
            unavailable_reasons={"process_unavailable", "process_identity_changed"},
        )
        or not _focus_fact_valid(
            value.get("frontmost_pid"),
            value_type=int,
            unavailable_reasons={"no_frontmost_application"},
        )
    ):
        return False

    focused = value.get("focused_window")
    if not isinstance(focused, dict):
        return False
    focused_status = focused.get("status")
    if not isinstance(focused_status, str) or focused_status not in {
        "observed",
        "unavailable",
        "error",
    }:
        return False
    window_fact_options = {
        "value_type": str,
        "unavailable_reasons": {
            "focused_window_unavailable",
            "no_value",
            "attribute_unsupported",
        },
        "error_reasons": {"unexpected_type", "value_out_of_bounds"},
    }
    match_fact_options = {
        "value_type": bool,
        "unavailable_reasons": {"focused_window_unavailable"},
    }
    if focused_status == "observed":
        return (
            set(focused) == {"status", "role", "title", "matches_settings"}
            and _focus_fact_valid(focused.get("role"), **window_fact_options)
            and _focus_fact_valid(focused.get("title"), **window_fact_options)
            and _focus_fact_valid(focused.get("matches_settings"), **match_fact_options)
        )
    if focused_status == "unavailable":
        reason = focused.get("reason")
        if (
            set(focused)
            != {
                "status",
                "reason",
                "role",
                "title",
                "matches_settings",
            }
            or not isinstance(reason, str)
            or reason
            not in {
                "no_value",
                "attribute_unsupported",
            }
        ):
            return False
        unavailable = {"status": "unavailable", "reason": "focused_window_unavailable"}
        return all(
            focused.get(name) == unavailable
            for name in ("role", "title", "matches_settings")
        )
    if set(focused) == {"status", "error_code", "role", "title", "matches_settings"}:
        code = focused.get("error_code")
        return (
            type(code) is int
            and code != 0
            and focused.get("role") == {"status": "error", "error_code": code}
            and focused.get("title") == {"status": "error", "error_code": code}
            and focused.get("matches_settings")
            == {"status": "unavailable", "reason": "focused_window_unavailable"}
        )
    if set(focused) == {"status", "reason", "role", "title", "matches_settings"}:
        return (
            focused.get("reason") == "unexpected_type"
            and focused.get("role") == {"status": "error", "reason": "unexpected_type"}
            and focused.get("title") == {"status": "error", "reason": "unexpected_type"}
            and focused.get("matches_settings")
            == {"status": "error", "reason": "unexpected_type"}
        )
    return False


def _native_receipt_valid(
    report: object,
    *,
    operation: str,
    baseline: dict,
    baseline_hash: str,
    returncode: int,
    current_hash: str | None = None,
) -> bool:
    if not isinstance(report, dict):
        return False
    target = report.get("target")
    status = report.get("status")
    actions = report.get("actions")
    if (
        report.get("format") != RECEIPT_FORMAT
        or report.get("operation") != operation
        or not isinstance(target, dict)
        or target.get("pid") != baseline["target"]["pid"]
        or target.get("bundle_identifier") != baseline["target"]["bundle_identifier"]
        or target.get("process_name") != baseline["target"]["process_name"]
        or report.get("baseline_sha256") != baseline_hash
        or (
            current_hash is not None
            and report.get("current_observation_sha256") != current_hash
        )
        or (current_hash is None and "current_observation_sha256" in report)
        or (current_hash is not None and report.get("scope") != "historical-restore")
        or (current_hash is None and "scope" in report)
        or report.get("expected_output_count") != baseline["expected_output_count"]
        or status not in {"complete", "stopped"}
        or not isinstance(actions, list)
        or len(actions) != baseline["expected_output_count"]
        or (status == "complete" and returncode != 0)
        or (status == "stopped" and returncode != 1)
        or not isinstance(report.get("phase"), str)
        or report.get("read_only") is not False
        or type(report.get("manual_inspection_required")) is not bool
    ):
        return False
    if "focus_diagnostics" in report and (
        status != "stopped"
        or report.get("phase") not in {"preflight", "actions"}
        or not _focus_diagnostics_valid(report["focus_diagnostics"])
    ):
        return False
    original = {row["name"]: row["value"] for row in baseline["outputs"]}
    descriptions = {row["name"]: row["description"] for row in baseline["outputs"]}
    indexed = {}
    for row in actions:
        if not isinstance(row, dict):
            return False
        name = row.get("name")
        attempted = row.get("attempted")
        outcome = row.get("outcome")
        actual = row.get("actual_value")
        expected = 0 if operation == "isolate" else original.get(name)
        if (
            name not in original
            or name in indexed
            or row.get("description") != descriptions[name]
            or row.get("baseline_value") != original[name]
            or row.get("desired_value") != expected
            or type(attempted) is not bool
            or outcome not in {"not_attempted", "known", "unknown"}
            or (attempted is False and outcome != "not_attempted")
            or (attempted is True and outcome == "not_attempted")
            or (
                outcome == "known" and (type(actual) is not int or actual not in (0, 1))
            )
            or (outcome == "unknown" and actual is not None)
            or (
                outcome == "not_attempted"
                and actual is not None
                and (type(actual) is not int or actual not in (0, 1))
            )
            or (
                attempted is True
                and (
                    type(row.get("prepress_elements")) is not int
                    or not 1 <= row["prepress_elements"] <= MAX_ELEMENTS
                    or type(row.get("prepress_windows")) is not int
                    or not 1 <= row["prepress_windows"] <= MAX_WINDOWS
                )
            )
        ):
            return False
        indexed[name] = row
    if set(indexed) != set(original):
        return False
    has_uncertainty = any(row["outcome"] == "unknown" for row in indexed.values())
    has_attempt = any(row["attempted"] for row in indexed.values())
    if report["manual_inspection_required"] != (
        has_uncertainty or (status == "stopped" and has_attempt)
    ):
        return False
    if status == "complete":
        values = report.get("observed_values")
        expected_values = (
            {name: 0 for name in original} if operation == "isolate" else original
        )
        if (
            report["manual_inspection_required"]
            or report.get("settings_window") != {"title": "Settings"}
            or any(
                type(report.get(field)) is not int or not 1 <= report[field] <= limit
                for field, limit in (
                    ("observed_elements_before", MAX_ELEMENTS),
                    ("observed_elements_after", MAX_ELEMENTS),
                    ("observed_windows_before", MAX_WINDOWS),
                    ("observed_windows_after", MAX_WINDOWS),
                )
            )
            or not isinstance(values, dict)
            or values != expected_values
            or any(row["outcome"] == "unknown" for row in indexed.values())
            or any(
                row["outcome"] == "known"
                and row["actual_value"] != row["desired_value"]
                for row in indexed.values()
            )
            or any(
                row["outcome"] == "not_attempted"
                and row["actual_value"] != row["desired_value"]
                for row in indexed.values()
            )
        ):
            return False
    return True


def run_live_midi_sync(
    pid: int,
    *,
    expected_bundle_id: str,
    expected_count: int,
    action: str | None = None,
    baseline_path: Path | None = None,
    output_path: Path | None = None,
    execute: bool = False,
    max_baseline_age_seconds: float = 300.0,
    historical_current_path: Path | None = None,
) -> dict:
    if platform.system() != "Darwin":
        raise ValueError("native Live MIDI Sync controls require macOS")
    if type(pid) is not int or not 1 <= pid <= 2_147_483_647:
        raise ValueError("PID must be a positive 32-bit integer")
    if not isinstance(expected_bundle_id, str) or not expected_bundle_id.strip():
        raise ValueError("expected bundle identifier is required")
    if type(expected_count) is not int or not 1 <= expected_count <= 128:
        raise ValueError("expected output count must be between 1 and 128")
    if not execute:
        if (
            action is not None
            or baseline_path is not None
            or historical_current_path is not None
        ):
            raise ValueError(
                "--action, --baseline and --historical-current-inventory require explicit --execute"
            )
        if output_path is not None:
            _safe_new_path(output_path, "baseline output")
        from .ax_snapshot import snapshot

        observed = snapshot(
            pid,
            window_title="Settings",
            max_elements=MAX_ELEMENTS,
            max_depth=MAX_DEPTH,
        )
        baseline = baseline_from_snapshot(
            observed,
            expected_pid=pid,
            expected_bundle_id=expected_bundle_id,
            expected_count=expected_count,
        )
        if output_path is not None:
            _write_new_json(output_path, baseline)
        return baseline

    if action not in {"isolate", "restore"}:
        raise ValueError("explicit --execute requires --action isolate or restore")
    if baseline_path is None or output_path is None:
        raise ValueError(
            "explicit --execute requires --baseline and a new --receipt path"
        )
    if historical_current_path is not None and action != "restore":
        raise ValueError("--historical-current-inventory is valid only for restore")
    if (
        isinstance(max_baseline_age_seconds, bool)
        or not isinstance(max_baseline_age_seconds, (int, float))
        or not math.isfinite(max_baseline_age_seconds)
        or max_baseline_age_seconds <= 0
        or max_baseline_age_seconds > MAX_BASELINE_AGE_SECONDS
    ):
        raise ValueError(
            "maximum baseline age must be positive and at most 300 seconds"
        )
    preliminary = {
        "format": RECEIPT_FORMAT,
        "status": "stopped",
        "phase": "baseline-validation",
        "operation": action,
        "read_only": False,
        "target": {"pid": pid, "bundle_identifier": expected_bundle_id},
        "expected_output_count": expected_count,
        "actions": [],
        "manual_inspection_required": False,
        "error": "baseline has not been validated; no native action was attempted",
    }
    identity = _write_new_json(output_path, preliminary)
    try:
        baseline, baseline_hash = _baseline_from_file(
            baseline_path,
            expected_pid=pid,
            expected_bundle_id=expected_bundle_id,
            expected_count=expected_count,
            max_age_seconds=max_baseline_age_seconds,
            allow_stale=historical_current_path is not None,
        )
    except ValueError as error:
        preliminary["error"] = str(error)
        _replace_owned_json(output_path, identity, preliminary)
        return preliminary
    historical_current = None
    historical_hash = None
    if historical_current_path is not None:
        try:
            historical_current, historical_hash = _baseline_from_file(
                historical_current_path,
                expected_pid=pid,
                expected_bundle_id=expected_bundle_id,
                expected_count=expected_count,
                max_age_seconds=max_baseline_age_seconds,
            )
            if historical_current["format"] != FORMAT:
                raise ValueError(
                    "historical restore requires a complete v2 current inventory"
                )
            historical_restore_expected(baseline, historical_current)
        except ValueError as error:
            preliminary["error"] = str(error)
            _replace_owned_json(output_path, identity, preliminary)
            return preliminary
    pending = _initial_receipt(action, baseline, baseline_hash)
    if historical_hash is not None:
        pending.update(
            scope="historical-restore",
            current_observation_sha256=historical_hash,
        )
    identity = _replace_owned_json(output_path, identity, pending)
    helper = Path(__file__).with_name("live_midi_sync.swift")
    command = [
        "/usr/bin/swift",
        str(helper),
        "--pid",
        str(pid),
        "--bundle-id",
        expected_bundle_id,
        "--expected-count",
        str(expected_count),
        "--operation",
        action,
        "--baseline",
        str(baseline_path),
        "--baseline-sha256",
        baseline_hash,
        "--max-baseline-age-seconds",
        str(max_baseline_age_seconds),
    ]
    if historical_current_path is not None and historical_hash is not None:
        command.extend(
            (
                "--historical-current-inventory",
                str(historical_current_path),
                "--historical-current-sha256",
                historical_hash,
            )
        )
        try:
            _, final_baseline_hash = _baseline_from_file(
                baseline_path,
                expected_pid=pid,
                expected_bundle_id=expected_bundle_id,
                expected_count=expected_count,
                max_age_seconds=max_baseline_age_seconds,
                allow_stale=True,
            )
            final_current, final_current_hash = _baseline_from_file(
                historical_current_path,
                expected_pid=pid,
                expected_bundle_id=expected_bundle_id,
                expected_count=expected_count,
                max_age_seconds=max_baseline_age_seconds,
            )
            if final_baseline_hash != baseline_hash:
                raise ValueError("original baseline changed after wrapper validation")
            if final_current_hash != historical_hash:
                raise ValueError(
                    "historical current inventory changed after wrapper validation"
                )
            historical_restore_expected(baseline, final_current)
        except ValueError as error:
            pending.update(
                status="stopped",
                phase="historical-current-revalidation",
                actions=[
                    row
                    | {
                        "attempted": False,
                        "outcome": "not_attempted",
                        "actual_value": None,
                    }
                    for row in pending["actions"]
                ],
                manual_inspection_required=False,
                error=str(error),
            )
            _replace_owned_json(output_path, identity, pending)
            return pending
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=NATIVE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pending["status"] = "stopped"
        pending["phase"] = "native-helper-timeout"
        pending["error"] = (
            "native helper timed out; inspect every originally enabled output before retry"
        )
        pending["manual_inspection_required"] = True
        _replace_owned_json(output_path, identity, pending)
        return pending
    except (OSError, subprocess.SubprocessError) as error:
        pending["status"] = "stopped"
        pending["phase"] = "native-helper-launch"
        pending["error"] = f"native helper launch failed: {error}"
        pending["actions"] = [
            row | {"attempted": False, "outcome": "not_attempted"}
            for row in pending["actions"]
        ]
        pending["manual_inspection_required"] = False
        _replace_owned_json(output_path, identity, pending)
        return pending
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        pending["status"] = "stopped"
        pending["phase"] = "native-helper-invalid-output"
        pending["error"] = (
            result.stderr.strip()[-2000:] or "native helper returned invalid JSON"
        )
        pending["manual_inspection_required"] = True
        _replace_owned_json(output_path, identity, pending)
        return pending
    if not _native_receipt_valid(
        report,
        operation=action,
        baseline=baseline,
        baseline_hash=baseline_hash,
        returncode=result.returncode,
        current_hash=historical_hash,
    ):
        pending["status"] = "stopped"
        pending["phase"] = "native-helper-invalid-receipt"
        pending["error"] = (
            "native helper returned an invalid receipt; action outcomes are unknown"
        )
        pending["manual_inspection_required"] = True
        _replace_owned_json(output_path, identity, pending)
        return pending
    _replace_owned_json(output_path, identity, report)
    return report
