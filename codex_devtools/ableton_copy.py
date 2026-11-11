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

# This opt-in diagnostic subset deliberately does not decode or normalize paths.
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9_-](?:[A-Za-z0-9._ -]*[A-Za-z0-9_-])?\Z")
MAX_EXPECTED_OCCURRENCES = 16


def _sibling_bundle_relative_path(old: Path, new: Path, reference: dict) -> dict:
    if (
        old.name != new.name
        or old.parent == new.parent
        or old.parent.parent != new.parent.parent
    ):
        raise ValueError(
            "diagnostic mode requires same-basename AMXDs in distinct canonical sibling bundles"
        )
    relative = reference["relative_path"]
    if reference["relative_path_type"] != "1" or not isinstance(relative, str):
        raise ValueError("diagnostic mode requires exactly one type-1 relative path")
    # Bound both traversal and total input; '..' is allowed only in a leading run.
    # No base is chosen; prefix components are not resolved or collapsed.
    if len(relative) > 4096:
        raise ValueError("diagnostic relative path exceeds 4096 characters")
    parts = relative.split("/")
    parents = 0
    while parents < len(parts) and parts[parents] == "..":
        parents += 1
    ordinary = parts[parents:]
    if (
        parents > 32
        or len(parts) > 64
        or len(ordinary) < 2
        or any(not _SAFE_COMPONENT.fullmatch(part) for part in ordinary)
        or any(
            not _SAFE_COMPONENT.fullmatch(part)
            for part in (old.parent.name, new.parent.name, old.name)
        )
        or parts[-2:] != [old.parent.name, old.name]
        or any(
            part in (old.parent.name, new.parent.name, old.name) for part in parts[:-2]
        )
    ):
        raise ValueError(
            "unsupported diagnostic relative path or ambiguous sibling-bundle tail"
        )
    old_tail = f"{old.parent.name}/{old.name}"
    new_tail = f"{new.parent.name}/{new.name}"
    prefix = relative[: -len(old_tail)]
    return {
        "old_relative_path": relative,
        "new_relative_path": prefix + new_tail,
        "preserved_prefix": prefix,
        "old_relative_path_bytes_hex": relative.encode("ascii").hex(),
        "new_relative_path_bytes_hex": (prefix + new_tail).encode("ascii").hex(),
        "preserved_prefix_bytes_hex": prefix.encode("ascii").hex(),
        "old_tail": old_tail,
        "new_tail": new_tail,
        "base_policy": "Relative prefix bytes preserved syntactically; relative base/resolution unverified.",
    }


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
    diagnostic_sibling_bundle: bool = False,
    expected_occurrences: int = 1,
) -> dict:
    """Copy a Set, changing only the explicitly selected old-device references."""
    if (
        type(expected_occurrences) is not int
        or not 1 <= expected_occurrences <= MAX_EXPECTED_OCCURRENCES
    ):
        raise ValueError(
            f"expected FileRef occurrences must be an integer from 1 to {MAX_EXPECTED_OCCURRENCES}"
        )
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
    if len(selected_indexes) != expected_occurrences:
        raise ValueError(
            "exact old AMXD path match count differs from --expected-occurrences; "
            f"declared {expected_occurrences}, found {len(selected_indexes)}"
        )
    selected_file_refs = []
    relative_transforms = []
    for selected_index in selected_indexes:
        selected_ref = refs[selected_index]
        if diagnostic_sibling_bundle:
            relative_transform = _sibling_bundle_relative_path(old, new, selected_ref)
            new_relative = relative_transform["new_relative_path"]
            relative_transforms.append(relative_transform)
        else:
            if selected_ref != {
                "raw_path": str(old),
                "relative_path": os.path.relpath(old, resolved.parent),
                "relative_path_type": "1",
            }:
                raise ValueError(
                    "each selected FileRef requires a canonical path and supported type-1 relative path"
                )
            new_relative = os.path.relpath(new, target.parent)
        selected_file_refs.append(
            {
                "index": selected_index,
                "old_path": str(old),
                "new_path": str(new),
                "old_relative_path": selected_ref["relative_path"],
                "new_relative_path": new_relative,
            }
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
    edits = []
    expected = list(refs)
    for selected_file_ref in selected_file_refs:
        selected_index = selected_file_ref["index"]
        selected_ref = refs[selected_index]
        match = matches[selected_index]
        block = match.group()
        for tag, value in (
            ("Path", str(new)),
            ("RelativePath", selected_file_ref["new_relative_path"]),
        ):
            pattern = re.compile(rb"<" + tag.encode() + rb'\s+Value="([^"<>]*)"\s*/>')
            found = list(pattern.finditer(block))
            if len(found) != 1:
                raise ValueError("unsupported reference attribute representation")
            attribute = found[0]
            if (
                diagnostic_sibling_bundle
                and tag == "RelativePath"
                and attribute.group(1) != selected_ref["relative_path"].encode("ascii")
            ):
                raise ValueError(
                    "diagnostic relative path requires literal safe ASCII attribute bytes"
                )
            replacement_bytes = html.escape(value, quote=True).encode()
            if tag == "RelativePath":
                selected_file_ref["old_relative_path_attribute_bytes_hex"] = (
                    attribute.group(1).hex()
                )
                selected_file_ref["new_relative_path_attribute_bytes_hex"] = (
                    replacement_bytes.hex()
                )
            edits.append(
                (
                    match.start() + attribute.start(1),
                    match.start() + attribute.end(1),
                    replacement_bytes,
                )
            )
        expected[selected_index] = {
            "raw_path": str(new),
            "relative_path": selected_file_ref["new_relative_path"],
            "relative_path_type": "1",
        }
    changed = xml
    for start, end, value in sorted(edits, reverse=True):
        changed = changed[:start] + value + changed[end:]
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
        "xml_attribute_edits": 2 * expected_occurrences,
        "expected_file_ref_occurrences": expected_occurrences,
        "actual_file_ref_occurrences": len(selected_indexes),
        "relative_path_policy": "diagnostic_sibling_bundle"
        if diagnostic_sibling_bundle
        else "strict_set_directory",
        "selected_file_refs": selected_file_refs,
        "evidence_limit": "Offline reference copy only; no Live load, dependency resolution or recall proof.",
    }
    if len(selected_file_refs) == 1:
        result["selected_file_ref"] = {
            key: selected_file_refs[0][key] for key in ("index", "old_path", "new_path")
        }
    if diagnostic_sibling_bundle:
        result["relative_path_transforms"] = [
            {"index": selected["index"], **transform}
            for selected, transform in zip(
                selected_file_refs, relative_transforms, strict=True
            )
        ]
        if len(relative_transforms) == 1:
            result["relative_path_transform"] = relative_transforms[0]
        result["evidence_limit"] += (
            " Diagnostic only: relative base/resolution unverified; no Live load/dependency/recall proof; native exact component/manifest pins required before effects."
        )
    if not execute:
        return result
    # Revalidate all pinned inputs after planning and immediately before creating
    # a fresh file. O_EXCL also rejects a concurrently created symlink/hardlink.
    current_source, current_raw, _ = _read_set(resolved)
    if current_source != resolved or current_raw != raw:
        raise ValueError("source Set changed before copy")
    inspect_set_device_refs(resolved)
    if _local_file_report(str(old)) != old_file:
        raise ValueError("selected old device changed before copy")
    if _local_file_report(str(new)) != replacement:
        raise ValueError("replacement device changed before copy")
    if target.parent.resolve(strict=True) != resolved.parent:
        raise ValueError("destination parent changed before copy")
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
