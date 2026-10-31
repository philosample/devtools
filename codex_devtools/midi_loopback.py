"""Bounded CoreMIDI self-contained destination transport smoke test."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import tempfile
import uuid


FIXTURE = [[0x90, 0x3C, 0x64], [0x80, 0x3C, 0x00], [0xB0, 0x07, 0x55], [0xF8]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _assess(native: dict) -> tuple[bool, str | None]:
    if native.get("status") != "passed":
        return False, native.get("error") or "native transport did not report passed"
    events = native.get("events")
    if not isinstance(events, list) or len(events) != len(FIXTURE):
        return False, f"expected {len(FIXTURE)} received events; got {len(events) if isinstance(events, list) else 'invalid'}"
    if native.get("sentEvents") != len(FIXTURE) or native.get("receivedEvents") != len(FIXTURE):
        return False, "sent and received event counts must equal the positive fixture count"
    if [event.get("bytes") for event in events] != FIXTURE:
        return False, "received bytes or event order differ from the exact fixture"
    expected_cleanup = {"output_port", "virtual_destination", "client"}
    cleanup = native.get("cleanup", {})
    if not expected_cleanup.issubset(cleanup) or not all(cleanup[name] is True for name in expected_cleanup):
        return False, "CoreMIDI resources were not all confirmed disposed"
    if any(not isinstance(e.get("observedMonotonicNanoseconds"), int) for e in events):
        return False, "callback monotonic observation timestamps are missing"
    return True, None


def loopback(out: Path, timeout_seconds: float = 3.0) -> dict:
    """Compile and run the private virtual-destination smoke test, saving evidence."""
    out = out.expanduser().resolve()
    if out.exists():
        raise ValueError(f"evidence output already exists: {out}")
    if platform.system() != "Darwin":
        raise ValueError("native CoreMIDI loopback requires macOS; no other MIDI endpoint fallback is allowed")
    if not 0.05 <= timeout_seconds <= 30:
        raise ValueError("timeout must be between 0.05 and 30 seconds")

    source = Path(__file__).with_name("midi_loopback.swift")
    source_bytes = source.read_bytes()
    receipt = {
        "format": "codex-devtools-native-midi-loopback-v1",
        "run_id": str(uuid.uuid4()),
        "started_utc": _utc_now(),
        "source": {"path": str(source), "sha256": hashlib.sha256(source_bytes).hexdigest()},
        "runtime": {"os": platform.platform(), "macos_version": platform.mac_ver()[0]},
        "timeout_seconds": timeout_seconds,
        "fixture": {"events": FIXTURE, "event_count": len(FIXTURE)},
        "transport_scope": "CoreMIDI output port to this process's private virtual destination handle only",
        "timing_claim": "callback monotonic observation recorded separately from CoreMIDI scheduled timestamps; no precision or latency claim",
        "status": "failed",
    }
    try:
        version = subprocess.run(["xcrun", "swiftc", "--version"], check=True, capture_output=True, text=True, timeout=20)
        sdk = subprocess.run(["xcrun", "--sdk", "macosx", "--show-sdk-path"], check=True, capture_output=True, text=True, timeout=20)
        sdk_version = subprocess.run(["xcrun", "--sdk", "macosx", "--show-sdk-version"], check=True, capture_output=True, text=True, timeout=20)
        receipt["runtime"]["swift_toolchain"] = version.stdout.strip()
        receipt["runtime"]["sdk_path"] = sdk.stdout.strip()
        receipt["runtime"]["sdk_version"] = sdk_version.stdout.strip()
        with tempfile.TemporaryDirectory(prefix="codex-midi-loopback-") as temp:
            executable = Path(temp) / "native-midi-loopback"
            compile_result = subprocess.run(
                ["xcrun", "swiftc", "-sdk", sdk.stdout.strip(), "-framework", "CoreMIDI", "-framework", "Foundation", str(source), "-o", str(executable)],
                capture_output=True, text=True, timeout=90,
            )
            receipt["compile"] = {"returncode": compile_result.returncode, "stderr": compile_result.stderr[-4000:]}
            if compile_result.returncode != 0:
                receipt["error"] = "Swift CoreMIDI smoke test did not compile"
            else:
                try:
                    run = subprocess.run([str(executable), str(timeout_seconds)], capture_output=True, text=True, timeout=timeout_seconds + 8)
                    receipt["native_returncode"] = run.returncode
                    if run.stderr:
                        receipt["native_stderr"] = run.stderr[-4000:]
                    try:
                        native = json.loads(run.stdout.strip().splitlines()[-1]) if run.stdout.strip() else None
                    except json.JSONDecodeError:
                        native = None
                    if not isinstance(native, dict):
                        receipt["error"] = "native smoke test returned no JSON evidence"
                    else:
                        receipt["native"] = native
                        passed, error = _assess(native)
                        receipt["status"] = "passed" if passed else "failed"
                        if error:
                            receipt["error"] = error
                except subprocess.TimeoutExpired:
                    receipt["error"] = f"native process exceeded bounded runtime ({timeout_seconds + 8:g} seconds)"
    except (OSError, subprocess.SubprocessError) as exc:
        receipt["error"] = f"CoreMIDI toolchain unavailable: {exc}"
    receipt["finished_utc"] = _utc_now()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt
