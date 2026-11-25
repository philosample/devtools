"""Build data/refdb.json: what each Max object is and what its inlets/outlets carry.

Sources (all from a local Max install):
  * the reference pages (docs/refpages/**/*.maxref.xml): object description, and the
    declared type of each inlet/outlet where Cycling '74 filled it in;
  * every help/example patch: which inlets actually receive signal cords, so control
    objects whose refpage types are blank placeholders are still known.

Usage: python3 -m maxlayout.mine_refdb [/Applications/Max.app] > maxlayout/data/refdb.json
"""
import collections
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET

PLACEHOLDER = {None, '', 'INLET_TYPE', 'OUTLET_TYPE', 'inactive'}


def clean(t):
    t = (t or '').strip()
    return '?' if t in PLACEHOLDER else t.lower()


def port_index(node):
    """Ports are id="0", "1"...; a few pages use names like "inlet_in1" - keep document order."""
    i = node.get('id', '')
    return int(i) if str(i).isdigit() else 999


def text_of(node):
    return ' '.join((node.text or '').split()) if node is not None else ''


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else '/Applications/Max.app'
    db = {}
    for f in glob.glob(os.path.join(root, '**/refpages/**/*.maxref.xml'), recursive=True):
        try:
            r = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        name = r.get('name')
        if not name:
            continue
        ins = sorted(r.iter('inlet'), key=port_index)
        outs = sorted(r.iter('outlet'), key=port_index)
        db[name] = {
            'module': r.get('module', ''),
            'digest': text_of(r.find('digest')),
            'in': [clean(i.get('type')) for i in ins],
            'in_digest': [text_of(i.find('digest')) for i in ins],
            'out': [clean(o.get('type')) for o in outs],
            'out_digest': [text_of(o.find('digest')) for o in outs],
            'sig_in': [],
        }
    # observed: which inlets receive signal cords in Cycling '74's own patches
    seen = collections.defaultdict(set)
    files = [f for f in glob.glob(os.path.join(root, '**/*.max*'), recursive=True)
             if f.endswith(('.maxpat', '.maxhelp'))]

    def walk(p):
        boxes = {b['box']['id']: b['box'] for b in p.get('boxes', [])}
        for ln in p.get('lines', []):
            pl = ln['patchline']
            s, d = boxes.get(pl['source'][0]), boxes.get(pl['destination'][0])
            if not s or not d:
                continue
            ot = s.get('outlettype') or []
            o = pl['source'][1]
            if o < len(ot) and ot[o] in ('signal', 'multichannelsignal'):
                cls = str(d.get('text', '')).split()[:1] if d.get('maxclass') == 'newobj' else [d.get('maxclass')]
                if cls:
                    seen[cls[0]].add(pl['destination'][1])
        for b in p.get('boxes', []):
            if isinstance(b['box'].get('patcher'), dict):
                walk(b['box']['patcher'])

    for f in files:
        try:
            with open(f, encoding='utf-8', errors='replace') as fh:
                walk(json.load(fh)['patcher'])
        except Exception:
            continue
    for cls, idx in seen.items():
        db.setdefault(cls, {'module': '', 'digest': '', 'in': [], 'in_digest': [], 'out': [],
                            'out_digest': [], 'sig_in': []})['sig_in'] = sorted(idx)
    json.dump(db, sys.stdout, separators=(',', ':'), sort_keys=True)


if __name__ == '__main__':
    main()
