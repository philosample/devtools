"""Explicit, narrow reference replacement in a new sibling Set; never host control."""

from __future__ import annotations

import gzip
import html
import os
import re
from pathlib import Path

from .ableton_set import (
    SHA256_RE,
    _extract_file_refs,
    _local_file_report,
    _read_set,
    inspect_set_device_refs,
)
from .maxfiles import sha256


def copy_with_device_ref(
    source: Path,
    destination: Path,
    *,
    source_sha256: str,
    old_amxd: Path,
    old_sha256: str,
    new_amxd: Path,
    new_sha256: str,
    execute: bool = False,
) -> dict:
    """Copy a Set, changing only the uniquely selected old-device reference."""
    if any(
        not SHA256_RE.fullmatch(value)
        for value in (source_sha256, old_sha256, new_sha256)
    ):
        raise ValueError("all three SHA-256 pins must be 64 hexadecimal characters")
    resolved, raw, xml = _read_set(source)
    target = destination.expanduser().absolute()
    if (
        source.expanduser().absolute() != resolved
        or target.parent.resolve() != resolved.parent
    ):
        raise ValueError(
            "canonical source and a sibling destination in the same Project directory required"
        )
    if (
        target.parent != resolved.parent
        or target.suffix.lower() != ".als"
        or target == resolved
    ):
        raise ValueError("a distinct canonical sibling .als destination required")
    if target.exists() or target.is_symlink():
        raise ValueError("destination already exists")
    if sha256(raw) != source_sha256.lower():
        raise ValueError("source Set SHA-256 changed")
    old = old_amxd.expanduser().resolve(strict=True)
    new = new_amxd.expanduser().resolve(strict=True)
    if (
        old == new
        or old_amxd.expanduser().absolute() != old
        or new_amxd.expanduser().absolute() != new
    ):
        raise ValueError("distinct canonical device paths required")
    if old.suffix.lower() != ".amxd":
        raise ValueError("selected old device path must be an .amxd file")
    old_file = _local_file_report(str(old))
    if old_file["status"] != "hashed" or old_file["sha256"] != old_sha256.lower():
        raise ValueError("selected old AMXD SHA-256 mismatch or unavailable")
    before = inspect_set_device_refs(resolved)
    refs = _extract_file_refs(xml)
    selected_indexes = [
        index
        for index, reference in enumerate(refs)
        if reference["raw_path"] == str(old)
    ]
    if len(selected_indexes) != 1:
        raise ValueError(
            "expected exactly one FileRef matching the exact old AMXD path; "
            f"found {len(selected_indexes)}"
        )
    selected_index = selected_indexes[0]
    selected_ref = refs[selected_index]
    if selected_ref != {
        "raw_path": str(old),
        "relative_path": os.path.relpath(old, resolved.parent),
        "relative_path_type": "1",
    }:
        raise ValueError(
            "selected FileRef requires a canonical path and supported type-1 relative path"
        )
    # Reuse the inspector's bounded, stable file hashing without treating a new
    # filename or SDK handle as an artifact attestation.
    replacement = _local_file_report(str(new))
    if (
        new.suffix.lower() != ".amxd"
        or replacement["status"] != "hashed"
        or replacement["sha256"] != new_sha256.lower()
    ):
        raise ValueError("replacement AMXD SHA-256 mismatch or unavailable")
    matches = list(
        re.finditer(rb"<FileRef(?:\s[^<>]*)?>.*?</FileRef\s*>", xml, re.DOTALL)
    )
    if len(matches) != len(refs):
        raise ValueError("unsupported FileRef byte representation")
    match = matches[selected_index]
    block = match.group()
    edits = []
    for tag, value in (
        ("Path", str(new)),
        ("RelativePath", os.path.relpath(new, target.parent)),
    ):
        pattern = re.compile(rb"<" + tag.encode() + rb'\s+Value="([^"<>]*)"\s*/>')
        found = list(pattern.finditer(block))
        if len(found) != 1:
            raise ValueError("unsupported reference attribute representation")
        attribute = found[0]
        edits.append(
            (
                match.start() + attribute.start(1),
                match.start() + attribute.end(1),
                html.escape(value, quote=True).encode(),
            )
        )
    changed = xml
    for start, end, value in sorted(edits, reverse=True):
        changed = changed[:start] + value + changed[end:]
    expected = list(refs)
    expected[selected_index] = {
        "raw_path": str(new),
        "relative_path": os.path.relpath(new, target.parent),
        "relative_path_type": "1",
    }
    if _extract_file_refs(changed) != expected:
        raise ValueError("replacement reference verification failed")
    output = gzip.compress(changed, mtime=0)
    result = {
        "schema": "codex-devtools-ableton-reference-copy/v1",
        "state": "ready",
        "executed": False,
        "source": before["set"],
        "destination": str(target),
        "old_amxd_sha256": old_sha256.lower(),
        "new_amxd": replacement,
        "output_sha256": sha256(output),
        "xml_attribute_edits": 2,
        "selected_file_ref": {
            "index": selected_index,
            "old_path": str(old),
            "new_path": str(new),
        },
        "evidence_limit": "Offline reference copy only; no Live load, dependency resolution or recall proof.",
    }
    if not execute:
        return result
    # Revalidate all pinned inputs after planning and immediately before creating
    # a fresh file. O_EXCL also rejects a concurrently created symlink/hardlink.
    if _read_set(resolved)[1] != raw:
        raise ValueError("source Set changed before copy")
    inspect_set_device_refs(resolved)
    if _local_file_report(str(old)) != old_file:
        raise ValueError("selected old device changed before copy")
    if _local_file_report(str(new)) != replacement:
        raise ValueError("replacement device changed before copy")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(output)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    verified = inspect_set_device_refs(target)
    verified_xml = _read_set(target)[2]
    if _extract_file_refs(verified_xml) != expected:
        raise ValueError(
            "new Set FileRefs differ from the selected-reference preview; preserve destination for inspection"
        )
    if verified["set"]["sha256"] != result["output_sha256"]:
        raise ValueError(
            "new Set readback differs; preserve destination for inspection"
        )
    result.update(state="copied", executed=True, verification=verified)
    return result
