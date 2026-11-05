"""Bounded, passive capture from one caller-pinned CoreMIDI MIDI source."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

READY_SCHEMA = "codex-devtools.raw-midi-source-ready/v1"
READY_TIMEOUT_SECONDS = 5.0
MAX_PACKET_DATA_BYTES = 256


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ready_from_line(
    line: str, *, run_id: str, source_uid: int, process_id: int
) -> tuple[dict | None, str | None]:
    if not line.endswith("\n") or "\r" in line:
        return None, "READY marker is partial or has invalid line termination"
    try:
        marker = json.loads(line)
    except json.JSONDecodeError:
        return None, "READY marker is not valid JSON"
    fields = {
        "event",
        "schema",
        "run_id",
        "source_uid",
        "process_id",
        "connected",
        "observed_monotonic_nanoseconds",
    }
    if not isinstance(marker, dict) or set(marker) != fields:
        return None, "READY marker has an unknown or incomplete schema"
    if marker.get("event") != "READY" or marker.get("schema") != READY_SCHEMA:
        return None, "READY marker type or schema is unknown"
    if (
        marker.get("run_id") != run_id
        or not isinstance(marker.get("source_uid"), int)
        or isinstance(marker.get("source_uid"), bool)
        or marker.get("source_uid") != source_uid
        or not isinstance(marker.get("process_id"), int)
        or isinstance(marker.get("process_id"), bool)
        or marker.get("process_id") != process_id
    ):
        return None, "READY marker run, source or process identity mismatch"
    if marker.get("connected") is not True:
        return None, "READY marker does not attest a connected source"
    timestamp = marker.get("observed_monotonic_nanoseconds")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp <= 0:
        return None, "READY marker timestamp is invalid"
    return marker, None


def _read_lines(stream, output: queue.Queue) -> None:
    try:
        for line in stream:
            output.put(line)
    finally:
        output.put(None)


def _read_stderr(stream, output: deque) -> None:
    for line in stream:
        output.append(line.rstrip("\n"))


def _assess(
    native: dict,
    *,
    source_uid: int,
    max_events: int,
    max_bytes: int,
    run_id: str,
    process_id: int,
) -> tuple[bool, str | None]:
    if native.get("status") != "captured":
        return False, native.get("error") or "native source capture did not complete"
    if (
        native.get("runID") != run_id
        or not isinstance(native.get("processID"), int)
        or isinstance(native.get("processID"), bool)
        or native.get("processID") != process_id
    ):
        return False, "native receipt run or process identity differs from READY"
    if (
        not isinstance(native.get("requestedSourceUID"), int)
        or isinstance(native.get("requestedSourceUID"), bool)
        or native.get("requestedSourceUID") != source_uid
    ):
        return False, "native receipt source UID differs from the pinned source"
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
            return False, "packet sequence or source identity is invalid"
        raw = packet.get("bytes")
        if (
            not isinstance(raw, list)
            or not raw
            or len(raw) > MAX_PACKET_DATA_BYTES
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 255
                for value in raw
            )
        ):
            return False, "raw packet bytes are invalid or exceed fixed packet storage"
        if any(
            not isinstance(packet.get(key), int)
            or isinstance(packet.get(key), bool)
            or packet[key] < 0
            for key in ("coreMIDITimestamp", "observedMonotonicNanoseconds")
        ):
            return False, "packet timestamps are invalid"
        size += len(raw)
    if byte_count != size:
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


def _collect_native(
    command: list[str],
    *,
    run_id: str,
    source_uid: int,
    seconds: float,
    on_ready: Callable[[dict], None] | None,
) -> tuple[dict | None, dict | None, str | None, int | None, list[str]]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
        return (
            None,
            None,
            "native listener pipes were unavailable",
            process.returncode,
            [],
        )
    lines: queue.Queue = queue.Queue()
    stderr_lines: deque = deque(maxlen=8)
    stdout_reader = threading.Thread(
        target=_read_lines, args=(process.stdout, lines), daemon=True
    )
    stderr_reader = threading.Thread(
        target=_read_stderr, args=(process.stderr, stderr_lines), daemon=True
    )
    stdout_reader.start()
    stderr_reader.start()

    started = time.monotonic()
    deadline = started + READY_TIMEOUT_SECONDS + seconds + 8.0
    marker = None
    try:
        first_line = lines.get(timeout=READY_TIMEOUT_SECONDS)
    except queue.Empty:
        first_line = None
        ready_error = (
            "native listener did not emit READY before the bounded readiness deadline"
        )
    else:
        if first_line is None:
            ready_error = "native listener ended before emitting READY"
        else:
            marker, ready_error = _ready_from_line(
                first_line,
                run_id=run_id,
                source_uid=source_uid,
                process_id=process.pid,
            )
            if marker is not None and process.poll() is not None:
                marker = None
                ready_error = (
                    "native listener exited before READY could be validated live"
                )
            if marker is not None:
                try:
                    if on_ready is not None:
                        on_ready(marker)
                except (
                    OSError
                ) as exc:  # CLI stdout publication can fail if its reader closes.
                    marker = None
                    ready_error = f"READY marker publication failed: {exc}"
    ready_marker = marker if first_line not in (None, "") else None

    output_lines = [first_line] if isinstance(first_line, str) else []
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            timed_out = True
            break
        if line is None:
            break
        output_lines.append(line)

    if timed_out and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    else:
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    stdout_reader.join(timeout=1)
    stderr_reader.join(timeout=1)
    native = None
    extra_output_error = None
    if len(output_lines) == 2 and output_lines[1].endswith("\n"):
        try:
            native = json.loads(output_lines[1])
        except json.JSONDecodeError:
            extra_output_error = "native listener final receipt is not valid JSON"
    elif ready_marker is not None:
        extra_output_error = "native listener omitted its final receipt or emitted unknown stdout markers"
    elif len(output_lines) > 1:
        try:
            native = (
                json.loads(output_lines[-1])
                if output_lines[-1].endswith("\n")
                else None
            )
        except json.JSONDecodeError:
            native = None
        if not isinstance(native, dict):
            extra_output_error = (
                "native listener emitted partial or unknown stdout markers"
            )
    elif output_lines:
        try:
            native = (
                json.loads(output_lines[0]) if output_lines[0].endswith("\n") else None
            )
        except json.JSONDecodeError:
            native = None
    if isinstance(native, dict):
        if native.get("runID") != run_id or native.get("processID") != process.pid:
            extra_output_error = "native receipt run/process correlation mismatch"
    else:
        extra_output_error = (
            extra_output_error or "native listener returned no correlated final receipt"
        )
    if timed_out:
        ready_error = (
            ready_error
            or f"native listener exceeded its bounded runtime ({deadline - started:g} seconds)"
        )
    if process.returncode not in (0, None):
        ready_error = (
            ready_error or f"native listener exited with status {process.returncode}"
        )
    protocol_error = ready_error or extra_output_error
    return native, ready_marker, protocol_error, process.returncode, list(stderr_lines)


def capture_source(
    out: Path,
    source_uid: int,
    seconds: float = 5.0,
    max_events: int = 256,
    max_bytes: int = 4096,
    on_ready: Callable[[dict], None] | None = None,
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
    run_id = str(uuid.uuid4())
    receipt = {
        "format": "codex-devtools-raw-midi-source-capture-v1",
        "run_id": run_id,
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
        "ready_timeout_seconds": READY_TIMEOUT_SECONDS,
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
                native, ready, protocol_error, returncode, stderr = _collect_native(
                    [
                        str(executable),
                        str(source_uid),
                        str(seconds),
                        str(max_events),
                        str(max_bytes),
                        run_id,
                    ],
                    run_id=run_id,
                    source_uid=source_uid,
                    seconds=seconds,
                    on_ready=on_ready,
                )
                receipt["native_returncode"] = returncode
                receipt["ready_validated"] = ready is not None
                if ready is not None:
                    receipt["ready"] = ready
                if stderr:
                    receipt["native_stderr"] = "\n".join(stderr)[-4000:]
                if native is not None:
                    receipt["native"] = native
                if protocol_error:
                    receipt["error"] = protocol_error
                elif returncode != 0:
                    receipt["error"] = (
                        f"native listener exited with status {returncode}"
                    )
                elif native is None:
                    receipt["error"] = (
                        "native source listener returned no JSON evidence"
                    )
                else:
                    captured, error = _assess(
                        native,
                        source_uid=source_uid,
                        max_events=max_events,
                        max_bytes=max_bytes,
                        run_id=run_id,
                        process_id=ready["process_id"] if ready else -1,
                    )
                    receipt["status"] = "captured" if captured else "failed"
                    if error:
                        receipt["error"] = error
    except (OSError, subprocess.SubprocessError) as exc:
        receipt["error"] = f"CoreMIDI toolchain unavailable: {exc}"
    receipt["finished_utc"] = _utc_now()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return receipt
