"""Spine-and-branches placement: the house style, learned from a hand-arranged patch.

  * The main chain is one vertical spine; every spine object shares the same left
    edge, so left-outlet -> left-inlet cords are straight.
  * Objects directly chained sit CHAIN_GAP apart; everything else keeps GAP.
  * Branches go right of the spine (displays nearest, secondary chains further out);
    send/receive utilities and what hangs off them go left.
  * Objects with no inputs sit just above what they feed.
"""
from .maxpat import object_class, snap, PITCH

CHAIN_GAP = 20.0
GAP = 45.0
LABEL_GAP = 10.0
TITLE_GAP = 30.0
UTILITY = {'send', 'receive', 'send~', 'receive~', 'forward'}
TERMINAL = {'dac~', 'ezdac~', 'mc.dac~', 'outlet'}


def place(P, origin=(30.0, 30.0), chain_gap=CHAIN_GAP, gap=GAP):
    """Position every box of patcher P in place. Returns the layout's height in px."""
    from .layout import comment_anchors          # avoid a circular import
    nodes = [b for b in P.boxes if b.is_node]
    if not nodes:
        return 0.0
    ids = {b.id for b in nodes}
    by = P.by_id
    anchors = comment_anchors(P)
    labels = {}
    for cid, (bid, _, _) in anchors.items():
        if bid in ids:
            labels.setdefault(bid, []).append(cid)

    def foot(n):
        """Width/height of a box plus the labels that ride on its right."""
        x, y, w, h = by[n].rect
        for cid in labels.get(n, []):
            cw, ch = by[cid].rect[2], by[cid].rect[3]
            w, h = w + LABEL_GAP + cw, max(h, ch)
        return w, h

    W = {n: foot(n)[0] for n in ids}
    H = {n: by[n].rect[3] for n in ids}
    hint = {n: (by[n].rect[1], by[n].rect[0]) for n in ids}

    edges = [(l.src[0], l.src[1], l.dst[0], l.dst[1]) for l in P.lines
             if l.src[0] in ids and l.dst[0] in ids and l.src[0] != l.dst[0]]
    # break cycles (feedback): DFS in reading order, drop back edges from the layering
    succ_all = {n: [] for n in ids}
    for e in edges:
        succ_all[e[0]].append(e)
    state, back = {}, set()
    for root in sorted(ids, key=hint.get):
        if root in state:
            continue
        stack = [(root, iter(succ_all[root]))]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            e = next(it, None)
            if e is None:
                state[node] = 2
                stack.pop()
                continue
            if state.get(e[2]) == 1:
                back.add(e)
            elif e[2] not in state:
                state[e[2]] = 1
                stack.append((e[2], iter(succ_all[e[2]])))
    dag = [e for e in edges if e not in back]
    pred = {n: [] for n in ids}
    succ = {n: [] for n in ids}
    for e in dag:
        pred[e[2]].append(e)
        succ[e[0]].append(e)
    indeg = {n: len(pred[n]) for n in ids}
    ready = sorted([n for n in ids if not indeg[n]], key=hint.get)
    topo = []
    while ready:
        n = ready.pop(0)
        topo.append(n)
        for e in succ[n]:
            indeg[e[2]] -= 1
            if not indeg[e[2]]:
                ready.append(e[2])
                ready.sort(key=hint.get)

    # ---- the spine: best path, favouring left-to-left cords, signal, ending at output
    def is_sig(e):
        ot = by[e[0]].d.get('outlettype') or []
        return e[1] < len(ot) and ot[e[1]] in ('signal', 'multichannelsignal')

    score, best_pred = {}, {}
    for n in topo:
        score[n], best_pred[n] = 1.0, None
        for e in pred[n]:
            if e[3] != 0:
                continue          # the spine runs through left inlets; right inlets are fed from the side
            s = score[e[0]] + 1.0 + (0.6 if e[1] == 0 else 0) + (0.3 if is_sig(e) else 0)
            if s > score[n]:
                score[n], best_pred[n] = s, e[0]
    end = max(topo, key=lambda n: (score[n] + (5 if object_class(by[n].d) in TERMINAL else 0),
                                   -hint[n][0]))
    spine = [end]
    while best_pred[spine[-1]] is not None:
        spine.append(best_pred[spine[-1]])
    spine.reverse()
    on_spine = set(spine)

    # ---- sides: utility (send/receive and what hangs only off it) left, the rest right
    side = {n: 'S' for n in spine}
    for n in topo:
        if n in side:
            continue
        cls = object_class(by[n].d)
        if cls in UTILITY or (pred[n] and all(side.get(e[0]) == 'L' for e in pred[n])):
            side[n] = 'L'
    for n in ids:
        side.setdefault(n, 'R')

    # ---- rows: everything sits CHAIN_GAP below whatever feeds it
    y = {}
    for n in topo:
        ps = [e[0] for e in pred[n] if e[0] in y]
        if not ps and n != spine[0]:
            continue                                   # sources: placed later, above targets
        base = max([y[p] + H[p] + chain_gap for p in ps] + [origin[1]])
        if n in on_spine and spine.index(n) > 0:
            prev = spine[spine.index(n) - 1]
            base = max(base, y[prev] + H[prev] + chain_gap)
        y[n] = base
    for n in reversed(topo):                           # sources sit just above what they feed
        if n in y:
            continue
        ts = [y[e[2]] for e in succ[n] if e[2] in y]
        y[n] = (min(ts) - chain_gap - H[n]) if ts else origin[1]
    # sources pushed above the top: shift everything down
    top = min(y.values())
    if top < origin[1]:
        for n in y:
            y[n] += origin[1] - top

    # ---- columns: spine at x0; right/left columns filled greedily without collisions
    def free(col, n):
        for m in col['items']:
            linked = any(e[0] == m and e[2] == n or e[0] == n and e[2] == m for e in dag)
            g = chain_gap if linked else gap
            if y[n] < y[m] + H[m] + g and y[m] < y[n] + H[n] + g:
                return False
        return True

    for n in ids:            # right side splits into near (displays, dead ends) and far (chains)
        if side[n] == 'R':
            side[n] = 'RN' if not succ[n] else 'RF'
    cols = {'RN': [], 'RF': [], 'L': []}
    col_of = {}
    for n in sorted([n for n in ids if side[n] != 'S'], key=lambda n: (y[n], hint[n])):
        s = side[n]
        prefer = [col_of[e[0]] for e in pred[n] if e[0] in col_of and side[e[0]] == s]
        options = prefer + list(range(len(cols[s]))) + [len(cols[s])]
        for c in options:
            if c == len(cols[s]):
                cols[s].append({'items': []})
            if free(cols[s][c], n):
                cols[s][c]['items'].append(n)
                col_of[n] = c
                break
    spine_w = max(W[n] for n in spine)
    x = {n: 0.0 for n in spine}
    cx = spine_w + gap
    for c in cols['RN'] + cols['RF']:
        for n in c['items']:
            x[n] = cx
        cx += max(W[n] for n in c['items']) + gap
    cx = 0.0
    for c in cols['L']:
        w = max(W[n] for n in c['items'])
        cx -= w + gap
        for n in c['items']:
            x[n] = cx
    shift = origin[0] - min(x.values())

    for n in ids:
        bx, by_ = snap(x[n] + shift, PITCH), round(y[n], 1)
        r = by[n].rect
        by[n].rect = [bx, by_, r[2], r[3]]
        lx = bx + r[2] + LABEL_GAP
        for cid in labels.get(n, []):
            cr = by[cid].rect
            by[cid].rect = [lx, by_ + 1, cr[2], cr[3]]
            lx += cr[2] + LABEL_GAP
    align_ports(P, set(spine), gap, chain_gap)
    # ---- free comments (title, notes): top row, right of the first spine object
    free_comments = [c for c in P.boxes if c.maxclass == 'comment' and c.id not in anchors]
    first = by[spine[0]]
    tx = first.rect[0] + W[spine[0]] + TITLE_GAP
    ty = first.rect[1]
    for c in sorted(free_comments, key=lambda c: (c.rect[1], c.rect[0])):
        cw, ch = c.rect[2], c.rect[3]
        while any(not (tx + cw + 10 < b.rect[0] or b.rect[0] + b.rect[2] + 10 < tx or
                       ty + ch + 10 < b.rect[1] or b.rect[1] + b.rect[3] + 10 < ty)
                  for b in nodes):
            tx = max(b.rect[0] + b.rect[2] for b in nodes) + TITLE_GAP
        c.rect = [tx, ty, cw, ch]
        ty += ch + 4
    return max(b.rect[1] + b.rect[3] for b in nodes) - min(b.rect[1] for b in nodes)


def align_ports(P, spine_ids, gap=GAP, chain_gap=CHAIN_GAP, max_w=240.0, max_stretch=3.0):
    """Make cords straight by moving or STRETCHING boxes so ports line up, the way the
    user does by hand: widen a [t f f f] until its right outlet sits above the display
    it feeds; widen a [+~] until its right inlet sits under the outlet feeding it."""
    from .lint import seg_hits_rect
    from .layout import comment_anchors
    nodes = [b for b in P.boxes if b.is_node]
    labels = {}
    for cid, (bid, _, _) in comment_anchors(P).items():
        labels.setdefault(bid, []).append(P.by_id[cid])
    linked = {(l.src[0], l.dst[0]) for l in P.lines} | {(l.dst[0], l.src[0]) for l in P.lines}
    incoming = {}
    for l in P.lines:
        incoming.setdefault(l.dst[0], set()).add(l.src[0])

    def foot(b, rect=None):
        x, y, w, h = rect or b.rect
        r = x + w
        for c in labels.get(b.id, []):
            r += 10 + c.rect[2]
        return [x, y, r - x, h]

    def collides(b, rect):
        f = foot(b, rect)
        for o in nodes:
            if o is b:
                continue
            g = chain_gap if (b.id, o.id) in linked else gap
            q = foot(o)
            if f[0] < q[0] + q[2] + g and q[0] < f[0] + f[2] + g and \
               f[1] < q[1] + q[3] + chain_gap and q[1] < f[1] + f[3] + chain_gap:
                return True
        return False

    def column_clear(x, y0, y1, skip):
        return not any(seg_hits_rect((x, y0), (x, y1), o.rect) for o in nodes if o.id not in skip)

    def set_rect(b, rect):
        dx = rect[0] + rect[2] - (b.rect[0] + b.rect[2])
        b.rect = rect
        for c in labels.get(b.id, []):
            cr = c.rect
            c.rect = [cr[0] + dx, cr[1], cr[2], cr[3]]

    lines = sorted(P.lines, key=lambda l: (P.by_id[l.src[0]].rect[1], -l.src[1]))
    for ln in lines:
        s, t = P.by_id[ln.src[0]], P.by_id[ln.dst[0]]
        if not (s.is_node and t.is_node) or s is t:
            continue
        k, j = ln.src[1], ln.dst[1]
        sx, sy = s.outlet_xy(k)
        tx, ty = t.inlet_xy(j)
        if abs(sx - tx) < 0.5 or ty <= sy or not column_clear(max(sx, tx), sy, ty, {s.id, t.id}):
            continue
        x, y, w, h = t.rect
        # 1. slide a side object (fed only by this source) under a SIDE outlet -
        #    never onto the spine's own column (outlet 0 of a spine object continues it)
        if t.id not in spine_ids and incoming.get(t.id) == {s.id} and j == 0 and ty - sy <= 70 \
                and not (s.id in spine_ids and k == 0):
            r = [x + (sx - tx), y, w, h]
            if not collides(t, r) and column_clear(sx, sy, ty, {s.id, t.id}):
                set_rect(t, r)
                continue
        # 2. stretch the source so outlet k reaches the inlet
        if k > 0 and s.nout > 1 and tx > sx:
            ws = 2 * 9.5 + (tx - s.rect[0] - 9.5) * (s.nout - 1) / k
            r = [s.rect[0], s.rect[1], ws, s.rect[3]]
            if ws <= min(max_w, max_stretch * s.rect[2]) and not collides(s, r) \
                    and column_clear(tx, sy, ty, {s.id, t.id}):
                set_rect(s, r)
                continue
        # 3. stretch the target so inlet j reaches the outlet
        if j > 0 and t.nin > 1 and sx > tx:
            wt = 2 * 9.5 + (sx - x - 9.5) * (t.nin - 1) / j
            r = [x, y, wt, h]
            if wt <= min(max_w, max_stretch * w) and not collides(t, r) \
                    and column_clear(sx, sy, ty, {s.id, t.id}):
                set_rect(t, r)
