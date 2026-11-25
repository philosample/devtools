"""Score the visual tidiness of a Max patch, like a PCB design-rule check.

Usage:
  python3 lint.py patch.maxpat [--before original.maxpat] [--json] [--no-recurse]
                               [--presentation]

Checks (per patcher, including subpatchers):
  diagonal     cord segments that are neither horizontal nor vertical
  jog          straight-ish cords whose ends are 1-14px out of line (looks like a mistake)
  crossing     points where cords of different nets cross
  overlap      segments of different nets running on top of / within 3px of each other
  through_box  cord segments passing through a box they don't belong to
  box_overlap  boxes overlapping each other (panels/background excluded)
  off_grid     boxes not on the patcher's grid (or half-grid in x, for pin-pitch layouts)
  upward       cords that enter their inlet from below the outlet (feedback; info only)
  sprawl       board far too wide/tall or mostly empty space (caps the score at 80)
  too_close    (with --min-gap N) objects closer than N px; a label hugging its
               control counts as part of it
  long_cord    cord bigger than a 1280x800 screen or crossing 3+ cords - advice:
               replace with a well-named [s]/[r] pair
  fanout_order (with --before) one outlet feeding several inlets whose right-to-left
               order changed - this CHANGES PATCH BEHAVIOUR in Max
  lost_cords   (with --before) connections that disappeared or appeared
  io_order     (with --before) inlet/outlet objects of a subpatcher changed left-to-right
               order - this renumbers the parent box's ports
  presentation (with --presentation) overlapping / off-grid presentation_rects

Exit status is 1 if any fanout_order, lost_cords or io_order problems exist (behaviour changed).
"""
import argparse, json, sys, os
from .maxpat import (load, walk_patchers, net_kind, io_order, ID_RE, check_cord, is_short_hop,
                     is_signal_outlet, check_trigger, TAKES_COMMANDS, object_class)

EPS = 0.5


def segs(P, ln):
    pts = P.polyline(ln)
    return list(zip(pts, pts[1:]))


def is_h(a, b):
    return abs(a[1] - b[1]) <= EPS


def is_v(a, b):
    return abs(a[0] - b[0]) <= EPS


def seg_cross(p, q):
    """Proper crossing between an H and a V segment (interior point, not endpoints)."""
    (a, b), (c, d) = p, q
    if is_h(a, b) and is_v(c, d):
        h, v = (a, b), (c, d)
    elif is_v(a, b) and is_h(c, d):
        h, v = (c, d), (a, b)
    else:
        return general_cross(p, q)
    hx0, hx1 = sorted([h[0][0], h[1][0]])
    vy0, vy1 = sorted([v[0][1], v[1][1]])
    x, y = v[0][0], h[0][1]
    return hx0 + EPS < x < hx1 - EPS and vy0 + EPS < y < vy1 - EPS


def general_cross(p, q):
    (x1, y1), (x2, y2) = p
    (x3, y3), (x4, y4) = q
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-9:
        return False
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    return 0.02 < t < 0.98 and 0.02 < u < 0.98


def on_lead(P, src, seg, depth=45.0):
    """Is this segment the shared vertical lead just below an outlet?"""
    b = P.by_id[src[0]]
    x, y = b.outlet_xy(src[1])
    (a, c) = seg
    return abs(a[0] - x) <= EPS and abs(c[0] - x) <= EPS and min(a[1], c[1]) >= y - EPS \
        and max(a[1], c[1]) <= y + depth


def collinear_overlap(p, q, tol=3.0):
    (a, b), (c, d) = p, q
    if is_h(a, b) and is_h(c, d) and abs(a[1] - c[1]) < tol:
        lo = max(min(a[0], b[0]), min(c[0], d[0]))
        hi = min(max(a[0], b[0]), max(c[0], d[0]))
        return hi - lo > 2
    if is_v(a, b) and is_v(c, d) and abs(a[0] - c[0]) < tol:
        lo = max(min(a[1], b[1]), min(c[1], d[1]))
        hi = min(max(a[1], b[1]), max(c[1], d[1]))
        return hi - lo > 2
    return False


def seg_hits_rect(a, b, r, shrink=1.0):
    x, y, w, h = r
    l, t, rr, bb = x + shrink, y + shrink, x + w - shrink, y + h - shrink
    if l >= rr or t >= bb:
        return False
    # Liang-Barsky clip
    dx, dy = b[0] - a[0], b[1] - a[1]
    u0, u1 = 0.0, 1.0
    for p_, q_ in ((-dx, a[0] - l), (dx, rr - a[0]), (-dy, a[1] - t), (dy, bb - a[1])):
        if abs(p_) < 1e-12:
            if q_ < 0:
                return False
        else:
            u = q_ / p_
            if p_ < 0:
                u0 = max(u0, u)
            else:
                u1 = min(u1, u)
    return u1 - u0 > 1e-3


SCREEN = (1280.0, 800.0)   # a cord bigger than one screen should be a send/receive


def rect_gap(r1, r2):
    """Horizontal and vertical clear distance between two rects (negative = overlapping)."""
    gx = max(r2[0] - (r1[0] + r1[2]), r1[0] - (r2[0] + r2[2]))
    gy = max(r2[1] - (r1[1] + r1[3]), r1[1] - (r2[1] + r2[3]))
    return gx, gy


def label_pair(a, b, touch=12.0):
    """A comment hugging an object (its label), or two stacked comments (title + note),
    count as one unit for spacing."""
    if a.maxclass != 'comment' and b.maxclass != 'comment':
        return False
    gx, gy = rect_gap(a.rect, b.rect)
    return (gx < touch and gy < 0) or (gy < touch and gx < 0 and a.maxclass == b.maxclass == 'comment')


def rect_overlap(r1, r2, tol=1.0):
    return (r1[0] + tol < r2[0] + r2[2] and r2[0] + tol < r1[0] + r1[2] and
            r1[1] + tol < r2[1] + r2[3] and r2[1] + tol < r1[1] + r1[3])


def lint_patcher(P, presentation=False, min_gap=0.0):
    issues = {k: [] for k in ('diagonal', 'jog', 'crossing', 'overlap', 'through_box',
                              'box_overlap', 'too_close', 'off_grid', 'upward', 'sprawl',
                              'long_cord', 'presentation', 'wiring_error', 'wiring_warning')}
    gx, gy = P.grid
    S = []
    for ln in P.lines:
        net = (ln.src, ln.dst)
        for sg in segs(P, ln):
            S.append((net, ln, sg))
        s, t = P.by_id[ln.src[0]], P.by_id[ln.dst[0]]
        a, b = s.outlet_xy(ln.src[1]), t.inlet_xy(ln.dst[1])
        desc = f"{label(s)}[{ln.src[1]}] -> {label(t)}[{ln.dst[1]}]"
        pts = P.polyline(ln)
        diag = [(p, q) for p, q in zip(pts, pts[1:]) if not is_h(p, q) and not is_v(p, q)]
        if diag and not (not ln.midpoints and is_short_hop(a, b)):   # short hops may be diagonal
            if not ln.midpoints and 0.5 < abs(a[0] - b[0]) < 15 and b[1] > a[1]:
                issues['jog'].append(f"{desc} is {abs(a[0]-b[0]):.1f}px out of line")
            else:
                issues['diagonal'].append(desc)
        if b[1] < a[1]:
            issues['upward'].append(desc)
        for o in P.boxes:
            if o.id in (s.id, t.id) or o.is_background or o.maxclass == 'comment' and o.d.get('hidden'):
                continue
            if any(seg_hits_rect(p, q, o.rect) for p, q in zip(pts, pts[1:])):
                issues['through_box'].append(f"{desc} passes through {label(o)}")
                break
    for i in range(len(S)):
        ni, li, si = S[i]
        for j in range(i + 1, len(S)):
            nj, lj, sj = S[j]
            if ni[1] == nj[1]:
                continue      # same inlet: fan-in cords are meant to merge
            if ni[0] == nj[0]:
                # same outlet: they may share the short lead below it, nothing more
                if on_lead(P, ni[0], si) and on_lead(P, nj[0], sj):
                    continue
                if seg_cross(si, sj):
                    continue  # a fan-out crossing itself doesn't mislead anyone
            if seg_cross(si, sj):
                issues['crossing'].append(f"{lname(P, li)} x {lname(P, lj)}")
            elif collinear_overlap(si, sj):
                issues['overlap'].append(f"{lname(P, li)} || {lname(P, lj)}")
    solid = [b for b in P.boxes if not b.is_background and not b.d.get('hidden')]
    for i in range(len(solid)):
        for j in range(i + 1, len(solid)):
            if rect_overlap(solid[i].rect, solid[j].rect):
                issues['box_overlap'].append(f"{label(solid[i])} / {label(solid[j])}")
    for b in solid:
        if b.maxclass == 'comment':
            continue
        x, y = b.rect[0], b.rect[1]
        offx = abs(x / (gx / 2) - round(x / (gx / 2))) > 0.01
        offy = abs(y / gy - round(y / gy)) > 0.01
        if offx or offy:
            issues['off_grid'].append(f"{label(b)} at ({x:g},{y:g})")
    # sprawl: a board that is very wide and thin, or mostly empty, is hard to read even
    # if every trace is perfect.  Computed over real (non-comment, non-panel) boxes.
    issues['sprawl'] = []
    real = [b for b in solid if b.maxclass != 'comment']
    if len(real) >= 6:
        L = min(b.rect[0] for b in real); T = min(b.rect[1] for b in real)
        R = max(b.rect[0] + b.rect[2] for b in real); B = max(b.rect[1] + b.rect[3] for b in real)
        W, H = R - L, B - T
        if H > 0 and W / H > 3.0 and W > 900:
            issues['sprawl'].append(f"board is {W:.0f}x{H:.0f}px (aspect {W/H:.1f}:1) - too wide; stack rows")
        if W > 0 and H / W > 4.0 and H > 1200:
            issues['sprawl'].append(f"board is {W:.0f}x{H:.0f}px (aspect 1:{H/W:.1f}) - too tall; use columns")
        dens = sum(b.rect[2] * b.rect[3] for b in real) / max(W * H, 1)
        if dens < 0.03 and W * H > 600 * 600:
            issues['sprawl'].append(f"boxes cover only {dens*100:.1f}% of a {W:.0f}x{H:.0f}px board - spread too thin")
    if min_gap > 0:
        units = [b for b in solid]
        for i in range(len(units)):
            for j in range(i + 1, len(units)):
                a, b = units[i], units[j]
                if label_pair(a, b):
                    continue
                gx_, gy_ = rect_gap(a.rect, b.rect)
                if gx_ < min_gap - 0.5 and gy_ < min_gap - 0.5 and not rect_overlap(a.rect, b.rect):
                    issues['too_close'].append(f"{label(a)} / {label(b)} ({max(gx_, gy_):.0f}px apart)")
    # long cords: candidates for a well-named [s]/[r] pair
    for ln, why in long_cords(P, issues['crossing']):
        issues['long_cord'].append(f"{lname(P, ln)} {why} - replace with a well-named [s]/[r] pair")
    into = {}
    for ln in P.lines:
        res = check_cord(P.by_id[ln.src[0]].d, ln.src[1], P.by_id[ln.dst[0]].d, ln.dst[1])
        if res:
            issues['wiring_' + res[0]].append(res[1])
        src = P.by_id[ln.src[0]]
        if is_signal_outlet(src.d, ln.src[1]):
            kind = 'audio'
        elif src.maxclass == 'message' or object_class(P.by_id[ln.dst[0]].d) in TAKES_COMMANDS:
            kind = 'command'      # e.g. startwindow -> dac~: commands work alongside audio
        else:
            kind = 'msg'
        into.setdefault(tuple(ln.dst), set()).add(kind)
    for b in P.boxes:
        res = check_trigger(b.d)
        if res:
            issues['wiring_' + res[0]].append(res[1])
    for (bid, i), kinds in into.items():
        if {'audio', 'msg'} <= kinds:
            issues['wiring_warning'].append(
                f"[{P.by_id[bid].text or P.by_id[bid].maxclass}] inlet {i} gets both audio and numbers - "
                f"while audio is connected, Max ignores the numbers there. Add them with a separate "
                f"[+~] / [sig~], or send them somewhere else.")
    if presentation:
        pres = [b for b in P.boxes if b.d.get('presentation') and b.d.get('presentation_rect')]
        for i in range(len(pres)):
            for j in range(i + 1, len(pres)):
                if pres[i].is_background or pres[j].is_background:
                    continue
                if rect_overlap(pres[i].d['presentation_rect'], pres[j].d['presentation_rect']):
                    issues['presentation'].append(f"overlap {label(pres[i])} / {label(pres[j])}")
        for b in pres:
            x, y = b.d['presentation_rect'][:2]
            if abs(x / (gx / 2) - round(x / (gx / 2))) > 0.01 or abs(y / (gy / 2) - round(y / (gy / 2))) > 0.01:
                issues['presentation'].append(f"off-grid {label(b)} at ({x:g},{y:g})")
    return issues


def long_cords(P, crossing_strings=None):
    """Cords that should be a send/receive pair: bigger than a screen, or crossing 3+."""
    if crossing_strings is None:
        crossing_strings = lint_patcher(P)['crossing']
    per = {}
    for c in crossing_strings:
        for part in c.split(' x '):
            per[part] = per.get(part, 0) + 1
    out = []
    for ln in P.lines:
        pts = P.polyline(ln)
        w_ = max(p[0] for p in pts) - min(p[0] for p in pts)
        h_ = max(p[1] for p in pts) - min(p[1] for p in pts)
        n_ = per.get(lname(P, ln), 0)
        if w_ > SCREEN[0] or h_ > SCREEN[1]:
            out.append((ln, f"spans {w_:.0f}x{h_:.0f}px"))
        elif n_ >= 3:
            out.append((ln, f"crosses {n_} cords"))
    return out


def sr_pairs(P, Q):
    """New [s X]/[r X] pairs in P (not in the original Q): {send box id: [(dst, inlet), ...]}."""
    def name(b):
        t = str(b.text).split()
        return (t[0], t[1]) if len(t) > 1 else (t[0] if t else '', None)
    sends, recvs = {}, {}
    for b in P.boxes:
        if b.id in Q.by_id or b.maxclass != 'newobj':
            continue
        cls, nm = name(b)
        if nm is None:
            continue
        if cls in ('s', 'send', 'send~'):
            sends[b.id] = nm
        elif cls in ('r', 'receive', 'receive~'):
            recvs.setdefault(nm, []).append(b.id)
    out = {}
    for sid, nm in sends.items():
        out[sid] = [tuple(l.dst) for rid in recvs.get(nm, []) for l in P.lines if l.src[0] == rid]
    return out


def label(b):
    t = b.text or b.d.get('varname') or b.maxclass
    t = str(t)
    return f"{b.id}:{t[:24]}"


def lname(P, ln):
    return f"{label(P.by_id[ln.src[0]])}->{label(P.by_id[ln.dst[0]])}"


def conn_set(P):
    return {(P.by_id[l.src[0]].id, l.src[1], P.by_id[l.dst[0]].id, l.dst[1]) for l in P.lines}


def fanout_orders(P):
    """For each control outlet with several destinations, the firing order Max will use
    (right-to-left by inlet x, then bottom-to-top)."""
    groups = {}
    for ln in P.lines:
        s = P.by_id[ln.src[0]]
        if net_kind(s, ln.src[1]) in ('audio', 'mc'):
            continue
        groups.setdefault(ln.src, []).append(ln)
    out = {}
    for key, lns in groups.items():
        if len(lns) < 2:
            continue
        def k(ln):
            x, y = P.by_id[ln.dst[0]].inlet_xy(ln.dst[1])
            return (-x, -y)
        out[key] = [(l.dst[0], l.dst[1]) for l in sorted(lns, key=k)]
    return out


def compare(before_doc, after_doc, recurse=True):
    probs = {'fanout_order': [], 'lost_cords': [], 'io_order': [], 'bad_ids': []}
    B = {p.path: p for p in walk_patchers(before_doc['patcher'], recurse)}
    for P in walk_patchers(after_doc['patcher'], recurse):
        Q = B.get(P.path)
        if Q is None:
            continue
        ids = [b.id for b in P.boxes]
        for i in sorted(set(i for i in ids if not ID_RE.match(str(i)) or ids.count(i) > 1)):
            probs['bad_ids'].append(f"{P.path}: box id {i!r} is not a unique obj-<number> "
                                    f"(Max will drop its cords)")
        cb, ca = conn_set(Q), conn_set(P)
        # A fan-out replaced by an inserted [t ...] is equivalent if every original
        # destination is reached through the trigger, in the original firing order
        # (trigger fires its outlets right-to-left).
        via_trigger = set()
        for tb in P.boxes:
            if tb.id in Q.by_id or tb.maxclass != 'newobj' or str(tb.text).split()[:1] not in (['t'], ['trigger']):
                continue
            ins = [l for l in P.lines if l.dst[0] == tb.id]
            if len(ins) != 1:
                continue
            src = tuple(ins[0].src)
            outs = sorted((l for l in P.lines if l.src[0] == tb.id), key=lambda l: -l.src[1])
            fired = [tuple(l.dst) for l in outs]
            orig = fanout_orders(Q).get(src) or [tuple(l.dst) for l in Q.lines if tuple(l.src) == src]
            if sorted(fired) == sorted(orig):
                if fired != orig and len(orig) > 1 and src in fanout_orders(Q):
                    probs['fanout_order'].append(f"{P.path}: trigger {tb.id} fires {fired}, original fired {orig}")
                via_trigger |= {(src[0], src[1], d[0], d[1]) for d in fired}
        # A cord replaced by an inserted [s name] -> [r name] pair is the same connection.
        via_sr = set()
        pairs = sr_pairs(P, Q)
        for l in P.lines:
            if l.dst[0] in pairs:
                via_sr |= {(l.src[0], l.src[1], d[0], d[1]) for d in pairs[l.dst[0]]}
        for c in sorted(cb - ca - via_trigger - via_sr):
            probs['lost_cords'].append(f"{P.path}: removed {c}")
        for c in sorted(ca - cb):
            # new cords are fine if they come from inserted trigger objects
            if not (c[0] not in Q.by_id or c[2] not in Q.by_id):
                probs['lost_cords'].append(f"{P.path}: added {c}")
        for kind, ob, oa in zip(('inlet', 'outlet'), io_order(Q), io_order(P)):
            if ob != oa:
                probs['io_order'].append(f"{P.path}: {kind} objects reordered {ob} -> {oa} "
                                         f"(renumbers the parent box's {kind}s!)")
        fb, fa = fanout_orders(Q), fanout_orders(P)
        for k, order in list(fa.items()):          # a send fires its receivers right there
            fa[k] = [d for dst in order for d in (pairs.get(dst[0]) or [dst])]
        for key, order in fb.items():
            if key in fa and fa[key] != order:
                s = Q.by_id[key[0]]
                probs['fanout_order'].append(
                    f"{P.path}: {label(s)} outlet {key[1]} fired {order}, now fires {fa[key]}")
    return probs


WEIGHTS = {'diagonal': 3, 'jog': 4, 'crossing': 2, 'overlap': 5, 'through_box': 6,
           'box_overlap': 8, 'too_close': 3, 'off_grid': 0.5, 'presentation': 2, 'sprawl': 0,
           'long_cord': 0, 'wiring_error': 0, 'wiring_warning': 0}


def lint_doc(doc, before=None, recurse=True, presentation=False, min_gap=0.0):
    """Lint a loaded patch. Returns {'score', 'totals', 'patchers', 'behaviour'}.
    before: the original doc, to check that behaviour didn't change."""
    report, totals = {}, {}
    for P in walk_patchers(doc['patcher'], recurse=recurse):
        iss = lint_patcher(P, presentation, min_gap)
        report[P.path] = {k: v for k, v in iss.items() if v}
        report[P.path]['_counts'] = {'boxes': len(P.boxes), 'cords': len(P.lines)}
        for k, v in iss.items():
            totals[k] = totals.get(k, 0) + len(v)
    behaviour = {}
    if before is not None:
        behaviour = compare(before, doc, recurse)
        for k, v in behaviour.items():
            totals[k] = len(v)
    ncords = sum(r['_counts']['cords'] for r in report.values()) or 1
    # 100 = perfect board, weighted by how ugly each defect looks.  Sprawl is a
    # per-patcher verdict rather than a per-cord defect, so it caps the score instead.
    sprawl_cap = 80.0 if totals.get('sprawl') else 100.0
    penalty = sum(totals.get(k, 0) * v for k, v in WEIGHTS.items())
    score = min(sprawl_cap, max(0.0, 100.0 - 100.0 * penalty / (ncords * 6 + 10)))
    advice = [f"{path}: {s}" for path, r in report.items()
              for k in ('wiring_error', 'wiring_warning', 'long_cord') for s in r.get(k, [])]
    return {'score': round(score, 1), 'totals': totals, 'patchers': report,
            'behaviour': behaviour, 'cords': ncords, 'advice': advice}


def print_report(res, max_list=6):
    print(f"tidiness score: {res['score']:.1f}/100   ({res['cords']} cords)")
    print('  ' + '  '.join(f"{k}={v}" for k, v in res['totals'].items()))
    for path, r in res['patchers'].items():
        items = {k: v for k, v in r.items() if not k.startswith('_')}
        if not items:
            continue
        print(f"\n[{path}]  {r['_counts']['boxes']} boxes, {r['_counts']['cords']} cords")
        for k, v in items.items():
            print(f"  {k} ({len(v)}):")
            for s in v[:max_list]:
                print(f"    - {s}")
            if len(v) > max_list:
                print(f"    ... {len(v) - max_list} more")
    for k, v in res['behaviour'].items():
        if v:
            print(f"\n!! {k} ({len(v)}) - patch behaviour changed:")
            for s in v[:max_list * 2]:
                print(f"    - {s}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('patch')
    ap.add_argument('--before')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--no-recurse', action='store_true')
    ap.add_argument('--presentation', action='store_true')
    ap.add_argument('--min-gap', type=float, default=0.0, help='flag objects closer than this (house style: 45)')
    ap.add_argument('--max-list', type=int, default=6, help='examples to print per check')
    a = ap.parse_args()
    res = lint_doc(load(a.patch), load(a.before) if a.before else None,
                   not a.no_recurse, a.presentation, a.min_gap)
    if a.json:
        out = dict(res)
        out.pop('cords')
        print(json.dumps(out, indent=1))
    else:
        print_report(res, a.max_list)
    sys.exit(1 if any(res['behaviour'].values()) else 0)


if __name__ == '__main__':
    main()
