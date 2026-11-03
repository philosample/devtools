"""Explicit, isolated GUI capture adapter; offline inspection has no UI effects."""

from __future__ import annotations

import hashlib
import json
import math
import os
import plistlib
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .midi_monitor import parse_document, write_csv, write_json

_CAPTURE_SAVE_TIMEOUT_SECONDS = 30.0
_CAPTURE_SAVE_STABLE_SECONDS = 0.5
_CAPTURE_SAVE_POLL_SECONDS = 0.1
_READY_MARKER_NAME = "capture-ready.json"


def source_identity(settings: dict, *, require_source: bool = True) -> dict:
    """Return validated endpoint IDs, requiring an input source by default.

    Saved captures can use ``require_source=False`` after MIDI Monitor
    disconnects every stream before saving; all stream entries are still
    validated, including endpoint shapes and duplicate IDs.
    """
    # Snoize rewrites saved display names on load and adds empty stream arrays.
    if not isinstance(settings, dict):
        raise ValueError("streamSettings must be a dictionary")  # noqa: TRY004 -- preserve source_identity's ValueError contract
    result = {}
    for key, value in settings.items():
        if not isinstance(key, str) or not isinstance(value, list):
            raise ValueError("source stream settings must be lists of endpoint objects")  # noqa: TRY004 -- preserve source_identity's ValueError contract
        if any(
            not isinstance(source, dict)
            or not isinstance(source.get("uniqueID"), int)
            or isinstance(source.get("uniqueID"), bool)
            for source in value
        ):
            raise ValueError("source entries must contain integer uniqueID values")
        identifiers = [source["uniqueID"] for source in value]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError(f"{key} contains duplicate endpoint uniqueID values")
        if identifiers or key == "portInputStream":
            result[key] = sorted(identifiers)
    if require_source and not result.get("portInputStream"):
        raise ValueError(
            "streamSettings must contain at least one valid portInputStream source"
        )
    return result


def endpoints() -> list:
    if sys.platform != "darwin":
        raise ValueError("CoreMIDI inventory requires macOS")
    script = Path(__file__).with_name("midi_endpoints.swift")
    result = subprocess.run(
        ["/usr/bin/swift", str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        raise ValueError(
            "CoreMIDI inventory failed (Swift developer tools required): "
            + result.stderr.strip()
        )
    return json.loads(result.stdout)


def make_template(destination: Path, source_name: str) -> dict:
    """Resolve exactly one online CoreMIDI input endpoint; never invent IDs."""
    if destination.exists():
        raise ValueError("template output already exists")
    matches = [
        s
        for s in endpoints()
        if s["direction"] == "source"
        and not s["offline"]
        and source_name in (s["name"], s["displayName"])
    ]
    if len(matches) != 1:
        raise ValueError(
            "source name must match exactly one online MIDI input endpoint"
        )
    source = {"uniqueID": matches[0]["uniqueID"], "name": matches[0]["displayName"]}
    document = {
        "version": 2,
        "streamSettings": {"portInputStream": [source]},
        "filterMask": 0xFFFFFFFF,
        "areSourcesShown": True,
        "isFilterShown": True,
    }
    with destination.open("xb") as f:
        plistlib.dump(document, f)
    return {"source": source, "template": str(destination)}


def _saved_stream_settings(parsed: dict) -> dict:
    settings = parsed.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("capture settings must be a dictionary")  # noqa: TRY004 -- malformed saved settings are capture validation failures
    streams = settings.get("streamSettings")
    if not isinstance(streams, dict):
        raise ValueError("capture streamSettings must be a dictionary")  # noqa: TRY004 -- malformed saved settings are capture validation failures
    if "portInputStream" not in streams or not isinstance(
        streams["portInputStream"], list
    ):
        raise ValueError("capture streamSettings must contain a portInputStream list")
    source_identity(streams, require_source=False)
    return streams


def _wait_for_final_capture(
    document: Path,
    *,
    timeout: float = _CAPTURE_SAVE_TIMEOUT_SECONDS,
    stable_for: float = _CAPTURE_SAVE_STABLE_SECONDS,
    poll_interval: float = _CAPTURE_SAVE_POLL_SECONDS,
) -> tuple[dict, str]:
    """Wait for MIDI Monitor's final save to be parseable and stable.

    A saved document with connected streams is not accepted, even if its bytes
    are otherwise parseable: that can be the pre-disconnect version still on
    disk while MIDI Monitor finishes saving the final document.
    """
    deadline = time.monotonic() + timeout
    candidate_hash = None
    stable_since = None
    last_error = None
    saw_connected = False
    while True:
        try:
            parsed = parse_document(document)
            current_hash = hashlib.sha256(document.read_bytes()).hexdigest()
            if parsed["document_sha256"] != current_hash:
                raise ValueError("capture changed while it was being parsed")
            streams = _saved_stream_settings(parsed)
            if any(streams.values()):
                saw_connected = True
                candidate_hash = None
                stable_since = None
            elif current_hash != candidate_hash:
                candidate_hash = current_hash
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= stable_for:
                # Re-parse at the end of the stability window and return the
                # exact digest associated with that parse.
                final = parse_document(document)
                final_hash = hashlib.sha256(document.read_bytes()).hexdigest()
                final_streams = _saved_stream_settings(final)
                if final["document_sha256"] == final_hash == candidate_hash and not any(
                    final_streams.values()
                ):
                    return final, final_hash
                candidate_hash = None
                stable_since = None
        except (OSError, ValueError) as exc:
            last_error = exc
            candidate_hash = None
            stable_since = None

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if saw_connected:
                reason = "capture inputs were not disconnected before save settled"
            elif last_error:
                reason = f"capture did not become parseable and stable: {last_error}"
            else:
                reason = "capture did not become parseable and stable before timeout"
            raise ValueError(reason)
        time.sleep(min(poll_interval, remaining))


def _validate_ready_marker(
    marker_path: Path,
    expected: dict,
    capture_process,
    preflight_path: Path,
    configured_source_identity: dict,
) -> dict:
    """Validate the atomic post-Clear marker against this live capture run."""
    if marker_path.is_symlink():
        raise ValueError("capture-ready marker must not be a symlink")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("capture-ready marker is missing or incomplete") from exc
    if marker != expected:
        raise ValueError("capture-ready marker does not match this run")
    if capture_process.poll() is not None:
        raise ValueError("capture process exited before readiness was validated")
    preflight = parse_document(preflight_path)
    preflight_hash = hashlib.sha256(preflight_path.read_bytes()).hexdigest()
    if preflight["document_sha256"] != preflight_hash:
        raise ValueError("preflight document changed while it was being validated")
    resolved_settings = preflight["settings"].get("streamSettings", {})
    if source_identity(resolved_settings) != configured_source_identity:
        raise ValueError(
            "ready marker preflight sources differ from configured sources"
        )
    if capture_process.poll() is not None:
        raise ValueError("capture process exited during readiness validation")
    return {
        "schema": "devtools-midi-capture-ready-v1",
        "marker_file": marker_path.name,
        "run_id": expected["run_id"],
        "capture_process_pid": expected["capture_process_pid"],
        "automation_process_pid": capture_process.pid,
        "preflight_sha256": preflight_hash,
        "source_identity": configured_source_identity,
    }


def _wait_for_ready_marker(
    marker_path: Path,
    expected: dict,
    capture_process,
    preflight_path: Path,
    configured_source_identity: dict,
    *,
    timeout: float,
    poll_interval: float = 0.02,
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        if marker_path.exists():
            return _validate_ready_marker(
                marker_path,
                expected,
                capture_process,
                preflight_path,
                configured_source_identity,
            )
        if capture_process.poll() is not None:
            raise ValueError("capture process exited before capture-ready marker")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("capture-ready marker was not written before timeout")
        time.sleep(min(poll_interval, remaining))


def capture(
    template: Path, out: Path, seconds: float, max_events: int = 100000
) -> dict:
    if sys.platform != "darwin":
        raise ValueError("GUI capture requires macOS")
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise ValueError("seconds must be finite and between 0 and 3600")
    if not 1 <= max_events <= 1000000:
        raise ValueError("max-events must be between 1 and 1000000")
    original = parse_document(template)
    settings = original["settings"]
    if not source_identity(settings.get("streamSettings", {})):
        raise ValueError("template must have explicitly selected input sources")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    name = "capture-" + out.name + ".mmon"
    document = out / name
    data = plistlib.loads(template.read_bytes())
    data.pop("messageData", None)
    document.write_bytes(plistlib.dumps(data))
    script = Path(__file__).with_name("midi_capture.applescript")
    receipt = {
        "schema": "devtools-midi-capture-v1",
        "status": "running",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "requested_seconds": seconds,
        "max_events": max_events,
        "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
        "adapter_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "configured_settings": settings,
        "configured_source_identity": source_identity(
            settings.get("streamSettings", {})
        ),
        "capture_file": name,
        "run_id": str(uuid.uuid4()),
        "timing_note": "UI-controlled capture; disconnecting inputs adds boundary uncertainty",
    }
    info = Path("/Applications/MIDI Monitor.app/Contents/Info.plist")
    if info.exists():
        receipt["midi_monitor_version"] = plistlib.loads(info.read_bytes()).get(
            "CFBundleShortVersionString"
        )
    receipt_path = out / "receipt.json"
    marker_path = out / _READY_MARKER_NAME
    capture_process_pid = os.getpid()
    marker = {
        "schema": "devtools-midi-capture-ready-v1",
        "run_id": receipt["run_id"],
        "capture_process_pid": capture_process_pid,
        "capture_file": name,
        "capture_document_url": document.as_uri(),
        "template_sha256": receipt["template_sha256"],
        "configured_source_identity": receipt["configured_source_identity"],
    }
    receipt["capture_process_pid"] = capture_process_pid
    receipt["ready_marker_file"] = marker_path.name

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    save()
    started = time.monotonic()
    process = None
    try:
        process = subprocess.Popen(
            [
                "/usr/bin/osascript",
                str(script),
                str(document),
                str(out / "preflight.mmon"),
                name,
                str(seconds),
                str(max_events),
                document.as_uri(),
                str(marker_path),
                json.dumps(marker, separators=(",", ":")),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        capture_deadline = started + seconds + 60
        ready_receipt = _wait_for_ready_marker(
            marker_path,
            marker,
            process,
            out / "preflight.mmon",
            receipt["configured_source_identity"],
            timeout=max(0, capture_deadline - time.monotonic()),
        )
        ready_receipt["automation_process_pid"] = process.pid
        receipt["ready"] = ready_receipt
        save()
        try:
            stdout, stderr = process.communicate(
                timeout=max(0, capture_deadline - time.monotonic())
            )
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            raise
        result = subprocess.CompletedProcess(
            process.args, process.returncode, stdout, stderr
        )
        receipt["adapter_stdout"] = result.stdout.strip()
        if result.returncode:
            raise ValueError("MIDI Monitor automation failed: " + result.stderr.strip())
        preflight = parse_document(out / "preflight.mmon")
        resolved = preflight["settings"].get("streamSettings", {})
        receipt["resolved_settings"] = preflight["settings"]
        if source_identity(resolved) != source_identity(
            settings.get("streamSettings", {})
        ):
            raise ValueError(
                "resolved MIDI sources differ from template (missing or auto-added endpoints)"
            )
        parsed, capture_hash = _wait_for_final_capture(document)
        write_json(parsed, out / "events.json")
        write_csv(parsed, out / "events.csv")
        receipt["event_count"] = len(parsed["events"])
        receipt["possible_truncation"] = len(parsed["events"]) >= max_events
        if hashlib.sha256(document.read_bytes()).hexdigest() != capture_hash:
            raise ValueError("capture changed after final save validation")
        receipt["capture_sha256"] = capture_hash
        receipt["preflight_sha256"] = hashlib.sha256(
            (out / "preflight.mmon").read_bytes()
        ).hexdigest()
        receipt["events_json_sha256"] = hashlib.sha256(
            (out / "events.json").read_bytes()
        ).hexdigest()
        receipt["events_csv_sha256"] = hashlib.sha256(
            (out / "events.csv").read_bytes()
        ).hexdigest()
        receipt["status"] = (
            "inconclusive" if receipt["possible_truncation"] else "captured"
        )
        return receipt
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
        receipt["status"] = "failed"
        receipt["error"] = str(exc)
        raise ValueError(f"{exc}; partial evidence retained at {out}") from exc
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - started
        # Preserve hashes for any partial artifacts if automation or parsing fails.
        for key, artifact in (
            ("capture_sha256", document),
            ("preflight_sha256", out / "preflight.mmon"),
            ("events_json_sha256", out / "events.json"),
            ("events_csv_sha256", out / "events.csv"),
            ("ready_marker_sha256", marker_path),
        ):
            if artifact.is_file() and key not in receipt:
                receipt[key] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        save()
