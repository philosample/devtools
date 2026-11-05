"""Bounded, passive capture from one caller-pinned CoreMIDI MIDI source."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _assess(
    native: dict,
    *,
    source_uid: int,
    max_events: int,
    max_bytes: int,
) -> tuple[bool, str | None]:
    if native.get("status") != "captured":
        return False, native.get("error") or "native source capture did not complete"
    source = native.get("source")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("uniqueID"), int)
        or isinstance(source.get("uniqueID"), bool)
        or source.get("uniqueID") != source_uid
        or source.get("offline") is not False
    ):
        return (
            False,
            "native capture source identity differs from the pinned online source UID",
        )
    packets = native.get("packets")
    packet_count = native.get("packetCount")
    if (
        not isinstance(packets, list)
        or not isinstance(packet_count, int)
        or isinstance(packet_count, bool)
        or packet_count != len(packets)
    ):
        return False, "packet list/count is invalid"
    byte_count = native.get("byteCount")
    if not isinstance(byte_count, int) or isinstance(byte_count, bool):
        return False, "byte count is invalid"
    size = 0
    for index, packet in enumerate(packets):
        if (
            not isinstance(packet, dict)
            or not isinstance(packet.get("sequence"), int)
            or isinstance(packet.get("sequence"), bool)
            or packet.get("sequence") != index
            or not isinstance(packet.get("sourceUniqueID"), int)
            or isinstance(packet.get("sourceUniqueID"), bool)
            or packet.get("sourceUniqueID") != source_uid
        ):
            return False, "packet source identity differs from the pinned source UID"
        raw = packet.get("bytes")
        if (
            not isinstance(raw, list)
            or not raw
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 255
                for value in raw
            )
        ):
            return False, "raw packet bytes are invalid"
        if any(
            not isinstance(packet.get(key), int)
            or isinstance(packet.get(key), bool)
            or packet[key] < 0
            for key in ("coreMIDITimestamp", "observedMonotonicNanoseconds")
        ):
            return False, "packet timestamps are invalid"
        size += len(raw)
    if native["byteCount"] != size:
        return False, "reported byte count differs from exact packet payloads"
    # Reaching a cap is inconclusive: more packets may have been omitted.
    if len(packets) >= max_events or size >= max_bytes:
        return False, "capture reached an event or byte bound and may be truncated"
    cleanup = native.get("cleanup")
    if not isinstance(cleanup, dict) or any(
        cleanup.get(key) is not True
        for key in ("sourceDisconnected", "inputPortDisposed", "clientDisposed")
    ):
        return False, "CoreMIDI input resources were not all confirmed cleaned up"
    return True, None


def capture_source(
    out: Path,
    source_uid: int,
    seconds: float = 5.0,
    max_events: int = 256,
    max_bytes: int = 4096,
) -> dict:
    """Compile and run the passive source listener, saving an exclusive receipt."""
    out = out.expanduser().absolute()
    if os.path.lexists(out):
        raise ValueError(f"evidence output already exists: {out}")
    if sys.platform != "darwin":
        raise ValueError(
            "native CoreMIDI source capture requires macOS; no other endpoint fallback is allowed"
        )
    if (
        not isinstance(source_uid, int)
        or isinstance(source_uid, bool)
        or not -(2**31) <= source_uid < 2**31
    ):
        raise ValueError("source UID must be a signed 32-bit integer")
    if not 0.05 <= seconds <= 30:
        raise ValueError("capture duration must be between 0.05 and 30 seconds")
    if not 1 <= max_events <= 65536:
        raise ValueError("max-events must be between 1 and 65536")
    if not 1 <= max_bytes <= 1048576:
        raise ValueError("max-bytes must be between 1 and 1048576")

    source = Path(__file__).with_name("midi_source_capture.swift")
    source_bytes = source.read_bytes()
    receipt = {
        "format": "codex-devtools-raw-midi-source-capture-v1",
        "run_id": str(uuid.uuid4()),
        "started_utc": _utc_now(),
        "source_pin": {"uniqueID": source_uid},
        "source_code": {
            "path": str(source),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        "runtime": {"os": platform.platform(), "macos_version": platform.mac_ver()[0]},
        "bounds": {
            "seconds": seconds,
            "max_events": max_events,
            "max_bytes": max_bytes,
        },
        "capture_scope": "passive CoreMIDI input connection to exactly one online source UID; no output port or send API",
        "status": "failed",
    }
    try:
        version = subprocess.run(
            ["xcrun", "swiftc", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        sdk = subprocess.run(
            ["xcrun", "--sdk", "macosx", "--show-sdk-path"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        sdk_version = subprocess.run(
            ["xcrun", "--sdk", "macosx", "--show-sdk-version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        receipt["runtime"].update(
            {
                "swift_toolchain": version.stdout.strip(),
                "sdk_path": sdk.stdout.strip(),
                "sdk_version": sdk_version.stdout.strip(),
            }
        )
        with tempfile.TemporaryDirectory(prefix="codex-midi-source-capture-") as temp:
            executable = Path(temp) / "native-midi-source-capture"
            compile_result = subprocess.run(
                [
                    "xcrun",
                    "swiftc",
                    "-sdk",
                    sdk.stdout.strip(),
                    "-framework",
                    "CoreMIDI",
                    "-framework",
                    "Foundation",
                    str(source),
                    "-o",
                    str(executable),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
            )
            receipt["compile"] = {
                "returncode": compile_result.returncode,
                "stderr": compile_result.stderr[-4000:],
            }
            if compile_result.returncode != 0:
                receipt["error"] = "Swift CoreMIDI source listener did not compile"
            else:
                try:
                    run = subprocess.run(
                        [
                            str(executable),
                            str(source_uid),
                            str(seconds),
                            str(max_events),
                            str(max_bytes),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=seconds + 8,
                    )
                    receipt["native_returncode"] = run.returncode
                    if run.stderr:
                        receipt["native_stderr"] = run.stderr[-4000:]
                    try:
                        native = (
                            json.loads(run.stdout.strip().splitlines()[-1])
                            if run.stdout.strip()
                            else None
                        )
                    except json.JSONDecodeError:
                        native = None
                    if not isinstance(native, dict):
                        receipt["error"] = (
                            "native source listener returned no JSON evidence"
                        )
                    else:
                        receipt["native"] = native
                        if run.returncode != 0:
                            receipt["error"] = (
                                f"native source listener exited with status {run.returncode}"
                            )
                        else:
                            captured, error = _assess(
                                native,
                                source_uid=source_uid,
                                max_events=max_events,
                                max_bytes=max_bytes,
                            )
                            receipt["status"] = "captured" if captured else "failed"
                            if error:
                                receipt["error"] = error
                except subprocess.TimeoutExpired:
                    receipt["error"] = (
                        f"native source listener exceeded bounded runtime ({seconds + 8:g} seconds)"
                    )
    except (OSError, subprocess.SubprocessError) as exc:
        receipt["error"] = f"CoreMIDI toolchain unavailable: {exc}"
    receipt["finished_utc"] = _utc_now()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return receipt
