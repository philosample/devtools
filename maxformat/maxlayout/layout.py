"""Place the boxes of a Max patch like components on a circuit board.

Two modes:

  tidy  (default) - light touch for patches a human already organised.  Keeps the
        overall arrangement but: refits object widths to the 7.5px pin pitch,
        snaps to the grid, opens up cramped vertical gaps by inserting space,
        nudges near-aligned boxes so their cords become perfectly straight, and
        pushes overlapping boxes apart.  Comments move with the object they label.

  full  - layered (Sugiyama-style) re-layout: signal flows top to bottom, rows of
        objects, crossings minimised, each box slid sideways so its inlet sits
        directly under the outlet feeding it.  Existing panels are treated as
        modules: their contents are laid out as a block and the panel is resized
        around it, so the board reads as a set of chips.

Both modes keep every connection, and (unless --fanout ignore) check that no fan-out (one control outlet
feeding several inlets) changes its right-to-left firing order.  If one would,
`--fanout trigger` makes the order explicit with a [t] object (typed b/i/f/l/s to match the source) instead
(semantics preserved; it's also Max best practice), `--fanout keep` reverts the
offending moves (tidy) or reports and exits 1 (full).

Usage:
  python3 layout.py in.maxpat -o out.maxpat [--mode tidy|full] [--no-refit]
                    [--fanout keep|trigger] [--no-recurse] [--subpatcher obj-12]
                    [--hgap 30] [--vgap 30]

Run route.py afterwards to draw the cords.
"""
import argparse, os, re, sys, statistics
from .maxpat import (load, save, walk_patchers, Patcher, text_width, pitch_width, snap, next_id,
                     guess_ports, trigger_letter,
                    net_kind, fix_comment_heights, io_order, restore_io_order,
                    PITCH, GRID, PORT_INSET)

TEXT_BOXES = ('newobj', 'message')


# --------------------------------------------------------------------------- utils
def refit_widths(P):
    """Give object/message boxes a width whose port spacing is a multiple of 7.5px,
    so any outlet can sit exactly above any inlet (the 'pin pitch')."""
    for b in P.boxes:
        if b.maxclass not in TEXT_BOXES or not b.d.get('text'):
            continue
        x, y, w, h = b.rect
        fs = float(b.d.get('fontsize', 12))
        need = max(text_width(b.text, fs), 32.0)
        # fit the box to its text (like a freshly typed Max object), on the pin pitch
        b.rect = [x, y, pitch_width(need, b.nin, b.nout), h]


def comment_anchors(P):
    """Map comment id -> (anchor box id, dx, dy).  A comment labels the closest
    non-comment box beside it (same row, within 45px) or just above/below it."""
    nodes = [b for b in P.boxes if b.is_node]
    out = {}
    for c in P.boxes:
        if c.maxclass != 'comment':
            continue
        cx, cy, cw, ch = c.rect
        best, bd = None, 1e9
        for b in nodes:
            x, y, w, h = b.rect
            vert_overlap = cy < y + h and y < cy + ch
            horiz_overlap = cx < x + w and x < cx + cw
            if vert_overlap:
                d = min(abs(cx - (x + w)), abs(x - (cx + cw)))
            elif horiz_overlap:
                d = min(abs(cy - (y + h)), abs(y - (cy + ch)))
            else:
                continue
            if d < bd and d <= 45:
                best, bd = b, d
        if best:
            out[c.id] = (best.id, cx - best.rect[0], cy - best.rect[1])
    return out


def move_comments(P, anchors):
    for cid, (bid, dx, dy) in anchors.items():
        c, b = P.by_id[cid], P.by_id[bid]
        x, y, w, h = c.rect
        if abs(dy) < 8:          # a label beside a box shares its top edge
            dy = 0.0
        c.rect = [snap(b.rect[0] + dx, PITCH / 2), b.rect[1] + dy, w, h]
    settle_comments(P)


def settle_comments(P):
    """Move any comment that collides with a box to the nearest free spot: right of the
    box it hits, else above it.  Comments wrap to their width in Max, so their rect is
    the real footprint."""
    solid = [b for b in P.boxes if b.is_node]
    texts = [c for c in P.boxes if c.maxclass == 'comment']
    for c in texts:
        for _ in range(6):
            hit = next((b for b in solid if overlaps(c.rect, b.rect, 2)), None)
            if hit is None:
                break
            x, y, w, h = c.rect
            right = [hit.rect[0] + hit.rect[2] + 8, y, w, h]
            above = [x, hit.rect[1] - h - 6, w, h]
            ok_r = not any(overlaps(right, b.rect, 2) for b in solid)
            ok_a = not any(overlaps(above, b.rect, 2) for b in solid) and above[1] >= 0
            c.rect = right if ok_r or not ok_a else above


def keep_on_canvas(P, margin=15.0):
    xs = [b.rect[0] for b in P.boxes]
    ys = [b.rect[1] for b in P.boxes]
    if not xs:
        return
    dx = max(0.0, margin - min(xs))
    dy = max(0.0, margin - min(ys))
    dx, dy = (snap(dx + GRID / 2, GRID) if dx else 0.0), (snap(dy + GRID / 2, GRID) if dy else 0.0)
    if dx or dy:
        for b in P.boxes:
            x, y, w, h = b.rect
            b.rect = [x + dx, y + dy, w, h]


def overlaps(r1, r2, gap=0.0):
    return (r1[0] < r2[0] + r2[2] + gap and r2[0] < r1[0] + r1[2] + gap and
            r1[1] < r2[1] + r2[3] + gap and r2[1] < r1[1] + r1[3] + gap)


def fanout_orders(P):
    groups = {}
    for ln in P.lines:
        s = P.by_id[ln.src[0]]
        if net_kind(s, ln.src[1]) in ('audio', 'mc'):
            continue
        groups.setdefault(tuple(ln.src), []).append(ln)
    out = {}
    for k, lns in groups.items():
        if len(lns) > 1:
            out[k] = [tuple(l.dst) for l in sorted(
                lns, key=lambda l: (-P.by_id[l.dst[0]].inlet_xy(l.dst[1])[0],
                                    -P.by_id[l.dst[0]].inlet_xy(l.dst[1])[1]))]
    return out


def insert_triggers(P, before, after):
    """Replace each fan-out whose order changed with an explicit [t ...] (typed to match the source: [t f f] after mtof) that
    fires the destinations in the ORIGINAL order (trigger fires right-to-left)."""
    added = 0
    for key, order in before.items():
        if after.get(key) == order:
            continue
        src = P.by_id[key[0]]
        kind = trigger_letter(src.d, key[1])
        if kind is None:
            continue          # unknown data type: a guessed [t] could change behaviour
        k = len(order)
        tid = next_id(P.d)
        x, y, w, h = src.rect
        ttext = 't' + (' ' + kind) * k
        tw = pitch_width(text_width(ttext), 1, k)
        box = {'id': tid, 'maxclass': 'newobj', 'numinlets': 1, 'numoutlets': k,
               'outlettype': [{'b': 'bang', 'i': 'int', 'f': 'float', 'l': 'list'}.get(kind, '')] * k, 'patching_rect': [x, y + h + 15, tw, 22.0],
               'text': ttext}
        P.d['boxes'].append({'box': box})
        # remove old fan-out cords, wire src -> t -> dests (first-to-fire on the right)
        keep = []
        for l in P.d['lines']:
            pl = l['patchline']
            if tuple(pl['source']) == key:
                continue
            keep.append(l)
        P.d['lines'] = keep
        P.d['lines'].append({'patchline': {'source': [key[0], key[1]], 'destination': [tid, 0]}})
        for i, (did, inl) in enumerate(order):
            P.d['lines'].append({'patchline': {'source': [tid, k - 1 - i], 'destination': [did, inl]}})
        added += 1
    return added


# --------------------------------------------------------------------------- tidy
def tidy(P, refit=True, fanout='keep', min_gap=15.0):
    fix_comment_heights(P)
    before = fanout_orders(P)
    io_before = io_order(P)
    anchors = comment_anchors(P)
    if refit:
        refit_widths(P)
    nodes = [b for b in P.boxes if b.is_node]
    gx, gy = P.grid
    # 1. snap
    for b in nodes:
        x, y, w, h = b.rect
        b.rect = [snap(x, gx / 2), snap(y, gy), w, h]
    # 2. open up cramped vertical gaps between connected boxes by inserting space
    #    below the upper box (shifting everything lower down), like adding a row.
    for _ in range(3):
        changed = False
        for ln in sorted(P.lines, key=lambda l: P.by_id[l.dst[0]].rect[1]):
            s, t = P.by_id[ln.src[0]], P.by_id[ln.dst[0]]
            if not (s.is_node and t.is_node) or s is t:
                continue
            gap = t.rect[1] - (s.rect[1] + s.rect[3])
            if -1 < gap < min_gap:
                need = snap(min_gap - gap + gy / 2, gy) if (min_gap - gap) % gy else min_gap - gap
                cut = t.rect[1] - 0.5
                for b in nodes:
                    if b.rect[1] >= cut:
                        x, y, w, h = b.rect
                        b.rect = [x, y + need, w, h]
                changed = True
        if not changed:
            break
    # 3. straighten near-aligned cords: slide a box sideways so its port lines up
    def collides(b, r):
        return any(o is not b and overlaps(r, o.rect, 4) for o in nodes)

    incoming = {}
    outgoing = {}
    for ln in P.lines:
        incoming.setdefault(ln.dst[0], []).append(ln)
        outgoing.setdefault(ln.src[0], []).append(ln)
    moved = set()
    for ln in sorted(P.lines, key=lambda l: (P.by_id[l.src[0]].rect[1], P.by_id[l.src[0]].rect[0])):
        s, t = P.by_id[ln.src[0]], P.by_id[ln.dst[0]]
        if s is t or not (s.is_node and t.is_node):
            continue
        sx, sy = s.outlet_xy(ln.src[1])
        tx, ty = t.inlet_xy(ln.dst[1])
        dx = sx - tx
        if abs(dx) < 0.01 or abs(dx) > 45 or ty <= sy:
            continue
        cands = []
        if t.id not in moved and len({l.src[0] for l in incoming.get(t.id, [])}) == 1:
            cands.append((t, dx))
        if s.id not in moved and len({l.dst[0] for l in outgoing.get(s.id, [])}) == 1 \
                and not incoming.get(s.id):
            cands.append((s, -dx))
        if t.id not in moved and (t, dx) not in cands:
            cands.append((t, dx))
        for b, d in cands:
            x, y, w, h = b.rect
            r = [x + d, y, w, h]
            if collides(b, r):
                continue
            old = b.rect
            b.rect = r
            now = fanout_orders(P)
            if (fanout == 'keep' and any(now.get(k) != v for k, v in before.items())) \
                    or io_order(P) != io_before:
                b.rect = old
                continue
            moved.add(b.id)
            break
    # 4. push apart overlapping boxes (left-to-right, row by row)
    for _ in range(4):
        hit = False
        ordered = sorted(nodes, key=lambda b: (b.rect[1], b.rect[0]))
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                if overlaps(a.rect, b.rect, 2):
                    x, y, w, h = b.rect
                    b.rect = [snap(a.rect[0] + a.rect[2] + gx, gx / 2), y, w, h]
                    hit = True
        if not hit:
            break
    restore_io_order(P, io_before)     # overlap pushes can also reorder them
    move_comments(P, anchors)
    keep_on_canvas(P)
    return finish_fanout(P, before, fanout)


def finish_fanout(P, before, fanout):
    if fanout == 'ignore':
        return [], 0
    after = fanout_orders(P)
    broken = {k: v for k, v in before.items() if after.get(k) != v}
    if broken and fanout == 'trigger':
        n = insert_triggers(P, before, after)
        after = fanout_orders(Patcher(P.d, P.path))     # re-read: P's cached lines are stale
        # a fan-out now fed through a [t] is no longer a fan-out at all - that's the fix
        left = {k: v for k, v in before.items() if k in after and after[k] != v}
        return [f"{k[0]} outlet {k[1]}: can't tell what data type it sends, so a [t] can't be "
                f"typed safely; its firing order would change {before[k]} -> {after.get(k)}"
                for k in left], n
    return [f"{k[0]} outlet {k[1]}: {before[k]} -> {after.get(k)}" for k in broken], 0


# --------------------------------------------------------------------------- full
class Item:
    """A placeable thing: a single box, or a module (group of boxes) laid out as a block."""
    def __init__(self, iid, w, h, members=None, rel=None):
        self.id, self.w, self.h = iid, w, h
        self.members = members or [iid]
        self.rel = rel or {iid: (0.0, 0.0)}
        self.x = self.y = 0.0
        self.layer = 0
        self.dummy = False


def layered(items, edges, hint, hgap=30.0, vgap=30.0, fans=(), max_width=720.0, fixed_layers=None):
    """Sugiyama layout.  items: {id: Item}; edges: [(u, ox, v, ix)] where ox/ix are
    x-offsets of the outlet/inlet inside u/v.  hint: {id: (x, y)} original position
    used to seed the ordering, so a human's left-to-right intent survives."""
    ids = list(items)
    succ = {i: [] for i in ids}
    pred = {i: [] for i in ids}
    E = []
    for (u, ox, v, ix) in edges:
        if u == v:
            continue
        E.append([u, ox, v, ix])
    # cycle breaking: DFS in hint order, reverse back edges
    order = sorted(ids, key=lambda i: (hint.get(i, (0, 0))[1], hint.get(i, (0, 0))[0]))
    state = {}
    adj = {i: [] for i in ids}
    for e in E:
        adj[e[0]].append(e)
    rev = set()

    def dfs(n):
        stack = [(n, iter(adj[n]))]
        state[n] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            v = nxt[2]
            if state.get(v) == 1:
                rev.add(id(nxt))
            elif v not in state:
                state[v] = 1
                stack.append((v, iter(adj[v])))
    for n in order:
        if n not in state:
            dfs(n)
    DE = []
    for e in E:
        u, ox, v, ix = e
        if id(e) in rev:
            DE.append((v, ix, u, ox, True))
        else:
            DE.append((u, ox, v, ix, False))
    for u, _, v, _, _ in DE:
        succ[u].append(v)
        pred[v].append(u)
    # longest-path layering
    layer = {}
    topo = []
    indeg = {i: len(pred[i]) for i in ids}
    q = [i for i in order if indeg[i] == 0]
    while q:
        n = q.pop(0)
        topo.append(n)
        for v in succ[n]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    for n in topo:
        layer[n] = max([layer[p] + 1 for p in pred[n]] or [0])
    if fixed_layers is not None:        # rows decided by the caller (e.g. shared across columns)
        for n in ids:
            layer[n] = fixed_layers[n]
    # pull sources (number boxes, messages, loadbang) down next to what they feed
    for n in (reversed(topo) if fixed_layers is None else []):
        if not pred[n] and succ[n]:
            layer[n] = max(layer[n], min(layer[s] for s in succ[n]) - 1)
    # Wide rows of pure sources (e.g. ten message boxes all feeding one object) become
    # a ladder: stacked rows above their target, like the rungs a human would draw.
    # Safe, because nothing feeds a source.
    if max_width and fixed_layers is None:
        by = {}
        for i in ids:
            by.setdefault(layer[i], []).append(i)
        shift = {}
        extra_total = 0
        for L in sorted(by):
            row = sorted(by[L], key=lambda i: hint.get(i, (0, 0))[0])
            if sum(items[i].w + hgap for i in row) <= max_width:
                continue
            fixed = [i for i in row if pred[i]]
            free = [i for i in row if not pred[i]]
            budget = max(max_width - sum(items[i].w + hgap for i in fixed), max_width / 2)
            # one rung per row: the rungs then share a column and their cords merge into
            # a single vertical bus, exactly how people draw a stack of message boxes
            ordered = sorted(free, key=lambda i: (hint.get(i, (0, 0))[1], hint.get(i, (0, 0))[0]))
            keep_n = 0
            cw = 0.0
            for i in reversed(ordered):         # as many as fit stay beside the fixed nodes
                if cw + items[i].w + hgap > budget - sum(items[j].w + hgap for j in fixed) / 2:
                    break
                cw += items[i].w + hgap
                keep_n += 1
            keep_n = max(1, min(keep_n, 2))
            chunks = [[i] for i in ordered[:len(ordered) - keep_n]] + [ordered[len(ordered) - keep_n:]]
            if len(chunks) <= 1:
                continue
            # last chunk stays in row L (next to its target); earlier chunks go above
            for k, ch in enumerate(chunks[:-1]):
                for i in ch:
                    shift[i] = -(len(chunks) - 1 - k)
        if shift:
            # renumber: every row L gets room for the ladder rungs inserted before it
            rungs = {}
            for i, d in shift.items():
                rungs[layer[i]] = max(rungs.get(layer[i], 0), -d)
            def newL(L):
                return L + sum(r for LL, r in rungs.items() if LL <= L)
            base = {i: newL(layer[i]) for i in ids}
            for i in ids:
                layer[i] = base[i] + shift.get(i, 0)
    for i in ids:
        items[i].layer = layer[i]
    # dummy nodes for long edges
    chains = []
    dn = 0
    segs = []
    # Long edges get a chain of zero-width dummy nodes, one per row they cross.  Edges
    # into the same inlet (or out of the same outlet) share their dummies, so a bus is
    # laid out as ONE column rather than a staircase of parallel wires.
    shared = {}
    seen_seg = set()
    for (u, ox, v, ix, r) in DE:
        lu, lv = layer[u], layer[v]
        prev, pox = u, ox
        path = []
        for L in range(lu + 1, lv):
            did = shared.get(('in', v, ix, L)) or shared.get(('out', u, ox, L))
            if did is None:
                did = f"__d{dn}"
                dn += 1
                it = Item(did, 0.0, 0.0)
                it.dummy = True
                it.layer = L
                items[did] = it
                shared[('in', v, ix, L)] = did
                shared.setdefault(('out', u, ox, L), did)
            if (prev, pox, did) not in seen_seg:
                segs.append((prev, pox, did, 0.0))
                seen_seg.add((prev, pox, did))
            prev, pox = did, 0.0
            path.append(did)
        if (prev, pox, v, ix) not in seen_seg:
            segs.append((prev, pox, v, ix))
            seen_seg.add((prev, pox, v, ix))
        chains.append(path)
    nL = max(it.layer for it in items.values()) + 1
    layers = [[] for _ in range(nL)]
    for it in items.values():
        layers[it.layer].append(it.id)
    for L in layers:
        L.sort(key=lambda i: hint.get(i, (1e9, 0))[0])
    # dummies: seed at their source's x
    up = {}
    down = {}
    for (u, ox, v, ix) in segs:
        down.setdefault(u, []).append((v, ox, ix))
        up.setdefault(v, []).append((u, ox, ix))

    def crossings(lay):
        tot = 0
        for a, b in zip(lay, lay[1:]):
            pos = {n: i for i, n in enumerate(b)}
            es = []
            for i, n in enumerate(a):
                for (v, ox, ix) in down.get(n, []):
                    if v in pos:
                        es.append((i + ox / 1000.0, pos[v] + ix / 1000.0))
            es.sort()
            for i in range(len(es)):
                for j in range(i + 1, len(es)):
                    if es[j][1] < es[i][1]:
                        tot += 1
        return tot

    best = [list(L) for L in layers]
    bestc = crossings(best)
    for sweep in range(24):
        downward = sweep % 2 == 0
        rng = range(1, nL) if downward else range(nL - 2, -1, -1)
        for li in rng:
            ref = layers[li - 1] if downward else layers[li + 1]
            pos = {n: i for i, n in enumerate(ref)}
            bary = {}
            for i, n in enumerate(layers[li]):
                nb = up.get(n, []) if downward else down.get(n, [])
                vals = [pos[m] + (ox if downward else ix) / 400.0 for (m, ox, ix) in nb if m in pos]
                bary[n] = sum(vals) / len(vals) if vals else i
            layers[li].sort(key=lambda n: bary[n])
        c = crossings(layers)
        if c < bestc:
            best, bestc = [list(L) for L in layers], c
    layers = best

    # Transposition: swap neighbours in a row whenever that removes crossings with the
    # rows above and below.  Cheap, and catches what the barycenter sweeps miss.
    def pair_crossings(a, b):
        if not a or not b:
            return 0
        pos = {n: i for i, n in enumerate(b)}
        es = []
        for i, n in enumerate(a):
            for (v, ox, ix) in down.get(n, []):
                if v in pos:
                    es.append((i + ox / 1000.0, pos[v] + ix / 1000.0))
        es.sort()
        return sum(1 for i in range(len(es)) for j in range(i + 1, len(es)) if es[j][1] < es[i][1])

    def local(li):
        c = 0
        if li > 0:
            c += pair_crossings(layers[li - 1], layers[li])
        if li + 1 < nL:
            c += pair_crossings(layers[li], layers[li + 1])
        return c

    for _ in range(8):
        improved = False
        for li in range(nL):
            L = layers[li]
            for k in range(len(L) - 1):
                before_c = local(li)
                if before_c == 0:
                    break
                L[k], L[k + 1] = L[k + 1], L[k]
                if local(li) < before_c:
                    improved = True
                else:
                    L[k], L[k + 1] = L[k + 1], L[k]
        if not improved:
            break
    # fan-outs: destinations sharing a layer keep their original left-to-right order,
    # because Max fires a fan-out right-to-left by position
    for L in layers:
        pos = {n: i for i, n in enumerate(L)}
        for grp in fans:
            here = [n for n in grp if n in pos]
            if len(here) > 1:
                slots = sorted(pos[n] for n in here)
                for slot, n in zip(slots, here):
                    L[slot] = n
    # y: rows
    y = 0.0
    for li, L in enumerate(layers):
        hmax = max([items[n].h for n in L] + [0])
        for n in L:
            items[n].y = y
        lanes = sum(1 for n in L for _ in down.get(n, []))
        y = snap(y + hmax + vgap + min(lanes, 8) * PITCH / 2, GRID)
        if y < items[L[0]].y + hmax + vgap if L else False:
            y += GRID
    # x: start packed, then iterate median alignment + order-preserving packing
    for L in layers:
        x = 0.0
        for n in L:
            items[n].x = x
            x += items[n].w + hgap

    def pack(L, desired):
        """Least-squares positions keeping order and spacing (pool adjacent violators)."""
        blocks = []
        for n in L:
            gapw = (hgap if not items[n].dummy else PITCH * 2)
            blocks.append([[n], desired[n], 1])
            while len(blocks) > 1:
                a, b = blocks[-2], blocks[-1]
                aw = sum(items[m].w + (hgap if not items[m].dummy else PITCH * 2) for m in a[0])
                if a[1] + aw <= b[1]:
                    break
                # merge: block start minimising squared error
                mem = a[0] + b[0]
                offs, o = [], 0.0
                for m in mem:
                    offs.append(o)
                    o += items[m].w + (hgap if not items[m].dummy else PITCH * 2)
                start = sum(desired[m] - off for m, off in zip(mem, offs)) / len(mem)
                blocks[-2:] = [[mem, start, len(mem)]]
        for mem, start, _ in blocks:
            o = start
            for m in mem:
                items[m].x = o
                o += items[m].w + (hgap if not items[m].dummy else PITCH * 2)

    for it_ in range(10):
        downward = it_ % 2 == 0
        rng = range(1, nL) if downward else range(nL - 2, -1, -1)
        for li in rng:
            desired = {}
            for n in layers[li]:
                nb = up.get(n, []) if downward else down.get(n, [])
                vals = []
                for (m, ox, ix) in nb:
                    if downward:
                        vals.append(items[m].x + ox - ix)
                    else:
                        vals.append(items[m].x + ix - ox)
                desired[n] = statistics.median(vals) if vals else items[n].x
            pack(layers[li], desired)
    # snap to pin pitch, then repair any spacing lost to rounding
    for L in layers:
        prev_end = None
        for n in L:
            it = items[n]
            it.x = snap(it.x, PITCH)
            if prev_end is not None and it.x < prev_end:
                it.x = snap(prev_end + PITCH / 2, PITCH)
            prev_end = it.x + it.w + (hgap if not it.dummy else PITCH * 2) - 0.01
    minx = min(it.x for it in items.values())
    for it in items.values():
        it.x -= minx
    for k in [k for k in items if k.startswith('__d')]:
        del items[k]
    return items


def footprint(P, b, labels):
    """A box plus the comments that label it, like a PCB footprint plus its silkscreen.
    Returns (w, h, offx, offy): footprint size and where the box sits inside it."""
    x, y, w, h = b.rect
    l, t, r, btm = x, y, x + w, y + h
    for cid in labels.get(b.id, []):
        cx, cy, cw, ch = P.by_id[cid].rect
        l, t, r, btm = min(l, cx), min(t, cy), max(r, cx + cw), max(btm, cy + ch)
    return r - l, btm - t, x - l, y - t


def full(P, refit=True, fanout='keep', hgap=30.0, vgap=30.0, origin=(30.0, 30.0)):
    fix_comment_heights(P)
    before = fanout_orders(P)
    io_before = io_order(P)
    anchors = comment_anchors(P)
    if refit:
        refit_widths(P)
    nodes = [b for b in P.boxes if b.is_node]
    node_ids = {b.id for b in nodes}
    # modules from panels: a node belongs to the smallest panel containing its centre
    panels = [b for b in P.boxes if b.maxclass == 'panel']
    member_of = {}
    for b in nodes:
        x, y, w, h = b.rect
        cx, cy = x + w / 2, y + h / 2
        best = None
        for p in panels:
            px, py, pw, ph = p.rect
            if px <= cx <= px + pw and py <= cy <= py + ph:
                if best is None or pw * ph < best.rect[2] * best.rect[3]:
                    best = p
        if best is not None:
            member_of[b.id] = best.id
    # panel titles: comments inside a panel near its top edge
    titles = {}
    for c in P.boxes:
        if c.maxclass != 'comment':
            continue
        for p in panels:
            px, py, pw, ph = p.rect
            cx, cy = c.rect[0], c.rect[1]
            if px <= cx <= px + pw and py <= cy <= py + 30:
                titles[c.id] = p.id
                anchors.pop(c.id, None)
    groups = {}
    for bid, pid in member_of.items():
        groups.setdefault(pid, []).append(bid)

    def port_off(b, kind, i):
        x = b.rect[0]
        return (b.outlet_xy(i)[0] if kind == 'out' else b.inlet_xy(i)[0]) - x

    hint = {b.id: (b.rect[0], b.rect[1]) for b in nodes}
    # left-to-right order each fan-out's destinations must keep (reverse of firing order)
    fan_boxes = []
    for order in (before.values() if fanout != 'ignore' else []):
        seq = []
        for did, _ in reversed(order):
            if did not in seq:
                seq.append(did)
        fan_boxes.append(seq)
    fan_boxes += [list(g) for g in io_before if len(g) > 1]   # subpatcher port order
    labels = {}
    for cid, (bid, _, _) in anchors.items():
        labels.setdefault(bid, []).append(cid)
    fp = {b.id: footprint(P, b, labels) for b in nodes}
    # lay out each module internally
    items = {}
    pad, title_h = 15.0, 0.0
    for pid, mem in groups.items():
        sub = {m: Item(m, fp[m][0], fp[m][1]) for m in mem}
        es = [(l.src[0], fp[l.src[0]][2] + port_off(P.by_id[l.src[0]], 'out', l.src[1]), l.dst[0],
               fp[l.dst[0]][2] + port_off(P.by_id[l.dst[0]], 'in', l.dst[1]))
              for l in P.lines if l.src[0] in sub and l.dst[0] in sub]
        layered(sub, es, hint, hgap, vgap, fan_boxes)
        tcid = next((c for c, p in titles.items() if p == pid), None)
        th = 30.0 if tcid else 0.0
        w = max(it.x + it.w for it in sub.values()) + 2 * pad
        h = max(it.y + it.h for it in sub.values()) + 2 * pad + th
        rel = {m: (it.x + pad + fp[m][2], it.y + pad + th + fp[m][3]) for m, it in sub.items()}
        item = Item(pid, w, h, mem, rel)
        item.title_x = 10.0
        if tcid:
            # The title must not sit where a cord drops into the module, or the
            # router has to box around the text.  Find a free stretch of the top band.
            memset = set(mem)
            entries = [rel[l.dst[0]][0] + port_off(P.by_id[l.dst[0]], 'in', l.dst[1])
                       for l in P.lines if l.dst[0] in memset and l.src[0] not in memset]
            tw = text_width(P.by_id[tcid].text) + 6
            spot = None
            x = 8.0
            while x + tw <= w - 8:
                if all(not (x - 10 <= e <= x + tw + 10) for e in entries):
                    spot = x
                    break
                x += PITCH
            if spot is None:      # no gap: widen the module and put the title on the right
                spot = w - pad + 10
                item.w = w = spot + tw + pad
            item.title_x = spot
        items[pid] = item
        px, py = P.by_id[pid].rect[:2]
        hint[pid] = (px, py)
    for b in nodes:
        if b.id not in member_of:
            w_, h_, ox_, oy_ = fp[b.id]
            items[b.id] = Item(b.id, w_, h_, rel={b.id: (ox_, oy_)})
    owner = {m: iid for iid, it in items.items() for m in it.members}
    top_edges = []
    for l in P.lines:
        if l.src[0] not in owner or l.dst[0] not in owner:
            continue
        u, v = owner[l.src[0]], owner[l.dst[0]]
        if u == v:
            continue
        su, sv = P.by_id[l.src[0]], P.by_id[l.dst[0]]
        top_edges.append((u, items[u].rel[su.id][0] + port_off(su, 'out', l.src[1]),
                          v, items[v].rel[sv.id][0] + port_off(sv, 'in', l.dst[1])))
    # modules need more breathing room than single boxes
    top_fans = []
    for seq in fan_boxes:
        t = []
        for m in seq:
            o = owner.get(m)
            if o and o not in t:
                t.append(o)
        top_fans.append(t)
    layered(items, top_edges, hint, hgap, vgap, top_fans)
    ox, oy = origin
    # leave room at the top for free-floating comments (titles / notes)
    free = [c for c in P.boxes if c.maxclass == 'comment' and c.id not in anchors and c.id not in titles]
    free.sort(key=lambda c: (c.rect[1], c.rect[0]))
    y = oy
    for c in free:
        x_, y_, w, h = c.rect
        c.rect = [ox, y, w, h]
        y = snap(y + h + 4, PITCH)
    if free:
        oy = snap(y + 15, GRID)
    for iid, it in items.items():
        for m in it.members:
            rx, ry = it.rel[m]
            b = P.by_id[m]
            b.rect = [snap(ox + it.x + rx, PITCH), snap(oy + it.y + ry, PITCH / 2), b.rect[2], b.rect[3]]
        if iid in groups:
            p = P.by_id[iid]
            p.rect = [snap(ox + it.x, PITCH), snap(oy + it.y, PITCH / 2), it.w, it.h]
    for cid, pid in titles.items():
        c, p = P.by_id[cid], P.by_id[pid]
        tx = getattr(items.get(pid), 'title_x', 10.0)
        c.rect = [snap(p.rect[0] + tx, PITCH / 2), p.rect[1] + 6, text_width(c.text) + 6, c.rect[3]]
    restore_io_order(P, io_before)
    move_comments(P, anchors)
    # comments that now collide with a box: nudge right of it
    for cid in anchors:
        c = P.by_id[cid]
        for b in nodes:
            if overlaps(c.rect, b.rect, 1):
                x, y, w, h = c.rect
                c.rect = [b.rect[0] + b.rect[2] + 8, y, w, h]
    return finish_fanout(P, before, fanout)


def _token(b):
    t = str(b.text or b.maxclass).split()
    word = t[0] if t else b.maxclass
    if word in ('t', 'trigger', 's', 'r', 'p') and len(t) > 1:
        word = t[1]
    word = re.sub(r'[^a-z0-9]+', '-', word.lower().replace('~', '')).strip('-')
    return word[:20] or 'x'


def insert_sends(P, lines):
    """Replace each cord with a send/receive pair named source-destination
    (#0-startwindow-dac), the way the user does it by hand. Audio uses send~/receive~.
    #0 keeps the name private to this patcher instance."""
    used = {str(b.text).split()[1] for b in P.boxes
            if b.maxclass == 'newobj' and len(str(b.text).split()) > 1
            and str(b.text).split()[0] in ('s', 'r', 'send', 'receive', 'send~', 'receive~')}
    done = 0
    for ln in lines:
        s, t = P.by_id[ln.src[0]], P.by_id[ln.dst[0]]
        audio = net_kind(s, ln.src[1]) in ('audio', 'mc')
        name = f"#0-{_token(s)}-{_token(t)}"
        k = 2
        while name in used:
            name, k = f"#0-{_token(s)}-{_token(t)}-{k}", k + 1
        used.add(name)
        x, y, w, h = s.rect
        tx, ty, tw, th = t.rect
        new = []
        for text, rect in ((('send~ ' if audio else 's ') + name, [x, y + h + 20, 0, 22.0]),
                           (('receive~ ' if audio else 'r ') + name, [tx, ty - 42, 0, 22.0])):
            nin, nout, ot = guess_ports(text)
            rect[2] = pitch_width(text_width(text), nin, nout)
            box = {'id': next_id(P.d), 'maxclass': 'newobj', 'text': text, 'numinlets': nin,
                   'numoutlets': nout, 'outlettype': ot, 'patching_rect': rect}
            P.d['boxes'].append({'box': box})
            new.append(box['id'])
        P.d['lines'] = [l for l in P.d['lines'] if l['patchline'] is not ln.d]
        P.d['lines'].append({'patchline': {'source': [s.id, ln.src[1]], 'destination': [new[0], 0]}})
        P.d['lines'].append({'patchline': {'source': [new[1], 0], 'destination': [t.id, ln.dst[1]]}})
        done += 1
    return done


def flow(P, refit=True, fanout='keep'):
    """House-style re-layout: a left-aligned spine with branches (see spine.py)."""
    from . import spine
    fix_comment_heights(P)
    before = fanout_orders(P)
    io_before = io_order(P)
    if refit:
        refit_widths(P)
    spine.place(P)
    restore_io_order(P, io_before)
    return finish_fanout(P, before, fanout)


def layout_doc(doc, mode='tidy', refit=True, fanout='keep', recurse=True, subpatcher=None,
               hgap=30.0, vgap=30.0):
    """Lay out every patcher in doc in place. Returns (problems, triggers_inserted).
    If problems is non-empty the layout would change behaviour: don't save doc."""
    problems, triggers = [], 0
    for P in walk_patchers(doc['patcher'], recurse=recurse):
        if subpatcher and not P.path.split('/')[-1].startswith(subpatcher + ':'):
            continue
        if not any(b.is_node for b in P.boxes):
            continue
        if mode == 'tidy':
            pr, t = tidy(P, refit, fanout)
        elif mode == 'flow':
            pr, t = flow(P, refit, fanout)
            if t:   # place the new trigger objects properly
                flow(Patcher(P.d, P.path), refit, 'keep')
        else:
            pr, t = full(P, refit, fanout, hgap, vgap)
            if t:   # place the new trigger objects properly
                full(Patcher(P.d, P.path), refit, 'keep', hgap, vgap)
        problems += [f"{P.path}: {p}" for p in pr]
        triggers += t
    return problems, triggers


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('-o', '--output')
    ap.add_argument('--mode', choices=['tidy', 'full'], default='tidy')
    ap.add_argument('--no-refit', action='store_true', help="don't change object box widths")
    ap.add_argument('--fanout', choices=['keep', 'trigger', 'ignore'], default='keep')
    ap.add_argument('--no-recurse', action='store_true')
    ap.add_argument('--subpatcher', help='only lay out this embedded subpatcher (box id)')
    ap.add_argument('--hgap', type=float, default=30.0)
    ap.add_argument('--vgap', type=float, default=30.0)
    a = ap.parse_args()
    doc = load(a.input)
    problems, triggers = layout_doc(doc, a.mode, not a.no_refit, a.fanout, not a.no_recurse,
                                    a.subpatcher, a.hgap, a.vgap)
    if problems:
        print("!! NOT SAVED: this layout would change fan-out firing order, i.e. what the patch does:")
        for p in problems:
            print('   ' + p)
        print("   Options: --fanout trigger (make the order explicit with [t] objects),")
        print("            --mode tidy (lighter touch), or lay out that subpatcher by hand.")
        sys.exit(1)
    save(doc, a.output or a.input)
    print(f"laid out ({a.mode})" + (f"; inserted {triggers} trigger object(s) to keep fan-out order" if triggers else ''))


if __name__ == '__main__':
    main()
