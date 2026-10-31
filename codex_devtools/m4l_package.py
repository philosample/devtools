"""Build an unfrozen, offline M4L MIDI-effect candidate with declared sidecars."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import struct
import unicodedata

from .maxfiles import decode_file, graph_report, sha256, walk_patchers


MANIFEST_NAME = "manifest.json"
M4L_PROJECT_TYPE = int.from_bytes(b"mmmm", "big")


def _has_midi_effect_stream(patcher: dict) -> bool:
    boxes = patcher.get("boxes")
    if not isinstance(boxes, list):
        return False
    objects = {
        item.get("box", {}).get("text")
        for item in boxes
        if isinstance(item, dict)
        and isinstance(item.get("box"), dict)
        and item["box"].get("maxclass") == "newobj"
    }
    return {"midiin", "midiout"}.issubset(objects)


def _validate_m4l_template_header(data: bytes) -> None:
    """Restrict packaging to the observed raw-patcher MIDI-effect wrapper."""
    if (
        len(data) < 32
        or data[:4] != b"ampf"
        or struct.unpack_from("<I", data, 4)[0] != 4
        or data[8:12] != b"mmmm"
        or data[12:16] != b"meta"
        or struct.unpack_from("<I", data, 16)[0] != 4
        or struct.unpack_from("<I", data, 20)[0] not in (0, 1)
        or data[24:28] != b"ptch"
        or struct.unpack_from("<I", data, 28)[0] != len(data) - 32
    ):
        raise ValueError("template has an unsupported AMXD header structure")


def _validate_midi_template(template_patch: dict, template_entries: list[dict]) -> None:
    root = template_patch.get("patcher")
    if not isinstance(root, dict):
        raise ValueError("template root has no Max patcher object")
    project = root.get("project")
    if not isinstance(project, dict) or project.get("amxdtype") != M4L_PROJECT_TYPE:
        raise ValueError("template metadata does not identify the supported M4L project type (FourCC 'mmmm')")
    if not _has_midi_effect_stream(root):
        raise ValueError("template root is not a MIDI-effect wrapper: expected top-level newobj 'midiin' and 'midiout'")
    if template_entries:
        raise ValueError("template contains embedded archive assets; packaging only supports an unfrozen raw-patcher template")


def _safe_dependency_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value or ":" in value:
        raise ValueError(f"{label} must be a safe single filename")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or path.name in (".", ".."):
        raise ValueError(f"{label} must be a safe single filename: {value!r}")
    return value


def _filename_collision_key(value: str) -> str:
    return unicodedata.normalize("NFD", value).casefold()


def _load_dependency_allowlist(path: Path) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid dependency allowlist {path}: {exc}") from exc
    if not isinstance(rows, list):
        raise ValueError("dependency allowlist must be a JSON array")
    results = []
    names: set[str] = set()
    collision_keys: set[str] = set()
    sources: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"name", "source"}:
            raise ValueError(f"dependency allowlist row {index} must contain only name and source")
        name = _safe_dependency_name(row["name"], f"dependency row {index} name")
        collision_key = _filename_collision_key(name)
        source = row["source"]
        if not isinstance(source, str) or not source or "\\" in source or ":" in source:
            raise ValueError(f"dependency row {index} source must be a relative POSIX path")
        source_path = PurePosixPath(source)
        if source_path.is_absolute() or any(part in ("", ".", "..") for part in source_path.parts):
            raise ValueError(f"dependency row {index} source must not escape the source root: {source!r}")
        if name in names or collision_key in collision_keys:
            raise ValueError(f"duplicate dependency destination name {name!r}")
        if source in sources:
            raise ValueError(f"duplicate dependency source path {source!r}")
        names.add(name)
        collision_keys.add(collision_key)
        sources.add(source)
        results.append({"name": name, "source": source})
    return results


def _declared_dependencies(patch: dict, label: str) -> dict[str, object]:
    """Collect dependency declarations from a root and all inline patchers."""
    root = patch.get("patcher", patch)
    if not isinstance(root, dict):
        raise ValueError(f"{label} has no Max patcher object")
    referenced: dict[str, object] = {}
    for patcher_path, current in walk_patchers(root):
        cache = current.get("dependency_cache", [])
        if not isinstance(cache, list):
            raise ValueError(f"{label} {patcher_path} dependency_cache must be an array")
        for index, entry in enumerate(cache):
            if not isinstance(entry, dict):
                raise ValueError(f"{label} {patcher_path} dependency_cache entry {index} must be an object")
            name = _safe_dependency_name(entry.get("name"), f"{label} {patcher_path} dependency_cache entry {index} name")
            bootpath = entry.get("bootpath", "")
            if bootpath not in (None, "", "."):
                raise ValueError(f"dependency {name!r} has a nonlocal bootpath; sidecar packaging requires the output directory")
            bootpath = "."  # Normalize equivalent local encodings before duplicate checks.
            if name in referenced and referenced[name] != bootpath:
                raise ValueError(f"dependency {name!r} has conflicting bootpaths in {label}")
            referenced[name] = bootpath
    return referenced


def _dependency_inputs(source_root: Path, source_patch: dict, allowlist: list[dict]) -> list[dict]:
    by_name = {item["name"]: item for item in allowlist}
    resolved_root = source_root.resolve(strict=True)
    inputs_by_name = {}
    for item in sorted(allowlist, key=lambda row: row["name"]):
        lexical = source_root / Path(*PurePosixPath(item["source"]).parts)
        try:
            resolved = lexical.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"dependency source is missing: {item['source']}") from exc
        if not resolved.is_relative_to(resolved_root):
            raise ValueError(f"dependency source escapes declared source root: {item['source']}")
        if not resolved.is_file():
            raise ValueError(f"dependency source is not a regular file: {item['source']}")
        data = resolved.read_bytes()
        if data[:4] in (b"mx@c", b"ampf") or resolved.suffix.lower() in (".mxf", ".amxd"):
            raise ValueError(f"dependency {item['name']!r} is a packaged/frozen Max container; only sidecar source/assets are supported")
        inputs_by_name[item["name"]] = {"name": item["name"], "source": item["source"], "path": resolved, "data": data}

    referenced = _declared_dependencies(source_patch, "source patcher")
    pending = list(referenced)
    visited_sidecars = set()
    while pending:
        name = pending.pop()
        item = by_name.get(name)
        if item is None:
            continue
        if not name.lower().endswith(".maxpat") or name in visited_sidecars:
            continue
        visited_sidecars.add(name)
        try:
            sidecar_patch = json.loads(inputs_by_name[name]["data"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Max patcher dependency {name!r} is not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(sidecar_patch, dict) or not isinstance(sidecar_patch.get("patcher"), dict):
            raise ValueError(f"Max patcher dependency {name!r} must have an object-valued patcher root")
        nested = _declared_dependencies(sidecar_patch, f"Max patcher dependency {name!r}")
        for nested_name, nested_bootpath in nested.items():
            if nested_name in referenced and referenced[nested_name] != nested_bootpath:
                raise ValueError(f"dependency {nested_name!r} has conflicting bootpaths across patchers")
            if nested_name not in referenced:
                referenced[nested_name] = nested_bootpath
                pending.append(nested_name)

    listed = set(by_name)
    expected = set(referenced)
    if listed != expected:
        missing = sorted(expected - listed)
        extra = sorted(listed - expected)
        raise ValueError(f"dependency allowlist must match all source and nested dependency_cache entries (missing={missing}, unreferenced={extra})")
    return [inputs_by_name[item["name"]] for item in sorted(allowlist, key=lambda row: row["name"])]


def _canonical_patch_hash(patch: dict) -> str:
    encoded = json.dumps(patch, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def package_m4l_candidate(
    source_root: Path,
    source: Path,
    template: Path,
    dependency_allowlist: Path,
    out: Path,
) -> dict:
    """Create a new unfrozen MIDI-effect AMXD candidate and loose sidecars.

    This verifies the supported wrapper roundtrip and declared dependency
    closure. It does not claim Max/Live load or runtime compatibility.
    """
    source_root = source_root.expanduser().resolve(strict=True)
    if not source_root.is_dir():
        raise ValueError("source root must be a directory")
    source = source.expanduser()
    if not source.is_absolute():
        source = source_root / source
    source = source.resolve(strict=True)
    if not source.is_relative_to(source_root) or not source.is_file():
        raise ValueError("source maxpat must be a regular file inside the declared source root")
    if source.suffix.lower() != ".maxpat":
        raise ValueError("source must be a .maxpat file")

    template = template.expanduser().resolve(strict=True)
    template_bytes = template.read_bytes()
    try:
        template_format, template_entries, template_patch = decode_file(template_bytes)
    except ValueError as exc:
        raise ValueError(f"template is not a supported AMXD: {exc}") from exc
    if template_format != "amxd" or template_patch is None:
        raise ValueError("template must be a readable raw-patcher AMXD")
    _validate_m4l_template_header(template_bytes)
    _validate_midi_template(template_patch, template_entries)

    source_bytes = source.read_bytes()
    try:
        source_patch = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"source maxpat is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(source_patch, dict) or not isinstance(source_patch.get("patcher"), dict):
        raise ValueError("source maxpat must have an object-valued patcher root")
    source_project = source_patch["patcher"].get("project")
    if source_project is not None and not isinstance(source_project, dict):
        raise ValueError("source patcher project metadata must be an object when present")
    if source_project is not None and "amxdtype" in source_project and source_project["amxdtype"] != M4L_PROJECT_TYPE:
        raise ValueError("source metadata conflicts with the supported M4L project type (FourCC 'mmmm')")
    project_metadata_supplied = source_project is None or "amxdtype" not in source_project
    if project_metadata_supplied:
        # Compiler-generated wrappers may omit project metadata. Keep the
        # validated template's project envelope, not its patch or dependencies.
        source_patch["patcher"]["project"] = {
            **template_patch["patcher"]["project"],
            **(source_project or {}),
        }
    if not _has_midi_effect_stream(source_patch["patcher"]):
        raise ValueError("source root is not a MIDI-effect wrapper: expected top-level 'midiin' and 'midiout'")
    graph = graph_report(source_patch)
    if graph["errors"]:
        raise ValueError("source Max graph is invalid: " + "; ".join(graph["errors"]))

    allowlist = _load_dependency_allowlist(dependency_allowlist.expanduser().resolve(strict=True))
    dependencies = _dependency_inputs(source_root, source_patch, allowlist)
    artifact_name = source.stem + ".amxd"
    _safe_dependency_name(artifact_name, "candidate artifact name")
    names = [item["name"] for item in dependencies]
    reserved = {_filename_collision_key(artifact_name), _filename_collision_key(MANIFEST_NAME)}
    if any(_filename_collision_key(name) in reserved for name in names):
        raise ValueError("candidate artifact or manifest name collides with a dependency")

    body = (json.dumps(source_patch, ensure_ascii=False, indent=2) + "\n").encode("utf-8") + b"\0"
    if len(body) > 0xFFFFFFFF:
        raise ValueError("candidate root exceeds the AMXD header size limit")
    candidate = template_bytes[:28] + struct.pack("<I", len(body)) + body
    output_format, output_entries, output_patch = decode_file(candidate)
    if output_format != "amxd" or output_entries or output_patch != source_patch:
        raise ValueError("candidate AMXD roundtrip did not preserve the prepared root patch")
    output_graph = graph_report(output_patch)
    if output_graph["errors"]:
        raise ValueError("candidate AMXD graph failed validation: " + "; ".join(output_graph["errors"]))

    out = out.expanduser().absolute()
    if os.path.lexists(out):
        raise FileExistsError(f"output directory already exists: {out}")
    if out.resolve(strict=False).is_relative_to(source_root):
        raise ValueError("output directory must be outside the declared source root")

    manifest = {
        "format": "codex-devtools-m4l-candidate-v1",
        "scope": "offline unfrozen candidate packaging; not a Live/Max runtime result",
        "source": {"path": source.relative_to(source_root).as_posix(), "bytes": len(source_bytes), "sha256": sha256(source_bytes)},
        "template": {"filename": template.name, "bytes": len(template_bytes), "sha256": sha256(template_bytes),
            "project_type_fourcc": "mmmm", "midi_effect_stream": ["midiin", "midiout"], "embedded_template_entries": 0},
        "dependencies": [
            {"name": item["name"], "source": item["source"], "storage": "sidecar", "embedded": False,
             "bytes": len(item["data"]), "sha256": sha256(item["data"])}
            for item in dependencies
        ],
        "artifact": {"filename": artifact_name, "bytes": len(candidate), "sha256": sha256(candidate),
            "root_patch_sha256": _canonical_patch_hash(output_patch)},
        "roundtrip": {"passed": True,
            "root_equal_to_source": output_patch == json.loads(source_bytes.decode("utf-8")),
            "project_metadata_supplied_from_template": project_metadata_supplied,
            "graph_errors": [], "graph_counts": output_graph["counts"]},
        "freeze": {"performed": False, "embedded_dependencies": False},
    }
    files = [(artifact_name, candidate)] + [(item["name"], item["data"]) for item in dependencies]
    files.append((MANIFEST_NAME, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")))

    out.mkdir(parents=True, exist_ok=False)
    created = []
    try:
        for name, data in files:
            path = out / name
            with path.open("xb") as stream:
                created.append(path)
                stream.write(data)
    except BaseException:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        try:
            out.rmdir()
        except OSError:
            pass
        raise
    return manifest
