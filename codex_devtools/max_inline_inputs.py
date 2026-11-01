"""Opt-in static pcontrol-to-inline-p input preflight; never a native compiler."""

from __future__ import annotations

from .maxfiles import walk_patchers

UNSUPPORTED_INPUTS = {"inlet~", "in", "in~", "mc.inlet~", "mc.in~", "mcs.in~"}


def _operator(box: dict) -> str:
    text = box.get("text")
    return text.split()[0] if isinstance(text, str) and text.split() else ""


def inline_input_report(patch: dict) -> dict:
    """Check ordinal existence, not inlet role/order or runtime mutation."""
    checked, excluded, errors = [], [], []
    root = patch.get("patcher", patch)
    if not isinstance(root, dict):
        return {
            "checked": [],
            "excluded": [],
            "errors": ["root patcher must be an object"],
        }
    for path, current in walk_patchers(root):
        raw_boxes, raw_lines = current.get("boxes", []), current.get("lines", [])
        if not isinstance(raw_boxes, list) or not isinstance(raw_lines, list):
            continue  # The normal graph report diagnoses these malformed arrays.
        boxes = {
            row["box"]["id"]: row["box"]
            for row in raw_boxes
            if isinstance(row, dict)
            and isinstance(row.get("box"), dict)
            and isinstance(row["box"].get("id"), str)
        }
        for index, item in enumerate(raw_lines):
            line = item.get("patchline") if isinstance(item, dict) else None
            if not isinstance(line, dict):
                continue
            source, dest = line.get("source"), line.get("destination")
            if not all(isinstance(v, list) and len(v) == 2 for v in (source, dest)):
                continue
            if not isinstance(source[0], str) or not isinstance(dest[0], str):
                continue
            producer, target = boxes.get(source[0], {}), boxes.get(dest[0], {})
            if (
                producer.get("maxclass") != "newobj"
                or _operator(producer) != "pcontrol"
            ):
                continue
            row = {
                "path": path,
                "line": index,
                "source_id": source[0],
                "target_id": dest[0],
                "target_ordinal": dest[1],
            }
            if target.get("maxclass") != "newobj" or _operator(target) not in {
                "p",
                "patcher",
            }:
                excluded.append(
                    {**row, "reason": "outside inline newobj p/patcher scope"}
                )
                continue
            child = target.get("patcher")
            row["declared_numinlets"] = target.get("numinlets")
            if not isinstance(child, dict):
                checked.append({**row, "actual_numinlets": None, "inlet_box_ids": []})
                errors.append(
                    f"{path}: pcontrol target {dest[0]!r}: unsupported missing inline patcher"
                )
                continue
            native, unsupported = [], []
            children = child.get("boxes")
            if child.get("classnamespace", "box") != "box" or not isinstance(
                children, list
            ):
                unsupported.append(
                    "unsupported child namespace or boxes representation"
                )
            else:
                for entry in children:
                    box = entry.get("box") if isinstance(entry, dict) else None
                    if not isinstance(box, dict) or not isinstance(
                        box.get("maxclass"), str
                    ):
                        unsupported.append("malformed child box")
                    elif box.get("maxclass") == "inlet":
                        native.append(box.get("id"))
                    elif box.get("maxclass") in UNSUPPORTED_INPUTS or (
                        box.get("maxclass") == "newobj"
                        and _operator(box) in UNSUPPORTED_INPUTS | {"inlet"}
                    ):
                        unsupported.append(
                            "unsupported inlet representation: " + str(box.get("id"))
                        )
            if any(
                not isinstance(identity, str) or not identity for identity in native
            ) or len(set(native)) != len(native):
                unsupported.append("missing or duplicate dedicated inlet IDs")
            row.update(
                {
                    "actual_numinlets": None if unsupported else len(native),
                    "inlet_box_ids": native,
                }
            )
            checked.append(row)
            prefix = f"{path}: pcontrol {source[0]!r} -> {dest[0]!r} at line {index}: "
            if unsupported:
                errors.append(prefix + "; ".join(unsupported))
                continue
            if type(source[1]) is not int or source[1] != 0:
                errors.append(prefix + "unsupported pcontrol output ordinal")
            declared = target.get("numinlets")
            if type(declared) is not int or declared != len(native):
                errors.append(
                    prefix
                    + f"declared numinlets {declared!r} differs from {len(native)} actual dedicated inlet boxes"
                )
            if type(dest[1]) is not int or not 0 <= dest[1] < len(native):
                errors.append(
                    prefix
                    + f"target ordinal {dest[1]!r} has no actual dedicated inlet ({len(native)} present)"
                )
    return {"checked": checked, "excluded": excluded, "errors": errors}
