"""Read-only Max archive parsing and patcher graph inspection."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from pathlib import PureWindowsPath


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"truncated 32-bit field at offset {offset}")
    return struct.unpack_from(">I", data, offset)[0]


def _chunks(data: bytes, start: int, end: int):
    pos = start
    while pos < end:
        if pos + 8 > end:
            raise ValueError("truncated chunk header")
        size = _u32(data, pos + 4)
        if size < 8 or pos + size > end:
            raise ValueError(f"invalid chunk size at offset {pos}")
        yield data[pos:pos + 4], data[pos + 8:pos + size], pos, size
        pos += size
    if pos != end:
        raise ValueError("chunk boundary mismatch")


def _safe_entry_name(name: str) -> None:
    if (not name or name in (".", "..") or "/" in name or "\\" in name
            or "\x00" in name or ":" in name or PureWindowsPath(name).drive):
        raise ValueError(f"unsafe archive filename {name!r}")


def parse_mxf(data: bytes) -> list[dict]:
    """Parse an mx@c archive whose root payload starts at byte 16."""
    if len(data) < 16 or data[:4] != b"mx@c" or _u32(data, 4) != 16:
        raise ValueError("unsupported MXF header (expected mx@c with root offset 16)")
    boundary = _u32(data, 12)
    if boundary < 16 or boundary > len(data):
        raise ValueError("invalid MXF directory boundary")
    outer = list(_chunks(data, boundary, len(data)))
    if len(outer) != 1 or outer[0][0] != b"dlst":
        raise ValueError("expected one dlst directory")
    entries = []
    seen = set()
    for tag, payload, _, _ in _chunks(outer[0][1], 0, len(outer[0][1])):
        if tag != b"dire":
            raise ValueError("unexpected directory record")
        fields = {}
        for field_tag, field_value, _, _ in _chunks(payload, 0, len(payload)):
            if field_tag in fields:
                raise ValueError(f"duplicate directory field {field_tag!r}")
            fields[field_tag] = field_value
        if not all(tag in fields for tag in (b"fnam", b"of32", b"sz32")):
            raise ValueError("incomplete directory record")
        if len(fields[b"of32"]) != 4 or len(fields[b"sz32"]) != 4:
            raise ValueError("invalid directory offset or size field")
        try:
            name = fields[b"fnam"].split(b"\0", 1)[0].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("archive filename is not UTF-8") from exc
        _safe_entry_name(name)
        if name in seen:
            raise ValueError(f"duplicate archive filename {name!r}")
        seen.add(name)
        offset, size = _u32(fields[b"of32"], 0), _u32(fields[b"sz32"], 0)
        if offset < 16 or offset + size > boundary:
            raise ValueError(f"entry outside MXF payload range: {name}")
        entries.append({"name": name, "offset": offset, "data": data[offset:offset + size]})
    entries.sort(key=lambda entry: entry["offset"])
    if not entries or entries[0]["offset"] != 16:
        raise ValueError("root entry does not begin at offset 16")
    for previous, current in zip(entries, entries[1:]):
        if previous["offset"] + len(previous["data"]) != current["offset"]:
            raise ValueError("archive payloads overlap or are not contiguous")
    if entries[-1]["offset"] + len(entries[-1]["data"]) != boundary:
        raise ValueError("unaccounted bytes in MXF payload area")
    return entries


def decode_file(data: bytes) -> tuple[str, list[dict], dict | None]:
    """Return (format, archive entries, parsed patch) for MXF/AMXD/JSON."""
    if data[:4] == b"mx@c":
        entries = parse_mxf(data)
        return "mxf", entries, _patch_from_entries(entries)
    if data[:4] == b"ampf":
        # The observed wrapper uses aaaa (audio), iiii (instrument), or
        # mmmm (MIDI) as its four-byte device tag. Keep the following meta
        # value opaque: inspected devices use both 1 and 7.
        if (len(data) < 32
                or data[4:8] != b"\x04\x00\x00\x00"
                or data[12:16] != b"meta"
                or data[16:20] != b"\x04\x00\x00\x00"):
            raise ValueError("unsupported AMXD header (expected fixed-size ampf/meta wrapper fields)")
        if data[8:12] not in (b"aaaa", b"iiii", b"mmmm"):
            raise ValueError(f"unsupported AMXD device tag {data[8:12]!r}")
        if data[24:28] == b"ciph":
            raise ValueError("unsupported encrypted AMXD ciph payload")
        if data[24:28] != b"ptch":
            raise ValueError(f"unsupported AMXD payload chunk {data[24:28]!r} (expected ptch)")
        body_size = struct.unpack_from("<I", data, 28)[0]
        if body_size != len(data) - 32:
            raise ValueError("AMXD ptch size does not match file size")
        body = data[32:]
        if body[:4] == b"mx@c":
            entries = parse_mxf(body)
            return "amxd", entries, _patch_from_entries(entries)
        patch = _load_patch(body)
        return "amxd", [], patch
    return "json", [], _load_patch(data)


def _load_patch(data: bytes) -> dict:
    try:
        patch = json.loads(data.rstrip(b"\0\r\n").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"patch payload is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(patch, dict):
        raise ValueError("Max JSON root must be an object")
    return patch


def _patch_from_entries(entries: list[dict]) -> dict | None:
    if not entries:
        return None
    try:
        patch = _load_patch(entries[0]["data"])
    except ValueError:
        return None
    return patch


def walk_patchers(patch: dict, path: str = "root"):
    """Yield root and inline nested patchers (including bpatcher contents)."""
    yield path, patch
    boxes = patch.get("boxes", [])
    if not isinstance(boxes, list):
        return
    for index, item in enumerate(boxes):
        if not isinstance(item, dict) or not isinstance(item.get("box"), dict):
            continue
        box = item["box"]
        child = box.get("patcher")
        if isinstance(child, dict):
            label = box.get("text") or box.get("id") or f"box-{index}"
            yield from walk_patchers(child, f"{path}/{label}")


def graph_report(patch: dict) -> dict:
    errors = []
    patcher_summaries = []
    total_boxes = total_lines = 0
    root = patch.get("patcher", patch)
    if not isinstance(root, dict):
        return {"errors": ["root patcher must be an object"], "patchers": [], "counts": {"patchers": 0, "boxes": 0, "lines": 0}}
    for path, current in walk_patchers(root):
        raw_boxes = current.get("boxes", [])
        raw_lines = current.get("lines", [])
        if not isinstance(raw_boxes, list):
            errors.append(f"{path}: boxes must be an array")
            raw_boxes = []
        if not isinstance(raw_lines, list):
            errors.append(f"{path}: lines must be an array")
            raw_lines = []
        boxes = []
        ids = {}
        for index, item in enumerate(raw_boxes):
            box = item.get("box") if isinstance(item, dict) else None
            if not isinstance(box, dict):
                errors.append(f"{path}: box entry {index} has no box object")
                continue
            box_id = box.get("id")
            if box_id is None:
                errors.append(f"{path}: box entry {index} has no ID")
                continue
            if box_id in ids:
                errors.append(f"{path}: duplicate ID {box_id!r}")
            ids[box_id] = box
            boxes.append(box)
        for index, item in enumerate(raw_lines):
            line = item.get("patchline") if isinstance(item, dict) else None
            if not isinstance(line, dict):
                errors.append(f"{path}: line entry {index} has no patchline object")
                continue
            for key, count_key in (("source", "numoutlets"), ("destination", "numinlets")):
                endpoint = line.get(key)
                if not isinstance(endpoint, list) or len(endpoint) != 2:
                    errors.append(f"{path}: invalid {key} endpoint at line {index}")
                    continue
                box_id, port = endpoint
                target = ids.get(box_id)
                if target is None:
                    errors.append(f"{path}: unknown {key} ID {box_id!r} at line {index}")
                    continue
                count = target.get(count_key)
                if isinstance(port, bool) or not isinstance(port, int) or isinstance(count, bool) or not isinstance(count, int) or port < 0 or port >= count:
                    errors.append(f"{path}: invalid {key} port {box_id!r}:{port!r} at line {index}")
        n_lines = len(raw_lines)
        patcher_summaries.append({"path": path, "boxes": len(raw_boxes), "lines": n_lines})
        total_boxes += len(raw_boxes)
        total_lines += n_lines
    return {"errors": errors, "patchers": patcher_summaries, "counts": {"patchers": len(patcher_summaries), "boxes": total_boxes, "lines": total_lines}}


def inventory(path: Path) -> dict:
    raw = path.read_bytes()
    fmt, entries, patch = decode_file(raw)
    archive = [{"name": entry["name"], "bytes": len(entry["data"]), "sha256": sha256(entry["data"])} for entry in entries]
    graph = graph_report(patch) if patch is not None else None
    return {"path": str(path), "format": fmt, "wrapper_valid": True,
            "graph_valid": None if graph is None else not graph["errors"],
            "bytes": len(raw), "sha256": sha256(raw), "entries": archive,
            "entry_count": len(archive), "graph": graph}


def extract_archive(path: Path, out: Path) -> list[dict]:
    """Extract every stored payload byte-for-byte without overwriting files."""
    raw = path.read_bytes()
    fmt, entries, _ = decode_file(raw)
    if fmt == "json" or not entries:
        raise ValueError("input has no embedded MXF archive to extract")
    out.mkdir(parents=True, exist_ok=True)
    destinations = [out / entry["name"] for entry in entries]
    for destination in destinations:
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"refusing to overwrite existing path: {destination}")
    for entry, destination in zip(entries, destinations):
        with destination.open("xb") as stream:
            stream.write(entry["data"])
    return [{"name": entry["name"], "bytes": len(entry["data"]), "sha256": sha256(entry["data"])} for entry in entries]
