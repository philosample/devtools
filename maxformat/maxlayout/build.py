"""Build a finished .maxpat from a compact JSON spec, in the house style.

Describe WHAT the patch is (objects, cords, modules); this decides WHERE everything
goes: modules as columns left-to-right, signal top-to-bottom inside each column, bold
comment titles (no panels), 45px minimum spacing, right-angle cords, Max's default
colours, and a presentation view.

Usage:
  python3 -m maxlayout.build spec.json -o patch.maxpat [--png preview.png]
  (or: maxformat.py spec.json patch.maxpat)

Spec (JSON):
{
  "title": "FM Voice",                 # patch name (shown in both views)
  "version": "1.0",                    # optional, shown next to the title
  "objects": [
    {"id": "car",  "text": "cycle~ 220"},                   # object box
    {"id": "go",   "message": "1"},                         # message box
    {"id": "amt",  "ui": "live.dial",                       # UI control
       "param": {"name": "Index", "min": 0, "max": 10, "init": 2, "unit": "float"}},
    {"id": "tog",  "ui": "toggle", "label": "audio on"},    # label = comment beside it
    {"id": "about", "comment": "Free note under the title"}
  ],
  "cords": ["car:0 -> out:0", ["car", 0, "out", 1]],
  "modules": [{"title": "CARRIER", "note": "optional one-line explanation",
               "members": ["car", "amt"]}]
}

Per-object extras: "inlets"/"outlets"/"outlettype" (override the port guess - do this
for externals or unusual arguments), "w"/"h", "attrs" (raw box attributes merged in).
param.unit: int | float | ms | Hz | dB | % | pan | st | midi | native;  param.type
float|int|enum (enum needs "enum": [...] names).  "theme" (pcb|graphite|light) only
if the user asked for colours.
"""
import argparse, json, os, re, sys
from .maxpat import (save, read_json, Patcher, Box, guess_ports, ui_defaults, text_width,
                     pitch_width, comment_height, snap, snap_up, PITCH, check_cord,
                     check_trigger)
from . import layout, route, style, present, lint

UNITS = {'int': 0, 'float': 1, 'ms': 2, 'hz': 3, 'db': 4, '%': 5, 'pan': 6, 'st': 7,
         'semitones': 7, 'midi': 8, 'custom': 9, 'native': 10}
PTYPE = {'float': 0, 'int': 1, 'enum': 2}

HEADER = {
    "fileversion": 1,
    "appversion": {"major": 8, "minor": 6, "revision": 5, "architecture": "x64", "modernui": 1},
    "classnamespace": "box", "rect": [80.0, 80.0, 1000.0, 700.0], "bglocked": 0,
    "openinpresentation": 0, "default_fontsize": 12.0, "default_fontface": 0,
    "default_fontname": "Arial", "gridonopen": 1, "gridsize": [15.0, 15.0],
    "gridsnaponopen": 1, "objectsnaponopen": 1, "statusbarvisible": 2, "toolbarvisible": 1,
    "lefttoolbarpinned": 0, "toptoolbarpinned": 0, "righttoolbarpinned": 0,
    "bottomtoolbarpinned": 0, "toolbars_unpinned_last_save": 0, "tallnewobj": 0,
    "boxanimatetime": 200, "enablehscroll": 1, "enablevscroll": 1, "devicewidth": 0.0,
    "description": "", "digest": "", "tags": "", "style": "", "subpatcher_template": "",
    "assistshowspatchername": 0,
}

CORD_RE = re.compile(r'^\s*([\w.\-]+)\s*:\s*(\d+)\s*->\s*([\w.\-]+)\s*:\s*(\d+)\s*$')


def parse_cord(c):
    if isinstance(c, str):
        m = CORD_RE.match(c)
        if not m:
            raise SystemExit(f"bad cord {c!r}; use 'src:outlet -> dst:inlet'")
        return m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
    if isinstance(c, dict):
        return c['from'], int(c.get('outlet', 0)), c['to'], int(c.get('inlet', 0))
    return c[0], int(c[1]), c[2], int(c[3])


def make_box(o, oid):
    fs = float(o.get('fontsize', 12))
    if 'comment' in o:
        w = float(o.get('w', min(max(text_width(o['comment'], fs), 40), 420)))
        box = {'maxclass': 'comment', 'text': o['comment'], 'numinlets': 1, 'numoutlets': 0,
               'patching_rect': [0, 0, w, comment_height(o['comment'], w, fs)]}
    elif 'message' in o:
        nin, nout, ot = 2, 1, ['']
        w = pitch_width(max(text_width(o['message'], fs), 32), nin, nout)
        box = {'maxclass': 'message', 'text': str(o['message']), 'numinlets': nin,
               'numoutlets': nout, 'outlettype': ot, 'patching_rect': [0, 0, w, 22.0]}
    elif 'ui' in o:
        nin, nout, ot, w, h = ui_defaults(o['ui'])
        box = {'maxclass': o['ui'], 'numinlets': nin, 'numoutlets': nout, 'outlettype': list(ot),
               'patching_rect': [0, 0, float(o.get('w', w)), float(o.get('h', h))]}
        if 'param' in o or o['ui'].startswith('live.'):
            p = o.get('param', {})
            name = p.get('name', o['id'])
            v = {'parameter_longname': name, 'parameter_shortname': p.get('short', name)[:12],
                 'parameter_type': PTYPE.get(p.get('type', 'float'), 0)}
            if 'min' in p:
                v['parameter_mmin'] = p['min']
            if 'max' in p:
                v['parameter_mmax'] = p['max']
            if 'init' in p:
                v['parameter_initial'] = [p['init']]
                v['parameter_initial_enable'] = 1
            if 'unit' in p:
                v['parameter_unitstyle'] = UNITS.get(str(p['unit']).lower(), 1)
            if 'enum' in p:
                v['parameter_enum'] = p['enum']
                v['parameter_type'] = 2
                v['parameter_mmax'] = len(p['enum']) - 1
            box['parameter_enable'] = 1
            box['varname'] = name
            box['saved_attribute_attributes'] = {'valueof': v}
    elif str(o.get('text', '')).split()[:1] in (['inlet'], ['outlet']):
        # Max stores subpatcher inlets/outlets as their own box type, not object text
        kind = o['text'].split()[0]
        nin, nout, ot, w, h = ui_defaults(kind)
        box = {'maxclass': kind, 'index': 1, 'comment': o.get('label', ''), 'numinlets': nin,
               'numoutlets': nout, 'patching_rect': [0, 0, w, h]}
        if nout:
            box['outlettype'] = list(o.get('outlettype', ot))
    else:
        text = o['text']
        nin, nout, ot = guess_ports(text)
        w = pitch_width(max(text_width(text, fs), 32), int(o.get('inlets', nin)), int(o.get('outlets', nout)))
        box = {'maxclass': 'newobj', 'text': text, 'numinlets': nin, 'numoutlets': nout,
               'outlettype': list(ot), 'patching_rect': [0, 0, w, 22.0]}
    for k_spec, k_box in (('inlets', 'numinlets'), ('outlets', 'numoutlets'), ('outlettype', 'outlettype')):
        if k_spec in o:
            box[k_box] = o[k_spec]
    if box.get('maxclass') == 'newobj' and len(box.get('outlettype', [])) != box['numoutlets']:
        ot = list(box.get('outlettype', []))[:box['numoutlets']]
        sig = 'signal' if str(box.get('text', '')).split()[0].endswith('~') else ''
        box['outlettype'] = ot + [sig] * (box['numoutlets'] - len(ot))
    if 'w' in o and 'ui' not in o:
        box['patching_rect'][2] = float(o['w'])
    box.update(o.get('attrs', {}))
    box['id'] = oid
    return box


# The house style.  These numbers are the user's stated preferences - change them here.
STYLE = {
    'min_gap': 45.0,      # no two objects (or object+label units) ever closer than this
    'row_gap': 45.0,      # between connected rows inside a module
    'side_gap': 45.0,     # between side-by-side objects inside a module
    'column_gap': 90.0,   # between module columns
    'margin': 30.0,       # from the patcher's top-left corner
    'label_gap': 6.0,     # a label sits this close to its control (label+control = one unit)
}
UI_SKIP = ('newobj', 'message', 'comment', 'panel')


def _comment(oid, text, fontsize=12.0, bold=False, w=None):
    w = float(w or min(max(text_width(text, fontsize) + 6, 30), 360))
    box = {'id': oid, 'maxclass': 'comment', 'text': text, 'numinlets': 1, 'numoutlets': 0,
           'patching_rect': [0.0, 0.0, w, comment_height(text, w, fontsize)]}
    if fontsize != 12.0:
        box['fontsize'] = fontsize
    if bold:
        box['fontface'] = 1
    return box


def _module_order(n_modules, edges):
    """Left-to-right order of module columns: topological by data flow, spec order breaks ties."""
    indeg = [0] * n_modules
    succ = [set() for _ in range(n_modules)]
    for a, b in edges:
        if b not in succ[a]:
            succ[a].add(b)
            indeg[b] += 1
    order, ready = [], [i for i in range(n_modules) if indeg[i] == 0]
    while ready:
        ready.sort()
        n = ready.pop(0)
        order.append(n)
        for m in sorted(succ[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    return order + [i for i in range(n_modules) if i not in order]   # cycles: spec order


def build(spec, name=None):
    """Return (doc, id_map) for a spec.  `name` titles the patch if the spec has no title."""
    S = STYLE
    pd = json.loads(json.dumps(HEADER))
    objs = spec.get('objects', [])
    n = 0

    def new_id():
        nonlocal n
        n += 1
        return f"obj-{n}"

    ids, by_spec, boxes = {}, {}, []
    for o in objs:
        if 'id' not in o:
            raise SystemExit(f"object without id: {o}")
        ids[o['id']] = new_id()
    for o in objs:
        by_spec[o['id']] = make_box(o, ids[o['id']])
        bad = check_trigger(by_spec[o['id']])
        if bad and bad[0] == 'error':
            raise SystemExit(f"object {o['id']}: {bad[1]}")
        boxes.append(by_spec[o['id']])
    labels = {}                                   # spec id -> label comment box
    for o in objs:
        if o.get('label'):
            labels[o['id']] = _comment(new_id(), o['label'])
            boxes.append(labels[o['id']])

    cords = []
    for c in spec.get('cords', []):
        s, so, d, di = parse_cord(c)
        if s not in ids or d not in ids:
            raise SystemExit(f"cord references unknown id: {c}")
        sb, db = by_spec[s], by_spec[d]
        if so >= sb['numoutlets'] or di >= db['numinlets']:
            raise SystemExit(f"cord {c}: {s} has {sb['numoutlets']} outlets / {d} has {db['numinlets']} inlets "
                             f"- fix the cord or give the object explicit \"inlets\"/\"outlets\"")
        bad = check_cord(sb, so, db, di)
        if bad and bad[0] == 'error':
            raise SystemExit(f"cord {c}: {bad[1]}")
        cords.append((s, so, d, di))

    # ---- modules ---------------------------------------------------------
    modules = [dict(m) for m in spec.get('modules', [])]
    member = {sid: mi for mi, m in enumerate(modules) for sid in m['members']}
    loose = [o['id'] for o in objs if 'comment' not in o and o['id'] not in member]
    if loose:
        modules.append({'title': None, 'members': loose})
        member.update({sid: len(modules) - 1 for sid in loose})
    mod_edges = [(member[s], member[d]) for s, _, d, _ in cords if member[s] != member[d]]
    order = _module_order(len(modules), mod_edges)

    # ---- header: patch title, version, free notes ------------------------
    x0 = y = S['margin']
    title = spec.get('title') or name
    header_ids = []
    if title:
        tb = _comment(new_id(), title, 18.0, True)
        tb['patching_rect'][:2] = [x0, y]
        boxes.append(tb)
        header_ids.append(tb['id'])
        if spec.get('version'):
            vb = _comment(new_id(), f"v{spec['version']}".replace('vv', 'v'))
            vb['patching_rect'][:2] = [x0 + tb['patching_rect'][2] + 8, y + 6]
            boxes.append(vb)
            header_ids.append(vb['id'])
        y += tb['patching_rect'][3] + 4
    for o in objs:
        if 'comment' in o and o['id'] not in member:
            nb = by_spec[o['id']]
            nb['patching_rect'][:2] = [x0, y]
            y += nb['patching_rect'][3] + 4
    top = snap_up(y + S['min_gap'], 15.0) if y > S['margin'] else S['margin']

    # ---- columns ---------------------------------------------------------
    def port_off(b, kind, i):
        bx = Box(b)
        return (bx.outlet_xy(i)[0] if kind == 'out' else bx.inlet_xy(i)[0]) - b['patching_rect'][0]

    spec_index = {o['id']: i for i, o in enumerate(objs)}

    # Rows are shared by all columns and come from the WHOLE signal graph, so anything
    # fed by an object sits below it even in another column: cross-column cords then
    # always run down-and-right, never back up.
    def unit_size(sid):
        b = by_spec[sid]
        w, h = b['patching_rect'][2], b['patching_rect'][3]
        if sid in labels:
            lb = labels[sid]['patching_rect']
            w, h = w + S['label_gap'] + lb[2], max(h, lb[3])
        return w, h

    nodes = [sid for m in modules for sid in m['members']
             if sid in by_spec and by_spec[sid]['maxclass'] != 'comment']
    allitems = {sid: layout.Item(sid, *unit_size(sid)) for sid in nodes}
    layout.layered(allitems, [(s, 0.0, d, 0.0) for s, _, d, _ in cords if s in allitems and d in allitems],
                   {sid: (spec_index[sid] * 10.0, 0.0) for sid in nodes}, max_width=0)
    row_of = {sid: it.layer for sid, it in allitems.items()}
    x = x0
    body_top = top
    sections = []
    placed = []   # (sid, column x, local item) - y filled in once all rows are known
    for mi in order:
        m = modules[mi]
        mem = [sid for sid in m['members'] if sid in by_spec and 'comment' not in by_spec[sid]['maxclass'] and by_spec[sid]['maxclass'] != 'comment']
        notes = [sid for sid in m['members'] if sid in by_spec and by_spec[sid]['maxclass'] == 'comment']
        ty = top
        head = []
        if m.get('title'):
            tb = _comment(new_id(), m['title'], 12.0, True)
            tb['patching_rect'][:2] = [x, ty]
            boxes.append(tb)
            head.append(tb)
            ty += tb['patching_rect'][3] + 4
        for sid in notes + ([m['note']] if m.get('note') else []):
            nb = by_spec[sid] if sid in by_spec else _comment(new_id(), sid)
            if sid not in by_spec:
                boxes.append(nb)
            nb['patching_rect'][:2] = [x, ty]
            head.append(nb)
            ty += nb['patching_rect'][3] + 4
        y0 = snap_up(ty + S['min_gap'], 15.0) if head else top
        # each object + its label is one unit
        sub = {sid: layout.Item(sid, *unit_size(sid)) for sid in mem}
        es = [(s, port_off(by_spec[s], 'out', so), d, port_off(by_spec[d], 'in', di))
              for s, so, d, di in cords if s in sub and d in sub]
        hint = {sid: (spec_index[sid] * 10.0, 0.0) for sid in mem}
        if sub:
            layout.layered(sub, es, hint, hgap=S['side_gap'], vgap=S['row_gap'],
                           fixed_layers={sid: row_of[sid] for sid in sub})
        width = max([it.x + it.w for it in sub.values()] + [h_['patching_rect'][2] for h_ in head] + [0])
        for sid, it in sub.items():
            placed.append((sid, x, it))
        body_top = max(body_top, y0)
        sections.append((head[0]['id'] if head and m.get('title') else None,
                         [ids[sid] for sid in mem if by_spec[sid]['maxclass'] not in UI_SKIP]))
        x = snap_up(x + width + S['column_gap'], 15.0)

    # global rows: each row as tall as its tallest unit in any column
    first = body_top
    row_h = {}
    for sid, _, it in placed:
        row_h[row_of[sid]] = max(row_h.get(row_of[sid], 0.0), it.h)
    row_y, yy = {}, first
    for r in sorted(row_h):
        row_y[r] = yy
        yy = snap_up(yy + row_h[r] + S['row_gap'], 15.0)
    for sid, cx, it in placed:
        b = by_spec[sid]
        bx, by_ = snap(cx + it.x, PITCH), row_y[row_of[sid]]
        b['patching_rect'][:2] = [bx, by_]
        if sid in labels:
            lb = labels[sid]['patching_rect']
            lb[:2] = [bx + b['patching_rect'][2] + S['label_gap'], by_ + 2]

    pd['boxes'] = [{'box': b} for b in boxes]
    pd['lines'] = [{'patchline': {'source': [ids[s], so], 'destination': [ids[d], di]}}
                   for s, so, d, di in cords]
    doc = {'patcher': pd}
    route.route_patcher(Patcher(pd))
    if spec.get('theme') and spec['theme'] != 'none':
        style.apply(doc, spec['theme'], modules=False)
    present.present_sections(Patcher(pd), header_ids, sections,
                             cols=int((spec.get('presentation') or {}).get('cols', 6)))
    xs = [b['patching_rect'][0] + b['patching_rect'][2] for b in boxes]
    ys = [b['patching_rect'][1] + b['patching_rect'][3] for b in boxes]
    pd['rect'] = [80.0, 80.0, max(640.0, max(xs) + 60), max(480.0, max(ys) + 60)]
    return doc, ids


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('spec')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--theme', help='override spec theme')
    ap.add_argument('--png', help='also render a preview PNG here (macOS)')
    a = ap.parse_args()
    spec = read_json(a.spec)
    if a.theme:
        spec['theme'] = a.theme
    doc, ids = build(spec, name=os.path.splitext(os.path.basename(a.output))[0])
    save(doc, a.output)
    res = lint.lint_doc(doc, presentation=True, min_gap=STYLE['min_gap'])
    print(f"wrote {a.output}  ({len(ids)} objects, score {res['score']})")
    print("id map: " + ', '.join(f"{k}={v}" for k, v in ids.items()))
    for s_ in res['advice']:
        print('  advice: ' + s_)
    if a.png:
        from . import render
        svg = os.path.splitext(a.png)[0] + '.svg'
        with open(svg, 'w') as f:
            f.write(render.render(Patcher(doc['patcher']), square=True))
        if render.to_png(svg, a.png):
            os.remove(svg)
            print(a.png)
        else:
            print('png unavailable; see ' + svg)


if __name__ == '__main__':
    main()
