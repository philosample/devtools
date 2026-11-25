"""Render a .maxpat to SVG (and PNG on macOS via qlmanage) so layouts can be
eyeballed without opening Max.  It's an approximation of Max's look - good for
judging wiring and spacing, not pixel-exact fonts.

Usage:
  python3 render.py patch.maxpat -o preview.svg [--png] [--presentation]
                    [--subpatcher obj-12] [--scale 1.0]
"""

import argparse
import html
import os
import shutil
import subprocess
import tempfile
from itertools import combinations
from pathlib import Path

from .maxpat import Patcher, load, net_kind, walk_patchers

NET = {"audio": "#e0b31b", "mc": "#f07b2c", "video": "#58b368", "control": "#cfcfcf"}


def rgba(c, default):
    if not c or not isinstance(c, list) or len(c) < 3:
        return default
    a = c[3] if len(c) > 3 else 1
    return f"rgba({int(c[0] * 255)},{int(c[1] * 255)},{int(c[2] * 255)},{a:.3f})"


def render(P, presentation=False, scale=1.0, show_grid=True, square=False):
    key = "presentation_rect" if presentation else "patching_rect"
    boxes = [
        b
        for b in P.boxes
        if (not presentation or b.d.get("presentation")) and b.d.get(key)
    ]
    if not boxes:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60"><text x="10" y="30">empty</text></svg>'
    pts = []
    for b in boxes:
        x, y, w, h = b.d[key]
        pts += [(x, y), (x + w, y + h)]
    if not presentation:
        for ln in P.lines:
            pts += P.polyline(ln)
    minx = min(p[0] for p in pts) - 30
    miny = min(p[1] for p in pts) - 30
    maxx = max(p[0] for p in pts) + 30
    maxy = max(p[1] for p in pts) + 30
    W, H = maxx - minx, maxy - miny
    if square:  # quicklook thumbnails crop to a square; pad instead
        if W > H:
            miny -= (W - H) / 2
            H = W
        else:
            minx -= (H - W) / 2
            W = H
    bg = rgba(
        P.d.get("locked_bgcolor" if presentation else "editing_bgcolor"), "#585858"
    )
    header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W * scale:.0f}" height="{H * scale:.0f}" '
        f'viewBox="{minx:.1f} {miny:.1f} {W:.1f} {H:.1f}" font-family="Arial, Helvetica, sans-serif">'
    )
    o = [header, f'<rect x="{minx}" y="{miny}" width="{W}" height="{H}" fill="{bg}"/>']
    gx, gy = P.grid
    if show_grid and not presentation:
        o.append(
            f'<defs><pattern id="g" width="{gx}" height="{gy}" patternUnits="userSpaceOnUse">'
            f'<path d="M {gx} 0 L 0 0 0 {gy}" fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="0.6"/>'
            f'</pattern></defs><rect x="{minx}" y="{miny}" width="{W}" height="{H}" fill="url(#g)"/>'
        )
    # backgrounds first (panels), then cords, then boxes - roughly Max's z-order
    for b in boxes:
        if b.maxclass == "panel" or b.d.get("background"):
            draw_box(o, b, key)
    if not presentation:
        for ln in P.lines:
            s = P.by_id[ln.src[0]]
            kind = net_kind(s, ln.src[1])
            col = rgba(ln.d.get("color"), NET[kind])
            width = 2.2 if kind in ("audio", "mc") else 1.4
            dash = (
                ' stroke-dasharray="5 2"'
                if kind == "audio" and not ln.d.get("color")
                else ""
            )
            path = " ".join(f"{x:.2f},{y:.2f}" for x, y in P.polyline(ln))
            o.append(
                f'<polyline points="{path}" fill="none" stroke="#1b1b1b" stroke-width="{width + 1.6}" '
                f'stroke-linejoin="round" opacity="0.55"/>'
            )
            o.append(
                f'<polyline points="{path}" fill="none" stroke="{col}" stroke-width="{width}" '
                f'stroke-linejoin="round"{dash}/>'
            )
    for b in boxes:
        if not (b.maxclass == "panel" or b.d.get("background")):
            draw_box(o, b, key, ports=not presentation)
    o.append("</svg>")
    return "\n".join(o)


def draw_box(o, b, key, ports=True):
    x, y, w, h = b.d[key]
    mc = b.maxclass
    fs = float(b.d.get("fontsize", 12))
    if mc == "panel":
        r = b.d.get("rounded", 8)
        fill = rgba(b.d.get("bgcolor"), "rgba(80,80,80,0.6)")
        stroke = (
            rgba(b.d.get("bordercolor"), "rgba(0,0,0,0)")
            if b.d.get("border")
            else "none"
        )
        sw = b.d.get("border", 0) or 0
        o.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )
        return
    if mc == "comment":
        col = rgba(b.d.get("textcolor"), "#f2f2f2")
        lines = []
        per = max(1, int((w - 8) / (5.6 * fs / 12)))
        for para in str(b.d.get("text", "")).split("\n"):
            words, cur = para.split(" "), ""
            for wd in words:
                if cur and len(cur) + 1 + len(wd) > per:
                    lines.append(cur)
                    cur = wd
                else:
                    cur = (cur + " " + wd).strip()
            lines.append(cur)
        weight = "bold" if b.d.get("fontface") in (1, 3) else "normal"
        for i, t in enumerate(lines):
            o.append(
                f'<text x="{x + 4}" y="{y + fs + 3 + i * fs * 1.25:.1f}" font-size="{fs}" fill="{col}" font-weight="{weight}">{html.escape(t)}</text>'
            )
        return
    fill = rgba(b.d.get("bgcolor") or b.d.get("bgfillcolor_color"), "#333333")
    if mc == "message":
        fill = rgba(b.d.get("bgcolor"), "#4a4a4a")
    o.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{4 if mc in ("message",) else 2}" fill="{fill}" '
        f'stroke="#1e1e1e" stroke-width="1"/>'
    )
    label = b.d.get("text")
    pv = (b.d.get("saved_attribute_attributes") or {}).get("valueof") or {}
    if label is None and pv.get("parameter_shortname"):
        label = pv["parameter_shortname"]
    if label is None:
        label = {"toggle": "", "button": "", "number": "0", "flonum": "0."}.get(mc, mc)
    if mc in ("toggle", "button"):
        cx, cy = x + w / 2, y + h / 2
        if mc == "button":
            o.append(
                f'<circle cx="{cx}" cy="{cy}" r="{min(w, h) / 2 - 4}" fill="none" stroke="#bbb" stroke-width="1.5"/>'
            )
        else:
            o.append(
                f'<rect x="{x + 4}" y="{y + 4}" width="{w - 8}" height="{h - 8}" fill="none" stroke="#bbb"/>'
            )
    elif label:
        col = rgba(b.d.get("textcolor"), "#ffffff")
        txt = html.escape(str(label))
        o.append(
            f'<svg x="{x}" y="{y}" width="{w}" height="{h}"><text x="5" y="{min(h, 22) / 2 + fs * 0.36:.1f}" '
            f'font-size="{fs}" fill="{col}">{txt}</text></svg>'
        )
    if ports:
        for i in range(b.nin):
            px, py = b.inlet_xy(i)
            o.append(
                f'<rect x="{px - 4:.1f}" y="{py - 0.5:.1f}" width="8" height="2.5" fill="#9ad"/>'
            )
        for i in range(b.nout):
            px, py = b.outlet_xy(i)
            o.append(
                f'<rect x="{px - 4:.1f}" y="{py - 2:.1f}" width="8" height="2.5" fill="#9ad"/>'
            )


def check_output_paths(input_path, outputs):
    outputs = list(map(Path, outputs))
    for output in outputs:
        if output.is_dir():
            raise ValueError(f"output path must be a file, not a directory: {output}")
    paths = [Path(input_path), *outputs]
    for first, second in combinations(paths, 2):
        try:
            aliases = first.samefile(second)
        except FileNotFoundError:
            aliases = first.resolve() == second.resolve()
        if aliases:
            raise ValueError(
                f"output paths must be distinct from the input file and each other: {second}"
            )


def to_png(svg_path, png_path, size=2000):
    quick_look = shutil.which("qlmanage")
    if not quick_look:
        return False
    png_path = Path(png_path)
    with tempfile.TemporaryDirectory() as directory:
        try:
            subprocess.run(
                [quick_look, "-t", "-s", str(size), "-o", directory, svg_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        thumbnail = Path(directory) / (Path(svg_path).name + ".png")
        if not thumbnail.is_file():
            return False
        try:
            with tempfile.TemporaryDirectory(dir=png_path.parent) as staging_dir:
                staged_png = Path(staging_dir) / png_path.name
                shutil.copyfile(thumbnail, staged_png)
                os.replace(staged_png, png_path)
        except OSError:
            return False
    return True


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("patch")
    ap.add_argument("-o", "--output", help="output .svg (default: alongside patch)")
    ap.add_argument(
        "--png", action="store_true", help="also write a PNG (macOS qlmanage)"
    )
    ap.add_argument("--presentation", action="store_true")
    ap.add_argument(
        "--subpatcher", help="box ID or full path of an embedded subpatcher"
    )
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()
    doc = load(a.patch)
    P = Patcher(doc["patcher"])
    if a.subpatcher:
        matches = [
            child
            for parent in walk_patchers(doc["patcher"])
            for box, child in parent.subpatchers()
            if a.subpatcher in (box.id, child.path)
        ]
        if not matches:
            ap.error(f"unknown embedded subpatcher: {a.subpatcher}")
        if len(matches) > 1:
            choices = ", ".join(child.path for child in matches)
            ap.error(
                f"ambiguous embedded subpatcher {a.subpatcher!r}; use its full path: {choices}"
            )
        P = matches[0]
    default_name = (
        os.path.splitext(a.patch)[0]
        + (".presentation" if a.presentation else "")
        + ".svg"
    )
    out = Path(a.output or default_name)
    png = out.with_suffix(".png")
    try:
        check_output_paths(a.patch, [out, png] if a.png else [out])
    except ValueError as exc:
        ap.error(str(exc))
    out.write_text(render(P, a.presentation, a.scale), encoding="utf-8")
    print(out)
    if a.png:
        with tempfile.TemporaryDirectory() as directory:
            square = Path(directory) / "preview.svg"
            square.write_text(
                render(P, a.presentation, a.scale, square=True), encoding="utf-8"
            )
            ok = to_png(square, png)
        print(png if ok else "PNG conversion unavailable; SVG saved")


if __name__ == "__main__":
    main()
