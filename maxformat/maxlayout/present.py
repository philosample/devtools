"""Lay out a patch's presentation view as a tidy control surface.

Controls are grouped into sections by the module panel they live in (in the
patching view).  Each section becomes a row of controls under a title, the module
panel is reused as the section background (panels may have a different
presentation_rect from their patching_rect), and labels that sit beside a control
in the patching view are reused underneath it in presentation.

Usage:
  python3 present.py in.maxpat -o out.maxpat [--ids obj-3,obj-7 | --auto]
                     [--cols 6] [--width 900] [--no-open]

  (default)  lay out boxes that already have "presentation": 1
  --ids      lay out exactly these boxes (and mark them presentation)
  --auto     every user-interface object (dials, sliders, toggles, gain, menus...)
  --no-open  don't set the patcher to open in presentation mode
"""
import argparse, os, sys
from .maxpat import load, save, Patcher, snap, PITCH, text_width
from .layout import comment_anchors

NOT_UI = {'newobj', 'message', 'comment', 'panel', 'bpatcher', 'jsui', 'fpic', 'inlet', 'outlet'}
SELF_LABELLED = ('live.dial', 'live.slider', 'live.gain~', 'live.text', 'live.tab')


def section_of(P, b):
    x, y, w, h = b.rect
    cx, cy = x + w / 2, y + h / 2
    best = None
    for p in P.boxes:
        if p.maxclass != 'panel':
            continue
        px, py, pw, ph = p.rect
        if px <= cx <= px + pw and py <= cy <= py + ph and (best is None or pw * ph < best.rect[2] * best.rect[3]):
            best = p
    return best


def present(P, ids=None, auto=False, cols=6, width=900.0, open_view=True, margin=15.0):
    if ids:
        ctrls = [P.by_id[i] for i in ids if i in P.by_id]
    elif auto:
        ctrls = [b for b in P.boxes if b.maxclass not in NOT_UI]
    else:
        ctrls = [b for b in P.boxes if b.d.get('presentation') and b.maxclass not in ('panel', 'comment')]
    if not ctrls:
        return 0
    anchors = comment_anchors(P)
    label_of = {}
    for cid, (bid, _, _) in anchors.items():
        label_of.setdefault(bid, cid)
    sections = {}
    for b in ctrls:
        p = section_of(P, b)
        sections.setdefault(p.id if p else None, []).append(b)
    keys = sorted(sections, key=lambda k: (0, 0) if k is None else (P.by_id[k].rect[1], P.by_id[k].rect[0]))
    titles = {}
    for k in keys:
        if k is None:
            continue
        px, py, pw, ph = P.by_id[k].rect
        for c in P.boxes:
            if c.maxclass == 'comment' and px <= c.rect[0] <= px + pw and py <= c.rect[1] <= py + 24:
                titles[k] = c
    x0 = y0 = margin
    row_h = 0.0
    row_panels = []

    def close_row():
        for p in row_panels:          # sections in one row share a height
            p.d['presentation_rect'][3] = snap(row_h, PITCH)
        row_panels.clear()
    for k in keys:
        items = sorted(sections[k], key=lambda b: (b.rect[1], b.rect[0]))
        title_h = 26.0 if k in titles else 0.0
        pad = 12.0 if k is not None else 0.0
        cells = []
        for b in items:
            lab = P.by_id[label_of[b.id]] if b.id in label_of and b.maxclass not in SELF_LABELLED else None
            lw = text_width(lab.text, float(lab.d.get('fontsize', 12))) if lab else 0
            cells.append((b, lab, max(b.rect[2], lw)))
        # rows of up to `cols`
        rows = [cells[i:i + cols] for i in range(0, len(cells), cols)]
        sec_w = max(sum(c[2] for c in r) + PITCH * 2 * (len(r) - 1) for r in rows) + 2 * pad
        sec_w = max(sec_w, (text_width(titles[k].text) + 2 * pad) if k in titles else 0)
        if x0 + sec_w > width and x0 > margin:
            close_row()
            x0 = margin
            y0 = snap(y0 + row_h + PITCH * 2, PITCH)
            row_h = 0.0
        y = y0 + pad + title_h
        for r in rows:
            x = x0 + pad
            rh = 0.0
            for b, lab, cw in r:
                bx, by, bw, bh = b.rect
                b.d['presentation'] = 1
                b.d['presentation_rect'] = [snap(x, PITCH), snap(y, PITCH), bw, bh]
                h = bh
                if lab is not None:
                    lab.d['presentation'] = 1
                    lab.d['presentation_rect'] = [snap(x - 4, PITCH), snap(y + bh + 2, PITCH),
                                                  max(cw, 40.0), lab.rect[3]]
                    h += lab.rect[3] + 2
                rh = max(rh, h)
                x += cw + PITCH * 2
            y += rh + PITCH * 2
        sec_h = y - y0 - PITCH * 2 + pad
        if k is not None:
            p = P.by_id[k]
            p.d['presentation'] = 1
            p.d['presentation_rect'] = [snap(x0, PITCH), snap(y0, PITCH), snap(sec_w, PITCH), snap(sec_h, PITCH)]
            row_panels.append(p)
            if k in titles:
                t = titles[k]
                t.d['presentation'] = 1
                t.d['presentation_rect'] = [snap(x0 + pad - 2, PITCH), snap(y0 + 6, PITCH), sec_w - 2 * pad, t.rect[3]]
        x0 = snap(x0 + sec_w + PITCH * 2, PITCH)
        row_h = max(row_h, sec_h)
    close_row()
    if open_view:
        P.d['openinpresentation'] = 1
    return len(ctrls)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('-o', '--output')
    ap.add_argument('--ids')
    ap.add_argument('--auto', action='store_true')
    ap.add_argument('--cols', type=int, default=6)
    ap.add_argument('--width', type=float, default=900.0)
    ap.add_argument('--no-open', action='store_true')
    a = ap.parse_args()
    doc = load(a.input)
    P = Patcher(doc['patcher'])
    n = present(P, a.ids.split(',') if a.ids else None, a.auto, a.cols, a.width, not a.no_open)
    save(doc, a.output or a.input)
    print(f"presentation: placed {n} controls")


if __name__ == '__main__':
    main()


def present_sections(P, header_ids, sections, cols=6, open_view=True, margin=15.0,
                     ctrl_gap=15.0, section_gap=45.0):
    """Presentation view without panels: the patch title (+ version) across the top,
    then one titled section per module, left to right, mirroring the patching view.
    sections: [(title comment id or None, [control box ids]), ...].  Sections with no
    controls are skipped; with no controls at all only the title/version show."""
    anchors = comment_anchors(P)
    label_of = {}
    for cid, (bid, _, _) in anchors.items():
        label_of.setdefault(bid, cid)
    x, y = margin, margin
    head_h = 0.0
    for hid in header_ids:
        b = P.by_id[hid]
        w, h = b.rect[2], b.rect[3]
        b.d['presentation'] = 1
        b.d['presentation_rect'] = [snap(x, PITCH), snap(y + (6 if head_h else 0), PITCH / 2), w, h]
        x += w + 8
        head_h = max(head_h, h)
    y = snap(margin + head_h + section_gap / 2, PITCH) if header_ids else margin
    x = margin
    for title_id, ctrl_ids in sections:
        ctrls = [P.by_id[i] for i in ctrl_ids if i in P.by_id]
        if not ctrls:
            continue
        sy = y
        sec_w = 0.0
        if title_id:
            t = P.by_id[title_id]
            t.d['presentation'] = 1
            t.d['presentation_rect'] = [snap(x, PITCH), snap(sy, PITCH), t.rect[2], t.rect[3]]
            sec_w = t.rect[2]
            sy += t.rect[3] + 8
        rows = [ctrls[i:i + cols] for i in range(0, len(ctrls), cols)]
        for row in rows:
            cx, rh = x, 0.0
            for b in row:
                bw, bh = b.rect[2], b.rect[3]
                b.d['presentation'] = 1
                b.d['presentation_rect'] = [snap(cx, PITCH), snap(sy, PITCH), bw, bh]
                uw, uh = bw, bh
                lab = P.by_id.get(label_of.get(b.id)) if b.maxclass not in SELF_LABELLED else None
                if lab is not None:
                    lab.d['presentation'] = 1
                    lab.d['presentation_rect'] = [snap(cx - 2, PITCH / 2), snap(sy + bh + 2, PITCH / 2),
                                                  lab.rect[2], lab.rect[3]]
                    uw, uh = max(bw, lab.rect[2]), bh + 2 + lab.rect[3]
                cx += uw + ctrl_gap
                rh = max(rh, uh)
            sec_w = max(sec_w, cx - ctrl_gap - x)
            sy += rh + ctrl_gap
        x = snap(x + sec_w + section_gap, PITCH)
    if open_view:
        P.d['openinpresentation'] = 1
