"""Orthogonal (90-degree) auto-router for Max patch cords.

Rewrites each patchline's "midpoints" so cords travel only horizontally and
vertically, leave outlets heading down, enter inlets heading down, avoid boxes,
never run on top of an unrelated cord, and cross other cords as rarely as possible.

Fan-in cords (several sources into the same inlet) merge into one trace that drops
into the inlet.  Fan-out cords (one outlet into several inlets) stay separate: they
share only the short unavoidable lead out of the outlet, then each takes its own path.

Box positions are never changed - run layout.py first if the placement is messy.

Usage:
  python3 route.py in.maxpat -o out.maxpat [--clearance 6] [--no-share]
                   [--keep-orthogonal] [--no-recurse] [--passes 2] [--clear] [-v]

  --keep-orthogonal  leave cords that already have clean right-angle midpoints alone
  --clear            strip every midpoint (back to Max's straight cords)
"""
import argparse, bisect, heapq, os, sys
from .maxpat import load, save, walk_patchers, PITCH, is_short_hop

HARD = 1.5     # px around a box that cords may never enter
BEND = 22.0    # cost of a 90-degree turn (px-equivalents)
CROSS = 45.0  # cost of crossing an unrelated cord: any detour shorter than ~600px wins
NEAR = 60.0    # cost of running parallel within 4px of another cord (merge into it or keep clear)
HALO = 1.5     # extra length multiplier inside another box's clearance halo
SHARE = 0.15   # length multiplier when riding a compatible cord's existing trace
INF = float('inf')
DIRS = {'R': (1, 0), 'L': (-1, 0), 'D': (0, 1), 'U': (0, -1)}
OPP = {'R': 'L', 'L': 'R', 'U': 'D', 'D': 'U'}


def compatible(a, b):
    """Cord keys are (src, dst) tuples.  Cords into the same inlet may share a trace."""
    return a[1] == b[1]

LEAD_LANES = 2   # fan-out cords may share this many lanes below their outlet, then split


def _coords(values, priority, min_gap=3.0):
    """Sorted coordinate list: every priority value exactly, plus other values that
    are at least min_gap away from anything already kept."""
    keep = sorted(set(round(v, 2) for v in priority))
    out = list(keep)
    for v in sorted(set(round(v, 2) for v in values)):
        i = bisect.bisect_left(out, v)
        if (i < len(out) and abs(out[i] - v) < min_gap) or (i > 0 and abs(out[i - 1] - v) < min_gap):
            continue
        out.insert(i, v)
    return out


class Router:
    def __init__(self, P, clearance=6.0, share=True):
        self.P, self.c, self.share = P, clearance, share
        self.cross_cost = CROSS
        self.hard, self.soft = [], []
        for b in P.boxes:
            if b.is_background or b.d.get('hidden'):
                continue
            x, y, w, h = b.rect
            if w <= 0 or h <= 0:
                continue
            self.hard.append((b.id, x - HARD, y - HARD, x + w + HARD, y + h + HARD))
            self.soft.append((b.id, x - self.c, y - self.c, x + w + self.c, y + h + self.c))
        self.ok = bool(self.hard)
        if not self.ok:
            return
        port_x, stub_y, xs, ys = [], [], [], []
        for ln in P.lines:
            (sx, sy), (tx, ty) = self.ends(ln)
            so, si = self.stubs(sx, sy, tx, ty)
            port_x += [sx, tx]
            stub_y += [sy + so, ty - si, sy, ty]     # lead ends AND the port anchors themselves
        for (_, l, t, r, b), (_, L, T, R, B) in zip(self.hard, self.soft):
            xs += [l, r, (l + r) / 2, L, R]
            ys += [t, b, (t + b) / 2, T, B]
        minx = min(o[1] for o in self.soft) - 8 * PITCH
        maxx = max(o[3] for o in self.soft) + 8 * PITCH
        miny = min(o[2] for o in self.soft) - 6 * PITCH
        maxy = max(o[4] for o in self.soft) + 6 * PITCH
        xs += [minx + i * PITCH for i in range(int((maxx - minx) / PITCH) + 1)]
        ys += [miny + i * PITCH for i in range(int((maxy - miny) / PITCH) + 1)]
        self.xs = _coords(xs, port_x)
        self.ys = _coords(ys, stub_y)
        self.xi = {x: i for i, x in enumerate(self.xs)}
        self.yi = {y: i for i, y in enumerate(self.ys)}
        self.blocked = self._cells(self.hard)
        self.halo = self._cells(self.soft) - self.blocked
        self.lead = {}    # outlet -> (column, first row, last row) shared by its fan-out
        self.edge = {}    # (nodeA, nodeB) -> set(cordkey)
        self.node = {}    # node -> {cordkey: set('h','v','turn')}
        self.paths = {}   # line idx -> (cordkey, edges, nodes)

    def _cells(self, rects):
        out = set()
        for _, l, t, r, b in rects:
            i0, i1 = bisect.bisect_right(self.xs, l + 1e-6), bisect.bisect_left(self.xs, r - 1e-6)
            j0, j1 = bisect.bisect_right(self.ys, t + 1e-6), bisect.bisect_left(self.ys, b - 1e-6)
            for i in range(i0, i1):
                for j in range(j0, j1):
                    out.add((i, j))
        return out

    def ends(self, ln):
        s, t = self.P.by_id[ln.src[0]], self.P.by_id[ln.dst[0]]
        return s.outlet_xy(ln.src[1]), t.inlet_xy(ln.dst[1])

    def stubs(self, sx, sy, tx, ty):
        """Lengths of the vertical leads out of the outlet and into the inlet.  Normally
        the clearance; shorter when another box sits just below the outlet / just
        above the inlet, so the lead ends in the free gap between them."""
        def room(x, y, downward):
            best = INF
            for _, l, t, r, b in self.hard:
                if l < x < r:
                    d = (t - y) if downward else (y - b)
                    if d > 0:
                        best = min(best, d)
            return best
        a = room(sx, sy, True)
        b = room(tx, ty, False)
        s_out = self.c if a >= 2 * self.c else max(1.0, round(a / 2, 2))
        s_in = self.c if b >= 2 * self.c else max(1.0, round(b / 2, 2))
        return s_out, s_in

    # ---- occupancy -------------------------------------------------------
    @staticmethod
    def _ek(a, b):
        return (a, b) if a <= b else (b, a)

    def register(self, idx, key, nodes):
        edges = [self._ek(a, b) for a, b in zip(nodes, nodes[1:])]
        for e in edges:
            self.edge.setdefault(e, set()).add(key)
        for n, nd in enumerate(nodes):
            tags = self.node.setdefault(nd, {}).setdefault(key, set())
            prv = nodes[n - 1] if n else None
            nxt = nodes[n + 1] if n + 1 < len(nodes) else None
            dirs = {('h' if o[1] == nd[1] else 'v') for o in (prv, nxt) if o is not None}
            if prv is None or nxt is None or len(dirs) > 1:
                tags.add('turn')
            tags |= dirs
        self.paths[idx] = (key, edges, nodes)

    def unregister(self, idx):
        if idx not in self.paths:
            return
        key, edges, nodes = self.paths.pop(idx)
        for e in edges:
            self.edge.get(e, set()).discard(key)
        for nd in nodes:
            self.node.get(nd, {}).pop(key, None)

    def _on_lead(self, src, nodes):
        lead = self.lead.get(src)
        return lead is not None and all(n[0] == lead[0] and lead[1] <= n[1] <= lead[2] for n in nodes)

    def _foreign(self, keys, key, nodes=()):
        """Cords that may NOT share these grid nodes with `key`."""
        return [k for k in keys if not (compatible(k, key) or
                                        (k[0] == key[0] and self._on_lead(key[0], nodes)))]

    def _near(self, a, b, key):
        (i, j), (i2, j2) = a, b
        pen = 0.0
        if j == j2:
            for jj in (j - 1, j + 1):
                if 0 <= jj < len(self.ys) and abs(self.ys[jj] - self.ys[j]) < 4.0:
                    if self.edge.get(self._ek((i, jj), (i2, jj)), set()) - {key}:
                        pen += NEAR
        else:
            for ii in (i - 1, i + 1):
                if 0 <= ii < len(self.xs) and abs(self.xs[ii] - self.xs[i]) < 4.0:
                    if self.edge.get(self._ek((ii, j), (ii, j2)), set()) - {key}:
                        pen += NEAR
        return pen

    # ---- search ----------------------------------------------------------
    def search(self, start, goal, key, window=None):
        """A* over (node, heading).  The path starts heading Down (out of the outlet's
        stub) and must finish heading Down (into the inlet's stub)."""
        i0, j0, i1, j1 = window or (0, 0, len(self.xs) - 1, len(self.ys) - 1)
        gx, gy = self.xs[goal[0]], self.ys[goal[1]]
        h = lambda n: abs(self.xs[n[0]] - gx) + abs(self.ys[n[1]] - gy)
        g = {(start, 'D'): 0.0}
        prev = {}
        pq = [(h(start), 0.0, start, 'D')]
        while pq:
            _, cost, n, d = heapq.heappop(pq)
            if cost > g.get((n, d), INF):
                continue
            if d == 'END':
                return self._unwind(prev, prev[(n, d)])
            if n == goal:
                if d == 'D':
                    return self._unwind(prev, (n, d))
                c2 = cost + BEND        # sideways arrival: one more bend into the stub
                if c2 < g.get((n, 'END'), INF):
                    g[(n, 'END')] = c2
                    prev[(n, 'END')] = (n, d)
                    heapq.heappush(pq, (c2, c2, n, 'END'))
                continue
            here = self.node.get(n, {})
            foreign_here = {k: here[k] for k in self._foreign(here, key, (n,))}
            for nd, (dx, dy) in DIRS.items():
                if nd == OPP[d]:
                    continue
                m = (n[0] + dx, n[1] + dy)
                if not (i0 <= m[0] <= i1 and j0 <= m[1] <= j1):
                    continue
                if m in self.blocked and m != goal:
                    continue
                e = self._ek(n, m)
                occ = self.edge.get(e, ())
                if self._foreign(occ, key, e):
                    continue
                step = 0.0
                if nd != d:
                    if foreign_here and n != start:
                        continue        # a corner on an unrelated cord reads as a junction
                    step += BEND
                elif foreign_here and n != start:
                    cross = 'v' if nd in 'LR' else 'h'
                    if any(tags != {cross} for tags in foreign_here.values()):
                        continue
                    step += self.cross_cost * len(foreign_here)
                length = abs(self.xs[m[0]] - self.xs[n[0]]) + abs(self.ys[m[1]] - self.ys[n[1]])
                shared = self.share and occ
                step += length * (SHARE if shared else 1.0)
                if not shared:
                    step += self._near(n, m, key)
                    if m in self.halo and m != goal:
                        step += length * HALO + 4
                nc = cost + step
                if nc < g.get((m, nd), INF):
                    g[(m, nd)] = nc
                    prev[(m, nd)] = (n, d)
                    heapq.heappush(pq, (nc + h(m), nc, m, nd))
        return None

    @staticmethod
    def _unwind(prev, state):
        out = [state[0]]
        while state in prev:
            state = prev[state]
            if state[0] != out[-1]:
                out.append(state[0])
        return out[::-1]

    def route_line(self, idx, ln):
        (sx, sy), (tx, ty) = self.ends(ln)
        key = (tuple(ln.src), tuple(ln.dst))
        so, si = self.stubs(sx, sy, tx, ty)
        start = (self.xi[round(sx, 2)], self.yi[round(sy + so, 2)])
        goal = (self.xi[round(tx, 2)], self.yi[round(ty - si, 2)])
        # The few px between a port and the lead's end are part of the cord too: record
        # them, or other cords will slip through them and cross unseen.
        a_s, a_t = self.yi[round(sy, 2)], self.yi[round(ty, 2)]
        lead_out = [(start[0], j) for j in range(a_s, start[1])]
        lead_in = [(goal[0], j) for j in range(goal[1] + 1, a_t + 1)]
        self.lead.setdefault(key[0], (start[0], a_s, min(start[1] + LEAD_LANES, len(self.ys) - 1)))
        # Already aligned and nothing in the way: a plain straight cord.
        if start[0] == goal[0] and goal[1] >= start[1]:
            nodes = [(start[0], j) for j in range(start[1], goal[1] + 1)]
            if not any(n in self.blocked for n in nodes[1:-1]) and \
               not any(self._foreign(self.edge.get(self._ek(a, b), ()), key, (a, b)) for a, b in zip(nodes, nodes[1:])):
                self.register(idx, key, lead_out + nodes + lead_in)
                return []
        pad = 14
        win = (max(0, min(start[0], goal[0]) - pad), max(0, min(start[1], goal[1]) - pad),
               min(len(self.xs) - 1, max(start[0], goal[0]) + pad),
               min(len(self.ys) - 1, max(start[1], goal[1]) + pad))
        was_blocked = start in self.blocked
        self.blocked.discard(start)
        path = self.search(start, goal, key, win) or self.search(start, goal, key)
        if was_blocked:
            self.blocked.add(start)
        if path is None:
            return None
        self.register(idx, key, lead_out + path + lead_in)
        return corners([(sx, sy)] + [(self.xs[i], self.ys[j]) for i, j in path] + [(tx, ty)])


def corners(pts):
    """Interior corner points of a polyline - exactly what Max stores as midpoints."""
    clean = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - clean[-1][0]) > 1e-6 or abs(p[1] - clean[-1][1]) > 1e-6:
            clean.append(p)
    out = []
    for a, b, c in zip(clean, clean[1:], clean[2:]):
        straight = (abs(a[0] - b[0]) < 1e-6 and abs(b[0] - c[0]) < 1e-6) or \
                   (abs(a[1] - b[1]) < 1e-6 and abs(b[1] - c[1]) < 1e-6)
        if not straight:
            out.append(b)
    return out


def _orthogonal(P, ln):
    pts = P.polyline(ln)
    return all(abs(a[0] - b[0]) <= 0.5 or abs(a[1] - b[1]) <= 0.5 for a, b in zip(pts, pts[1:]))


def diagonal_hops(P, idxs):
    """Draw short hops (one row down, a bit sideways) as plain diagonals, like a human
    would - unless the straight line would pass through a box."""
    from .lint import seg_hits_rect
    for idx in idxs:
        ln = P.lines[idx]
        s, t = P.by_id[ln.src[0]], P.by_id[ln.dst[0]]
        a, b = s.outlet_xy(ln.src[1]), t.inlet_xy(ln.dst[1])
        if not is_short_hop(a, b):
            continue
        if any(seg_hits_rect(a, b, o.rect) for o in P.boxes
               if o.id not in (s.id, t.id) and not o.is_background):
            continue
        ln.midpoints = []


def route_patcher(P, clearance=6.0, share=True, keep_orthogonal=False, passes=4, verbose=False,
                  short_hops=True):
    R = Router(P, clearance, share)
    if not R.ok:
        return 0, 0
    order = []
    for idx, ln in enumerate(P.lines):
        (sx, sy), (tx, ty) = R.ends(ln)
        straight = abs(sx - tx) < 0.01 and ty > sy
        order.append((0 if straight else 1, abs(sx - tx) + abs(ty - sy), idx))
    order.sort()
    todo = [i for _, _, i in order
            if not (keep_orthogonal and P.lines[i].midpoints and _orthogonal(P, P.lines[i]))]
    failed = set()
    # Negotiated routing: every pass re-routes each cord against all the others with a
    # higher crossing penalty, so cords trade lanes until crossings are squeezed out.
    for p in range(max(1, passes)):
        R.cross_cost = CROSS * (2 ** p)
        for idx in todo:
            ln = P.lines[idx]
            R.unregister(idx)
            mps = R.route_line(idx, ln)
            if mps is None:
                failed.add(idx)
                continue
            failed.discard(idx)
            ln.midpoints = mps
    if short_hops:
        diagonal_hops(P, todo)
    if verbose:
        for idx in sorted(failed):
            ln = P.lines[idx]
            print(f"  no route for {ln.src}->{ln.dst} in {P.path}", file=sys.stderr)
    return len(todo) - len(failed), len(failed)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('-o', '--output')
    ap.add_argument('--clearance', type=float, default=6.0)
    ap.add_argument('--no-share', action='store_true', help='never let fan-in cords merge into one trace')
    ap.add_argument('--keep-orthogonal', action='store_true')
    ap.add_argument('--no-recurse', action='store_true', help='skip embedded subpatchers')
    ap.add_argument('--passes', type=int, default=2)
    ap.add_argument('--clear', action='store_true')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    doc = load(a.input)
    ok = fail = 0
    for P in walk_patchers(doc['patcher'], recurse=not a.no_recurse):
        if a.clear:
            for ln in P.lines:
                ln.midpoints = []
            continue
        o, f = route_patcher(P, a.clearance, not a.no_share, a.keep_orthogonal, a.passes, a.verbose)
        ok, fail = ok + o, fail + f
    save(doc, a.output or a.input)
    print('cleared all midpoints' if a.clear else
          f"routed {ok} cords" + (f", {fail} unroutable (left as they were)" if fail else ''))


if __name__ == '__main__':
    main()
