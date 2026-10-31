"""Explicit, isolated GUI capture adapter; offline inspection has no UI effects."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import plistlib
import subprocess
import sys
import time
from datetime import datetime, timezone

from .midi_monitor import parse_document, write_json, write_csv


def source_identity(settings: dict) -> dict:
    # Snoize rewrites saved display names on load and adds empty stream arrays.
    if not isinstance(settings, dict):
        raise ValueError("streamSettings must be a dictionary")
    result = {}
    for key, value in settings.items():
        if not value:
            continue
        if isinstance(value, list):
            if any(not isinstance(s, dict) or not isinstance(s.get("uniqueID"), int) for s in value):
                raise ValueError("source entries must contain integer uniqueID values")
            result[key] = sorted(s["uniqueID"] for s in value)
        else:
            result[key] = value
    return result


def endpoints() -> list:
    if sys.platform != "darwin":
        raise ValueError("CoreMIDI inventory requires macOS")
    script = Path(__file__).with_name("midi_endpoints.swift")
    result = subprocess.run(["/usr/bin/swift", str(script)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError("CoreMIDI inventory failed (Swift developer tools required): " + result.stderr.strip())
    return json.loads(result.stdout)


def make_template(destination: Path, source_name: str) -> dict:
    """Resolve exactly one online CoreMIDI input endpoint; never invent IDs."""
    if destination.exists():
        raise ValueError("template output already exists")
    matches = [s for s in endpoints() if s["direction"] == "source" and not s["offline"]
               and source_name in (s["name"], s["displayName"])]
    if len(matches) != 1:
        raise ValueError("source name must match exactly one online MIDI input endpoint")
    source = {"uniqueID": matches[0]["uniqueID"], "name": matches[0]["displayName"]}
    document = {"version": 2, "streamSettings": {"portInputStream": [source]},
                "filterMask": 0xFFFFFFFF, "areSourcesShown": True, "isFilterShown": True}
    with destination.open("xb") as f:
        plistlib.dump(document, f)
    return {"source": source, "template": str(destination)}


def capture(template: Path, out: Path, seconds: float, max_events: int = 100000) -> dict:
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
        "schema": "devtools-midi-capture-v1", "status": "running",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "requested_seconds": seconds, "max_events": max_events,
        "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
        "adapter_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "configured_settings": settings,
        "configured_source_identity": source_identity(settings.get("streamSettings", {})),
        "capture_file": name,
        "timing_note": "UI-controlled capture; disconnecting inputs adds boundary uncertainty",
    }
    info = Path("/Applications/MIDI Monitor.app/Contents/Info.plist")
    if info.exists():
        receipt["midi_monitor_version"] = plistlib.loads(info.read_bytes()).get("CFBundleShortVersionString")
    receipt_path = out / "receipt.json"
    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    save()
    started = time.monotonic()
    try:
        result = subprocess.run([
            "/usr/bin/osascript", str(script), str(document),
            str(out / "preflight.mmon"), name, str(seconds), str(max_events), document.as_uri(),
        ], capture_output=True, text=True, timeout=seconds + 60)
        receipt["adapter_stdout"] = result.stdout.strip()
        if result.returncode:
            raise ValueError("MIDI Monitor automation failed: " + result.stderr.strip())
        preflight = parse_document(out / "preflight.mmon")
        resolved = preflight["settings"].get("streamSettings", {})
        receipt["resolved_settings"] = preflight["settings"]
        if source_identity(resolved) != source_identity(settings.get("streamSettings", {})):
            raise ValueError("resolved MIDI sources differ from template (missing or auto-added endpoints)")
        parsed = parse_document(document)
        if any(parsed["settings"].get("streamSettings", {}).values()):
            raise ValueError("capture inputs were not disconnected; capture boundary is unverified")
        write_json(parsed, out / "events.json")
        write_csv(parsed, out / "events.csv")
        receipt["event_count"] = len(parsed["events"])
        receipt["possible_truncation"] = len(parsed["events"]) >= max_events
        receipt["capture_sha256"] = hashlib.sha256(document.read_bytes()).hexdigest()
        receipt["preflight_sha256"] = hashlib.sha256((out / "preflight.mmon").read_bytes()).hexdigest()
        receipt["events_json_sha256"] = hashlib.sha256((out / "events.json").read_bytes()).hexdigest()
        receipt["events_csv_sha256"] = hashlib.sha256((out / "events.csv").read_bytes()).hexdigest()
        receipt["status"] = "inconclusive" if receipt["possible_truncation"] else "captured"
        return receipt
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
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
        ):
            if artifact.is_file():
                receipt[key] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        save()
