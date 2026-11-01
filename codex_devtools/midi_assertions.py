"""Offline summaries and expectation checks for parsed MIDI events."""

from __future__ import annotations

import hashlib
import itertools
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import fmean
from typing import Any

_SELECTORS = {"type", "channel", "controller", "value", "note", "raw", "origin"}
_EXPECTATION_KEYS = _SELECTORS | {"min_count", "max_count"}


def _origin(event: Mapping[str, Any]) -> str:
    origin = event.get("origin")
    return origin if isinstance(origin, str) and origin else "(unknown)"


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def analyze(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize parsed events without inferring tempo or timestamp units."""
    counts: Counter[str] = Counter()
    origin_counts: Counter[str] = Counter()
    invalids: list[dict[str, Any]] = []
    note_on_count = note_off_count = unmatched_note_off_count = 0
    active_notes: Counter[tuple[str, int, int]] = Counter()
    cc_stats: dict[tuple[str, int, int], dict[str, int | str]] = {}
    unknown_units: Counter[str] = Counter()
    events_without_timestamp = 0
    clocks: dict[str, list[float]] = defaultdict(list)
    ignored_clock_units: Counter[tuple[str, str]] = Counter()

    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ValueError(f"event at index {index} must be an object")  # noqa: TRY004 -- retain the parser's ValueError contract
        event_type = event.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError(f"event at index {index} has no valid type")
        origin = _origin(event)
        counts[event_type] += 1
        origin_counts[origin] += 1
        if event_type == "invalid":
            invalids.append({"index": index, "raw": event.get("raw"), "origin": origin})

        note_event = event_type in ("note_on", "note_off")
        if note_event and all(
            _integer(event.get(key)) for key in ("channel", "note", "value")
        ):
            channel, note, velocity = event["channel"], event["note"], event["value"]
            is_on = event_type == "note_on" and velocity != 0
            if is_on:
                note_on_count += 1
                active_notes[(origin, channel, note)] += 1
            else:
                note_off_count += 1
                key = (origin, channel, note)
                if active_notes[key] > 0:
                    active_notes[key] -= 1
                    if active_notes[key] == 0:
                        del active_notes[key]
                else:
                    unmatched_note_off_count += 1

        if event_type == "control_change" and all(
            _integer(event.get(key)) for key in ("channel", "controller", "value")
        ):
            key = (origin, event["channel"], event["controller"])
            value = event["value"]
            if key not in cc_stats:
                cc_stats[key] = {
                    "origin": origin,
                    "channel": key[1],
                    "controller": key[2],
                    "count": 0,
                    "min": value,
                    "max": value,
                }
            stats = cc_stats[key]
            stats["count"] = int(stats["count"]) + 1
            stats["min"] = min(int(stats["min"]), value)
            stats["max"] = max(int(stats["max"]), value)

        timestamp = event.get("timestamp")
        unit = event.get("timestamp_unit")
        if timestamp is None:
            events_without_timestamp += 1
        elif unit != "reference_seconds":
            unknown_units[str(unit) if unit is not None else "(missing)"] += 1

        if event_type == "clock":
            if (
                unit == "reference_seconds"
                and isinstance(timestamp, (int, float))
                and not isinstance(timestamp, bool)
            ):
                clocks[origin].append(float(timestamp))
            else:
                ignored_clock_units[
                    (origin, str(unit) if unit is not None else "(missing)")
                ] += 1

    clock_intervals: dict[str, dict[str, Any]] = {}
    for origin in sorted(set(clocks) | {origin for origin, _ in ignored_clock_units}):
        times = sorted(clocks.get(origin, []))
        intervals = [later - earlier for earlier, later in itertools.pairwise(times)]
        clock_intervals[origin] = {
            "timestamp_count": len(times),
            "interval_count": len(intervals),
            "min_seconds": min(intervals) if intervals else None,
            "mean_seconds": fmean(intervals) if intervals else None,
            "max_seconds": max(intervals) if intervals else None,
            "ignored_timestamp_units": {
                unit: count
                for (source, unit), count in sorted(ignored_clock_units.items())
                if source == origin
            },
        }

    balances = [
        {"origin": origin, "channel": channel, "note": note, "active_count": count}
        for (origin, channel, note), count in sorted(active_notes.items())
    ]
    return {
        "event_counts": dict(sorted(counts.items())),
        "by_origin": dict(sorted(origin_counts.items())),
        "invalids": {"count": len(invalids), "events": invalids},
        "note_balance": {
            "note_on_count": note_on_count,
            "note_off_count": note_off_count,
            "net_count": note_on_count - note_off_count,
            "unmatched_note_off_count": unmatched_note_off_count,
            "active_notes": balances,
        },
        "cc_ranges": [cc_stats[key] for key in sorted(cc_stats)],
        "clock_intervals": clock_intervals,
        "unknown_timestamp_units": dict(sorted(unknown_units.items())),
        "events_without_timestamp": events_without_timestamp,
    }


def _validate_expectations(expectations: Any) -> list[dict[str, Any]]:
    if not isinstance(expectations, Mapping):
        raise ValueError("expectations must be an object with an 'expectations' array")  # noqa: TRY004 -- retain the CLI's ValueError contract
    unknown_top = set(expectations) - {"expectations"}
    if unknown_top:
        raise ValueError(
            f"unknown expectation document keys: {', '.join(sorted(map(str, unknown_top)))}"
        )
    entries = expectations.get("expectations")
    if not isinstance(entries, list):
        raise ValueError("expectations must contain an array named 'expectations'")  # noqa: TRY004 -- retain the CLI's ValueError contract
    if not entries:
        raise ValueError(
            "expectations must contain at least one explicit success condition"
        )
    validated = []
    for index, entry in enumerate(entries):
        label = f"expectations[{index}]"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{label} must be an object")  # noqa: TRY004 -- retain the CLI's ValueError contract
        unknown = set(entry) - _EXPECTATION_KEYS
        if unknown:
            raise ValueError(
                f"{label} has unknown keys: {', '.join(sorted(map(str, unknown)))}"
            )
        selectors = set(entry) & _SELECTORS
        if not selectors:
            raise ValueError(f"{label} needs at least one event selector")
        if not ({"min_count", "max_count"} & set(entry)):
            raise ValueError(f"{label} needs min_count or max_count")
        if "type" in entry and (
            not isinstance(entry["type"], str) or not entry["type"]
        ):
            raise ValueError(f"{label}.type must be a non-empty string")
        for key in ("raw", "origin"):
            if key in entry and (not isinstance(entry[key], str) or not entry[key]):
                raise ValueError(f"{label}.{key} must be a non-empty string")
        if "channel" in entry and (
            not _integer(entry["channel"]) or not 1 <= entry["channel"] <= 16
        ):
            raise ValueError(f"{label}.channel must be an integer from 1 to 16")
        for key in ("controller", "note"):
            if key in entry and (
                not _integer(entry[key]) or not 0 <= entry[key] <= 127
            ):
                raise ValueError(f"{label}.{key} must be an integer from 0 to 127")
        if "value" in entry and not _integer(entry["value"]):
            raise ValueError(f"{label}.value must be an integer")
        for key in ("min_count", "max_count"):
            if key in entry and (not _integer(entry[key]) or entry[key] < 0):
                raise ValueError(f"{label}.{key} must be a non-negative integer")
        if (
            "min_count" in entry
            and "max_count" in entry
            and entry["min_count"] > entry["max_count"]
        ):
            raise ValueError(f"{label}.min_count cannot exceed max_count")
        validated.append(dict(entry))
    return validated


def check_expectations(
    events: Sequence[Mapping[str, Any]], expectations: Any
) -> dict[str, Any]:
    """Match event-subset expectations and return count plus pass/fail per rule.

    Expectation documents have the form ``{"expectations": [{...}]}``. Each
    rule has one or more event fields as exact-match selectors and at least one
    of ``min_count`` or ``max_count``.
    """
    rules = _validate_expectations(expectations)
    checks = []
    for index, rule in enumerate(rules):
        selectors = {key: value for key, value in rule.items() if key in _SELECTORS}
        count = sum(
            1
            for event in events
            if isinstance(event, Mapping)
            and all(event.get(key) == value for key, value in selectors.items())
        )
        passed = count >= rule.get("min_count", 0) and count <= rule.get(
            "max_count", float("inf")
        )
        checks.append(
            {
                "index": index,
                "match": selectors,
                "count": count,
                "min_count": rule.get("min_count"),
                "max_count": rule.get("max_count"),
                "passed": passed,
            }
        )
    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def verify_capture_receipt(
    document_path: str | Path, document: Mapping[str, Any], receipt_path: str | Path
) -> dict[str, Any]:
    """Verify that a saved capture and its receipt describe the same exact run.

    Receipt verification is intentionally offline. It checks the source identity
    before/after resolution and the saved .mmon bytes; it does not establish that
    captured events meet behavioral expectations.
    """
    import json

    path = Path(document_path).resolve()
    receipt_file = Path(receipt_path)
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    if not isinstance(receipt, Mapping):
        raise ValueError("capture receipt must be a JSON object")  # noqa: TRY004 -- retain the CLI's ValueError contract
    if receipt.get("schema") != "devtools-midi-capture-v1":
        raise ValueError("unsupported MIDI capture receipt schema")
    if receipt.get("status") not in ("captured", "inconclusive"):
        raise ValueError(
            f"capture receipt status is not usable: {receipt.get('status')!r}"
        )
    capture_file = receipt.get("capture_file")
    if (
        not isinstance(capture_file, str)
        or not capture_file
        or Path(capture_file).name != capture_file
    ):
        raise ValueError("capture receipt has an invalid capture_file")
    if path != (receipt_file.resolve().parent / capture_file).resolve():
        raise ValueError("capture document path does not match receipt capture_file")

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if document.get("document_sha256") != digest:
        raise ValueError("capture document changed after parsing")
    if receipt.get("capture_sha256") != digest:
        raise ValueError("capture document SHA-256 does not match receipt")
    settings = document.get("settings")
    if not isinstance(settings, Mapping):
        raise ValueError("capture document has no settings")  # noqa: TRY004 -- retain the CLI's ValueError contract
    current = settings.get("streamSettings")
    configured = receipt.get("configured_settings")
    resolved = receipt.get("resolved_settings")
    if not all(isinstance(item, Mapping) for item in (current, configured, resolved)):
        raise ValueError("receipt or capture document is missing source settings")

    # Import lazily: this helper is also used by the strictly offline CLI.
    from .midi_capture import source_identity

    configured_sources = source_identity(configured.get("streamSettings", {}))
    resolved_sources = source_identity(resolved.get("streamSettings", {}))
    if not configured_sources:
        raise ValueError("receipt contains no explicitly configured input source")
    if configured_sources != resolved_sources:
        raise ValueError(
            "capture source identity differs from configured/resolved receipt sources"
        )
    saved_sources = source_identity(current, require_source=False)
    if "portInputStream" not in saved_sources:
        raise ValueError("capture document has no portInputStream setting")
    if any(saved_sources.values()) and saved_sources != configured_sources:
        raise ValueError(
            "capture source identity differs from configured/resolved receipt sources"
        )
    events = document.get("events")
    if not isinstance(events, list) or receipt.get("event_count") != len(events):
        raise ValueError("capture event count does not match receipt")

    return {
        "verified": True,
        "status": receipt["status"],
        "capture_file": capture_file,
        "capture_sha256": digest,
        "source_identity": configured_sources,
        "event_count": len(events),
        "template_sha256": receipt.get("template_sha256"),
        "adapter_sha256": receipt.get("adapter_sha256"),
        "midi_monitor_version": receipt.get("midi_monitor_version"),
    }
