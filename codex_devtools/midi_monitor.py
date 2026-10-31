"""Read-only parser and exporters for Snoize MIDI Monitor ``.mmon`` documents.

The file is a property list whose ``messageData`` member is an NSKeyedArchive.
This module reads that archive as data; it never invokes Apple's object
unarchiver or executes archived classes.
"""

from __future__ import annotations

import csv
import json
import plistlib
from pathlib import Path
from typing import Any


_SUPPORTED_VERSIONS = {1, 2}
_UID = plistlib.UID


def _plist(data: bytes, what: str) -> Any:
    try:
        return plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError, OverflowError) as exc:
        raise ValueError(f"malformed or truncated {what} property list: {exc}") from exc


def _archive_root(archive: Any) -> tuple[list[Any], list[Any]]:
    if not isinstance(archive, dict) or archive.get("$archiver") != "NSKeyedArchiver":
        raise ValueError("unknown messageData format: expected NSKeyedArchiver")
    if archive.get("$version") != 100000:
        raise ValueError(f"unsupported NSKeyedArchive version: {archive.get('$version')!r}")
    objects = archive.get("$objects")
    top = archive.get("$top")
    if not isinstance(objects, list) or not isinstance(top, dict):
        raise ValueError("malformed NSKeyedArchive: missing $objects or $top")
    ref = top.get("root")
    if not isinstance(ref, _UID) or not 0 <= ref.data < len(objects):
        raise ValueError("malformed NSKeyedArchive: invalid root reference")
    root = objects[ref.data]
    if not isinstance(root, dict) or not isinstance(root.get("NS.objects"), list):
        raise ValueError("unknown messageData schema: root is not an archived message array")
    return objects, root["NS.objects"]


def _ref(objects: list[Any], value: Any, label: str) -> Any:
    if not isinstance(value, _UID) or not 0 <= value.data < len(objects):
        raise ValueError(f"malformed NSKeyedArchive: invalid {label} reference")
    return objects[value.data]


def _class_name(objects: list[Any], item: dict[str, Any]) -> str:
    class_ref = item.get("$class")
    cls = _ref(objects, class_ref, "class")
    if not isinstance(cls, dict) or not isinstance(cls.get("$classname"), str):
        raise ValueError("malformed NSKeyedArchive: invalid class descriptor")
    return cls["$classname"]


def _decode_event(objects: list[Any], ref: Any) -> dict[str, Any]:
    item = _ref(objects, ref, "message")
    if not isinstance(item, dict):
        raise ValueError("unknown messageData schema: message entry is not an object")
    class_name = _class_name(objects, item)
    allowed = {
        "SMVoiceMessage", "SMSystemCommonMessage", "SMSystemRealTimeMessage",
        "SMSystemExclusiveMessage", "SMInvalidMessage",
    }
    if class_name not in allowed:
        raise ValueError(f"unknown messageData schema: unsupported message class {class_name!r}")
    status = item.get("statusByte")
    if not isinstance(status, int) or isinstance(status, bool) or not 0 <= status <= 255:
        raise ValueError(f"malformed {class_name}: invalid statusByte")

    timestamp = item.get("clockTimeStamp")
    timestamp_unit = "reference_seconds"
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool) or timestamp == 0:
        timestamp = item.get("timeStampInNanos", item.get("timeStamp"))
        timestamp_unit = "host_ticks_or_nanoseconds"
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        timestamp = None
        timestamp_unit = None

    extras: bytes
    raw: bytes | None = None
    if class_name == "SMVoiceMessage":
        extras = item.get("dataBytes")
        if not isinstance(extras, bytes) or len(extras) != 2:
            raise ValueError("malformed SMVoiceMessage: expected two dataBytes")
        voice_status = status & 0xF0
        kind = {
            0x80: "note_off", 0x90: "note_on", 0xA0: "poly_aftertouch",
            0xB0: "control_change", 0xC0: "program_change",
            0xD0: "channel_pressure", 0xE0: "pitch_bend",
        }.get(voice_status)
        if kind is None:
            raise ValueError(f"malformed SMVoiceMessage: invalid status 0x{status:02X}")
        event: dict[str, Any] = {"type": kind or "unknown_voice", "channel": (status & 0x0F) + 1}
        if status & 0xF0 == 0xB0:
            event.update(controller=extras[0], value=extras[1])
        elif status & 0xF0 in (0x80, 0x90, 0xA0):
            event.update(note=extras[0], value=extras[1])
        elif status & 0xF0 == 0xC0:
            event["program"] = extras[0]
        elif status & 0xF0 == 0xD0:
            event["value"] = extras[0]
        elif status & 0xF0 == 0xE0:
            event["value"] = extras[0] | (extras[1] << 7)
        if voice_status in (0xC0, 0xD0):
            extras = extras[:1]  # Snoize archives two bytes even for one-byte voice messages.
    elif class_name == "SMSystemExclusiveMessage":
        data = _ref(objects, item.get("data"), "SysEx data")
        if not isinstance(data, bytes):
            raise ValueError("malformed SMSystemExclusiveMessage: data is not bytes")
        extras = data + (b"\xf7" if item.get("wasReceivedWithEOX", False) else b"")
        event = {"type": "system_exclusive"}
    elif class_name == "SMInvalidMessage":
        data = _ref(objects, item.get("data"), "invalid-message data")
        if not isinstance(data, bytes):
            raise ValueError("malformed SMInvalidMessage: data is not bytes")
        # InvalidMessage stores the received raw bytes directly; statusByte is a dummy.
        extras = b""
        event = {"type": "invalid"}
        raw = data
    elif class_name == "SMSystemCommonMessage":
        extras = item.get("dataBytes")
        if not isinstance(extras, bytes) or len(extras) != 2:
            raise ValueError("malformed SMSystemCommonMessage: expected two dataBytes")
        event = {"type": {0xF1:"time_code", 0xF2:"song_position", 0xF3:"song_select", 0xF6:"tune_request"}.get(status, "system_common")}
        count = {0xF1:1, 0xF2:2, 0xF3:1, 0xF6:0}.get(status)
        if count is None:
            raise ValueError(f"malformed SMSystemCommonMessage: unsupported status 0x{status:02X}")
        extras = extras[:count]
    else:
        # SystemRealTimeMessage encodes only Message's fields; unlike
        # SystemCommonMessage, it has no dataBytes field in Snoize's NSCoding.
        extras = b""
        event = {"type": {0xF8:"clock", 0xFA:"start", 0xFB:"continue", 0xFC:"stop", 0xFE:"active_sense", 0xFF:"reset"}.get(status)}
        if event["type"] is None:
            raise ValueError(f"malformed SMSystemRealTimeMessage: unsupported status 0x{status:02X}")

    if raw is None:
        raw = bytes([status]) + extras
    event.update(raw=raw.hex(" ").upper(), timestamp=timestamp, timestamp_unit=timestamp_unit)
    origin = item.get("originatingEndpoint")
    if isinstance(origin, _UID):
        origin = _ref(objects, origin, "originatingEndpoint")
        if not isinstance(origin, str):
            raise ValueError("unknown messageData schema: originatingEndpoint is not an archived string")
    if isinstance(origin, str) and origin:
        event["origin"] = origin
    return event


def parse_document(path: str | Path) -> dict[str, Any]:
    """Parse a Snoize MIDI Monitor saved document without unarchiving objects."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        raise
    document = _plist(data, "document")
    if not isinstance(document, dict):
        raise ValueError("unknown .mmon format: document root is not a dictionary")
    version = document.get("version")
    if version not in _SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported MIDI Monitor document version: {version!r}")
    settings = {key: value for key, value in document.items() if key != "messageData"}
    message_data = document.get("messageData")
    if message_data is None:
        refs: list[Any] = []
        objects: list[Any] = []
    elif not isinstance(message_data, bytes):
        raise ValueError("malformed .mmon document: messageData is not bytes")
    else:
        archive = _plist(message_data, "messageData archive")
        objects, refs = _archive_root(archive)
    return {"version": version, "settings": settings, "events": [_decode_event(objects, ref) for ref in refs]}


def write_json(document: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(document: dict[str, Any], path: str | Path) -> None:
    fields = ["timestamp", "timestamp_unit", "type", "channel", "controller", "value", "note", "program", "raw", "origin"]
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(document.get("events", []))

