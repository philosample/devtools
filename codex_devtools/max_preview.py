"""Static source-derived Max patcher geometry reports and SVG schematics."""

from __future__ import annotations

import html
import json
import math
from pathlib import Path

from .maxfiles import decode_file, sha256, walk_patchers


def _label(box: dict) -> tuple[str, str]:
    text = box.get("text")
    if isinstance(text, str) and text.strip():
        return text, "text"
    attrs = box.get("saved_attribute_attributes")
    valueof = attrs.get("valueof") if isinstance(attrs, dict) else None
    longname = valueof.get("parameter_longname") if isinstance(valueof, dict) else None
    if isinstance(longname, str) and longname.strip():
        return longname, "parameter_longname"
    maxclass = box.get("maxclass")
    if isinstance(maxclass, str) and maxclass:
        return maxclass, "maxclass"
    return "(unlabelled)", "fallback"


def _rect(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    numbers = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        try:
            number = float(item)
        except OverflowError:
            return None
        if not math.isfinite(number):
            return None
        numbers.append(number)
    x, y, width, height = numbers
    if width < 0 or height < 0:
        return None
    if not math.isfinite(x + width) or not math.isfinite(y + height):
        return None
    return [x, y, width, height]


def _is_presented(value: object) -> bool:
    return value is True or (isinstance(value, (int, float)) and not isinstance(value, bool) and value == 1)


def _overlaps(boxes: list[dict]) -> list[dict]:
    ordered = sorted(boxes, key=lambda item: (item["rect"][0], item["rect"][1], item["index"]))
    active: list[dict] = []
    result = []
    for current in ordered:
        x, y, width, height = current["rect"]
        active = [item for item in active if item["rect"][0] + item["rect"][2] > x]
        for other in active:
            ox, oy, ow, oh = other["rect"]
            left, top = max(x, ox), max(y, oy)
            right, bottom = min(x + width, ox + ow), min(y + height, oy + oh)
            if right > left and bottom > top:
                result.append({
                    "first": {"index": other["index"], "id": other["id"]},
                    "second": {"index": current["index"], "id": current["id"]},
                    "intersection": {"x": left, "y": top, "width": right - left, "height": bottom - top},
                })
        active.append(current)
    result.sort(key=lambda item: (item["first"]["index"], item["second"]["index"]))
    return result


def inspect_patch(patch: dict) -> list[dict]:
    """Inspect every root or inline patcher using stored presentation geometry when available."""
    if not isinstance(patch, dict):
        raise ValueError("Max patch root must be an object")
    root = patch.get("patcher", patch)
    if not isinstance(root, dict):
        raise ValueError("Max root patcher must be an object")
    reports = []
    for patcher_path, patcher in walk_patchers(root):
        raw_boxes = patcher.get("boxes", [])
        if not isinstance(raw_boxes, list):
            raise ValueError(f"{patcher_path}: boxes must be an array")
        has_presentation = any(
            isinstance(entry, dict) and isinstance(entry.get("box"), dict) and _is_presented(entry["box"].get("presentation"))
            for entry in raw_boxes
        )
        mode = "presentation" if has_presentation else "patching"
        selected = []
        invalid_rects = []
        for index, entry in enumerate(raw_boxes):
            box = entry.get("box") if isinstance(entry, dict) else None
            if not isinstance(box, dict):
                invalid_rects.append({"index": index, "reason": "missing box object"})
                continue
            if mode == "presentation" and not _is_presented(box.get("presentation")):
                continue
            key = "presentation_rect" if mode == "presentation" else "patching_rect"
            rect = _rect(box.get(key))
            if rect is None:
                invalid_rects.append({"index": index, "id": box.get("id"), "reason": f"missing or invalid {key}"})
                continue
            label, label_source = _label(box)
            box_id = box.get("id")
            if not isinstance(box_id, (str, int)) or isinstance(box_id, bool):
                box_id = None
            selected.append({
                "index": index,
                "id": box_id,
                "type": box.get("maxclass") if isinstance(box.get("maxclass"), str) else None,
                "label": label,
                "label_source": label_source,
                "rect": rect,
            })
        if selected:
            min_x = min(item["rect"][0] for item in selected)
            min_y = min(item["rect"][1] for item in selected)
            max_x = max(item["rect"][0] + item["rect"][2] for item in selected)
            max_y = max(item["rect"][1] + item["rect"][3] for item in selected)
            bounds = {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y}
            if not all(math.isfinite(value) for value in bounds.values()):
                raise ValueError(f"{patcher_path}: geometry bounds exceed finite numeric range")
        else:
            bounds = None
        overlaps = _overlaps(selected)
        reports.append({
            "path": patcher_path,
            "mode": mode,
            "bounds": bounds,
            "box_count": len(selected),
            "invalid_rects": invalid_rects,
            "overlap_count": len(overlaps),
            "overlaps": overlaps,
            "boxes": selected,
        })
    return reports


def render_svg(patchers: list[dict], source_name: str, source_sha256: str) -> bytes:
    """Render a deterministic, plain SVG layout schematic; no Max UI or JS executes."""
    page_width = 1000.0
    margin = 20.0
    panel_gap = 16.0
    panel_header = 52.0
    panel_footer = 18.0
    usable_width = page_width - 2 * margin
    geometry_width = usable_width - 20.0
    max_geometry = 720.0
    layouts = []
    y = margin
    for patcher in patchers:
        bounds = patcher["bounds"]
        if not bounds or patcher["box_count"] == 0:
            scale = 1.0
            width = usable_width
            height = 80.0
        else:
            span_x = max(bounds["width"], 1.0)
            span_y = max(bounds["height"], 1.0)
            scale = min(1.0, geometry_width / span_x, max_geometry / span_y)
            width = usable_width
            height = max(100.0, span_y * scale + panel_header + panel_footer)
        layouts.append({"patcher": patcher, "x": margin, "y": y, "width": width, "height": height, "scale": scale})
        y += height + panel_gap
    y_offset = 48.0
    page_height = max(2 * margin, y - panel_gap + y_offset + margin)
    chunks = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_width:g}" height="{page_height:g}" viewBox="0 0 {page_width:g} {page_height:g}">',
        '<style>text{font-family:Arial,Helvetica,sans-serif;fill:#17212b}.panel{fill:#fff;stroke:#9aa8b4}.object{fill:#eaf1f7;stroke:#415567}.label{font-size:11px}.heading{font-size:14px;font-weight:bold}.meta{font-size:10px;fill:#506070}</style>',
        f'<!-- source sha256 {source_sha256}; static source geometry only -->',
        f'<text x="{margin:g}" y="{margin:g}" class="heading">{html.escape(source_name)}</text>',
        f'<text x="{margin:g}" y="{margin + 16:g}" class="meta">source sha256 {source_sha256} · static geometry schematic, not a native Max rendering</text>',
    ]
    for panel_index, layout in enumerate(layouts):
        patcher = layout["patcher"]
        px, py, width, height, scale = layout["x"], layout["y"] + y_offset, layout["width"], layout["height"], layout["scale"]
        chunks.append(f'<g id="panel-{panel_index}"><rect class="panel" x="{px:g}" y="{py:g}" width="{width:g}" height="{height:g}"/>')
        path_text = f'{patcher["path"]} · {patcher["mode"]} · {patcher["box_count"]} boxes · {patcher["overlap_count"]} rectangle overlaps'
        chunks.append(f'<text class="heading" x="{px + 10:g}" y="{py + 20:g}">{html.escape(path_text)}</text>')
        bounds = patcher["bounds"]
        if not bounds:
            chunks.append(f'<text class="meta" x="{px + 10:g}" y="{py + 40:g}">No valid rectangles selected</text>')
        else:
            chunks.append(f'<text class="meta" x="{px + 10:g}" y="{py + 38:g}">bounds x={bounds["x"]:g}, y={bounds["y"]:g}, w={bounds["width"]:g}, h={bounds["height"]:g}</text>')
            origin_x, origin_y = bounds["x"], bounds["y"]
            for box_index, box in enumerate(patcher["boxes"]):
                x, by, bw, bh = box["rect"]
                rx = px + 10 + (x - origin_x) * scale
                ry = py + panel_header + (by - origin_y) * scale
                rw, rh = max(bw * scale, 0.5), max(bh * scale, 0.5)
                clip_id = f"clip-{panel_index}-{box_index}"
                title = f'{box["id"]!s} · {box["type"] or "unknown"} · {box["label"]}'
                chunks.append(f'<defs><clipPath id="{clip_id}"><rect x="{rx + 2:g}" y="{ry + 1:g}" width="{max(rw - 4, 0):g}" height="{max(rh - 2, 0):g}"/></clipPath></defs>')
                chunks.append(f'<g><title>{html.escape(title)}</title><rect class="object" x="{rx:g}" y="{ry:g}" width="{rw:g}" height="{rh:g}"/>')
                chunks.append(f'<text class="label" x="{rx + 4:g}" y="{ry + min(15, max(rh - 2, 2)):g}" clip-path="url(#{clip_id})">{html.escape(box["label"])}</text></g>')
        chunks.append('</g>')
    chunks.append('</svg>')
    return ("\n".join(chunks) + "\n").encode("utf-8")


def preview(path: Path, out: Path | None = None) -> dict:
    source = path.expanduser().resolve(strict=True)
    data = source.read_bytes()
    file_format, _entries, patch = decode_file(data)
    if patch is None:
        raise ValueError("input does not contain a readable Max JSON root patcher")
    patchers = inspect_patch(patch)
    result = {
        "format": "codex-devtools-max-preview-v1",
        "source": {"path": str(source), "format": file_format, "bytes": len(data), "sha256": sha256(data)},
        "patchers": patchers,
        "rendering": "static source geometry schematic; box labels are stored text, parameter longname, or maxclass; no Max/JSUI execution",
    }
    if out is not None:
        output = out.expanduser().absolute()
        if output.resolve(strict=False) == source:
            raise ValueError("SVG output must not overwrite the source file")
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"refusing to overwrite existing SVG: {output}")
        svg = render_svg(patchers, source.name, result["source"]["sha256"])
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(svg)
        result["svg"] = {"path": str(output), "bytes": len(svg), "sha256": sha256(svg)}
    return result
