"""Mine inlet/outlet counts and default sizes from a local Max install.

Usage: python3 mine_objdb.py [/Applications/Max.app] > ../assets/objdb.json

Produces {"newobj": {class: {nargs: [nin, nout, outlettype]}}, "ui": {maxclass: {...}}}
using the most common observation for each key. Only factual port metadata is kept.
"""
import json, glob, sys, collections, os

root = sys.argv[1] if len(sys.argv) > 1 else '/Applications/Max.app'
files = [f for f in glob.glob(os.path.join(root, '**/*.max*'), recursive=True)
         if f.endswith(('.maxpat', '.maxhelp'))]

def walk(p):
    yield p
    for b in p.get('boxes', []):
        sp = b['box'].get('patcher')
        if isinstance(sp, dict):
            yield from walk(sp)

newobj = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
ui = collections.defaultdict(collections.Counter)
for f in files:
    try:
        with open(f, encoding='utf-8', errors='replace') as fh:
            d = json.load(fh)
    except Exception:
        continue
    for p in walk(d.get('patcher', {})):
        for b in p.get('boxes', []):
            b = b['box']
            mc = b.get('maxclass')
            nin, nout = b.get('numinlets'), b.get('numoutlets')
            if nin is None or nout is None:
                continue
            ot = tuple(b.get('outlettype', []))
            if mc == 'newobj':
                toks = str(b.get('text', '')).split()
                if not toks:
                    continue
                cls = toks[0]
                nargs = len([t for t in toks[1:] if not t.startswith('@')])
                # attributes (@foo bar) don't change port counts; count positional args only
                pos, skip = 0, False
                for t in toks[1:]:
                    if t.startswith('@'):
                        skip = True; continue
                    if skip:
                        skip = False; continue
                    pos += 1
                newobj[cls][str(min(pos, 12))][(nin, nout, ot)] += 1
            elif mc not in ('comment', 'panel'):
                r = b.get('patching_rect')
                if r:
                    ui[mc][(nin, nout, ot, r[2], r[3])] += 1

out = {'source': 'Max bundled patches', 'newobj': {}, 'ui': {}}
for cls, by in newobj.items():
    total = sum(sum(c.values()) for c in by.values())
    if total < 2:
        continue
    out['newobj'][cls] = {k: [*c.most_common(1)[0][0][:2], list(c.most_common(1)[0][0][2])]
                          for k, c in by.items()}
for mc, c in ui.items():
    (nin, nout, ot, w, h), _ = c.most_common(1)[0]
    out['ui'][mc] = [nin, nout, list(ot), w, h]
json.dump(out, sys.stdout, separators=(',', ':'), sort_keys=True)
