"""Read-only inspection of external device references in one Ableton Live Set."""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath

from .maxfiles import sha256


MAX_SET_FILE_BYTES = 32 * 1024 * 1024
MAX_SET_XML_BYTES = 128 * 1024 * 1024
MAX_REFERENCES = 100_000
MAX_AMXD_BYTES = 512 * 1024 * 1024
SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)
CHUNK_BYTES = 1024 * 1024


def _read_set(path: Path) -> tuple[Path, bytes, bytes]:
    supplied = path.expanduser()
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"cannot resolve selected Set file: {exc}") from exc
    if not resolved.is_file():
        raise ValueError("selected path is not a regular .als file")
    if resolved.suffix.lower() != ".als":
        raise ValueError("selected Set file must have an .als extension")
    try:
        before_read = resolved.stat()
    except OSError as exc:
        raise ValueError(f"cannot stat selected Set file: {exc}") from exc
    if before_read.st_size > MAX_SET_FILE_BYTES:
        raise ValueError(f"compressed Set exceeds the {MAX_SET_FILE_BYTES}-byte input limit")
    try:
        with resolved.open("rb") as stream:
            raw = stream.read(MAX_SET_FILE_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"cannot read selected Set file: {exc}") from exc
    if len(raw) > MAX_SET_FILE_BYTES:
        raise ValueError(f"compressed Set exceeds the {MAX_SET_FILE_BYTES}-byte input limit")
    try:
        after_read = resolved.stat()
    except OSError as exc:
        raise ValueError(f"cannot restat selected Set file: {exc}") from exc
    if (before_read.st_size != after_read.st_size
            or before_read.st_mtime_ns != after_read.st_mtime_ns):
        raise ValueError("selected Set changed while it was being read")
    if len(raw) < 2 or raw[:2] != b"\x1f\x8b":
        raise ValueError("selected .als file is not a gzip stream")
    try:
        chunks: list[bytes] = []
        total = 0
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            while True:
                chunk = stream.read(min(CHUNK_BYTES, MAX_SET_XML_BYTES - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SET_XML_BYTES:
                    raise ValueError(f"decompressed Set XML exceeds the {MAX_SET_XML_BYTES}-byte limit")
                chunks.append(chunk)
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise ValueError(f"selected .als gzip stream is malformed or truncated: {exc}") from exc
    return resolved, raw, b"".join(chunks)


def _absolute_local_path(raw_path: str) -> Path | None:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    # Recognize a Windows absolute path without interpreting it relative to the
    # current POSIX directory. Such paths remain raw-only on this host.
    if PureWindowsPath(raw_path).drive:
        return None
    return None


def _local_file_report(raw_path: str) -> dict:
    candidate = _absolute_local_path(raw_path)
    if candidate is None:
        return {"status": "not_local_absolute_path", "exists": None,
                "resolved_path": None, "bytes": None, "sha256": None}
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return {"status": "missing", "exists": False,
                "resolved_path": str(candidate.resolve(strict=False)), "bytes": None, "sha256": None}
    except OSError as exc:
        raise ValueError(f"cannot resolve referenced AMXD path {raw_path!r}: {exc}") from exc
    if not resolved.is_file():
        return {"status": "not_a_regular_file", "exists": False,
                "resolved_path": str(resolved), "bytes": None, "sha256": None}
    before_hash = resolved.stat()
    size = before_hash.st_size
    if size > MAX_AMXD_BYTES:
        raise ValueError(f"referenced AMXD exceeds the {MAX_AMXD_BYTES}-byte hashing limit: {raw_path}")
    digest = hashlib.sha256()
    total = 0
    try:
        with resolved.open("rb") as stream:
            while True:
                block = stream.read(CHUNK_BYTES)
                if not block:
                    break
                total += len(block)
                if total > MAX_AMXD_BYTES:
                    raise ValueError(f"referenced AMXD grew beyond the {MAX_AMXD_BYTES}-byte hashing limit: {raw_path}")
                digest.update(block)
    except OSError as exc:
        raise ValueError(f"cannot hash referenced AMXD path {raw_path!r}: {exc}") from exc
    after_hash = resolved.stat()
    if (total != before_hash.st_size or after_hash.st_size != before_hash.st_size
            or after_hash.st_mtime_ns != before_hash.st_mtime_ns):
        raise ValueError(f"referenced AMXD changed while it was being hashed: {raw_path}")
    return {"status": "hashed", "exists": True, "resolved_path": str(resolved),
            "bytes": total, "sha256": digest.hexdigest()}


def _extract_file_refs(xml_bytes: bytes) -> list[dict]:
    try:
        xml_text = xml_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Set XML must be UTF-8") from exc
    upper = xml_text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise ValueError("DTD and entity declarations are unsupported in Set XML")

    stack: list[str] = []
    active_refs: list[dict] = []
    refs: list[dict] = []
    root_seen = False
    live_set_count = 0
    file_ref_count = 0
    try:
        parser = ET.iterparse(io.BytesIO(xml_bytes), events=("start", "end"))
        for event, element in parser:
            tag = element.tag
            if not isinstance(tag, str) or tag.startswith("{"):
                raise ValueError("namespaced or non-text XML tags are unsupported")
            if event == "start":
                if not root_seen:
                    root_seen = True
                    if tag != "Ableton":
                        raise ValueError(f"unsupported Set XML root {tag!r}; expected 'Ableton'")
                if tag == "LiveSet":
                    live_set_count += 1
                if tag == "FileRef":
                    file_ref_count += 1
                    if file_ref_count > MAX_REFERENCES:
                        raise ValueError(f"Set exceeds the {MAX_REFERENCES}-FileRef limit")
                    active_refs.append({"depth": len(stack), "path_values": [],
                                        "relative_path_values": [], "relative_path_types": []})
                elif active_refs and len(stack) == active_refs[-1]["depth"] + 1:
                    frame = active_refs[-1]
                    if tag in ("Path", "RelativePath", "RelativePathType"):
                        value = element.attrib.get("Value")
                        if tag == "Path":
                            frame["path_values"].append(value)
                        elif tag == "RelativePath":
                            frame["relative_path_values"].append(value)
                        else:
                            frame["relative_path_types"].append(value)
                stack.append(tag)
                continue

            if tag == "FileRef":
                if not active_refs:
                    raise ValueError("malformed Set XML FileRef nesting")
                frame = active_refs.pop()
                path_values = frame["path_values"]
                if len(path_values) != 1 or not isinstance(path_values[0], str) or not path_values[0].strip():
                    raise ValueError("each FileRef must contain exactly one non-empty direct Path Value")
                for field in ("relative_path_values", "relative_path_types"):
                    if len(frame[field]) > 1 or any(not isinstance(value, str) for value in frame[field]):
                        raise ValueError(f"FileRef has malformed {field}")
                refs.append({
                    "raw_path": path_values[0],
                    "relative_path": frame["relative_path_values"][0] if frame["relative_path_values"] else None,
                    "relative_path_type": frame["relative_path_types"][0] if frame["relative_path_types"] else None,
                })
            if not stack or stack[-1] != tag:
                raise ValueError("malformed Set XML element nesting")
            stack.pop()
            element.clear()
    except ET.ParseError as exc:
        raise ValueError(f"selected .als Set XML is malformed: {exc}") from exc

    if not root_seen or live_set_count != 1:
        raise ValueError("unsupported Ableton Set XML schema; expected one Ableton root and one LiveSet")
    if active_refs or stack:
        raise ValueError("malformed Set XML has unclosed FileRef or element")
    return refs


def inspect_set_device_refs(
    path: Path,
    *,
    expected_amxd_path: Path | None = None,
    expected_amxd_sha256: str | None = None,
) -> dict:
    """Inspect FileRef paths in exactly one selected gzip-compressed Ableton Set."""
    if (expected_amxd_path is None) != (expected_amxd_sha256 is None):
        raise ValueError("expected AMXD path and SHA-256 must be supplied together")
    if expected_amxd_sha256 is not None and not SHA256_RE.fullmatch(expected_amxd_sha256):
        raise ValueError("expected AMXD SHA-256 must be 64 hexadecimal characters")

    set_path, raw, xml_bytes = _read_set(path)
    raw_refs = _extract_file_refs(xml_bytes)
    if not raw_refs:
        raise ValueError("recognized Set contains no FileRef entries")

    deduplicated: dict[str, dict] = {}
    for reference in raw_refs:
        raw_path = reference["raw_path"]
        if raw_path not in deduplicated:
            deduplicated[raw_path] = {
                "raw_path": raw_path,
                "occurrences": 0,
                "relative_paths": [],
                "relative_path_types": [],
            }
        entry = deduplicated[raw_path]
        entry["occurrences"] += 1
        if reference["relative_path"] is not None and reference["relative_path"] not in entry["relative_paths"]:
            entry["relative_paths"].append(reference["relative_path"])
        if reference["relative_path_type"] is not None and reference["relative_path_type"] not in entry["relative_path_types"]:
            entry["relative_path_types"].append(reference["relative_path_type"])

    file_refs = list(deduplicated.values())
    for entry in file_refs:
        is_amxd = Path(entry["raw_path"]).suffix.lower() == ".amxd" or PureWindowsPath(entry["raw_path"]).suffix.lower() == ".amxd"
        entry["is_amxd"] = is_amxd
        if is_amxd:
            entry["local_file"] = _local_file_report(entry["raw_path"])
    amxd_refs = [entry for entry in file_refs if entry["is_amxd"]]
    if not amxd_refs:
        raise ValueError("recognized Set contains no .amxd FileRef paths")

    assertion = {"enabled": False}
    if expected_amxd_path is not None:
        try:
            expected = expected_amxd_path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"expected AMXD path cannot be resolved: {exc}") from exc
        if expected.suffix.lower() != ".amxd" or not expected.is_file():
            raise ValueError("expected AMXD path must resolve to an existing .amxd regular file")
        expected_size = expected.stat().st_size
        if expected_size > MAX_AMXD_BYTES:
            raise ValueError(f"expected AMXD exceeds the {MAX_AMXD_BYTES}-byte hashing limit")
        expected_file = _local_file_report(str(expected))
        if len(amxd_refs) != 1:
            raise ValueError(f"expected exactly one distinct AMXD FileRef path; found {len(amxd_refs)} (ambiguous)")
        actual_ref = amxd_refs[0]
        if actual_ref["local_file"]["status"] != "hashed":
            raise ValueError(f"expected AMXD FileRef is not a readable local file: {actual_ref['raw_path']!r}")
        if actual_ref["local_file"]["resolved_path"] != str(expected):
            raise ValueError("AMXD FileRef does not resolve to the expected exact path")
        if expected_file["sha256"].lower() != expected_amxd_sha256.lower():
            raise ValueError("expected AMXD file SHA-256 does not match the supplied digest")
        if actual_ref["local_file"]["sha256"].lower() != expected_amxd_sha256.lower():
            raise ValueError("referenced AMXD SHA-256 does not match the expected digest")
        assertion = {"enabled": True, "status": "passed", "expected_amxd_path": str(expected),
                     "expected_amxd_sha256": expected_amxd_sha256.lower()}

    return {
        "schema": "codex-devtools-ableton-set-device-refs-v1",
        "evidence_level": "saved_set_file_references_only",
        "set": {"provided_path": str(path), "resolved_path": str(set_path),
                "bytes": len(raw), "sha256": sha256(raw)},
        "file_ref_count": len(raw_refs),
        "unique_file_ref_path_count": len(file_refs),
        "file_refs": file_refs,
        "amxd_references": amxd_refs,
        "expected_amxd_assertion": assertion,
        "evidence_limit": "Saved FileRef paths and current disk bytes do not prove the active Live instance or host dependency resolution.",
    }
