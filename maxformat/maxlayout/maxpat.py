"""Shared helpers for reading, measuring and writing Max .maxpat files.

Geometry facts (calibrated against ~31k segmented cords in Max 8.6's bundled patches):
  * A cord attaches to port i of a box at
        x = box.x + 9.5 + i * (box.w - 19) / (n - 1)     (n > 1)
        x = box.x + 9.5                                  (n == 1)
    outlets at y = box.y + box.h, inlets at y = box.y.
  * patchline "midpoints" is a flat [x1, y1, x2, y2, ...] list; Max draws
    outlet -> mp1 -> ... -> mpN -> inlet with straight segments.
"""
import json, math, os, re

PORT_INSET = 9.5          # distance from box edge to first/last port anchor
GRID = 15.0               # Max default grid
PITCH = 7.5               # "pin pitch": port spacing multiple that keeps every port alignable

HERE = os.path.dirname(os.path.abspath(__file__))
_OBJDB = None

NON_NODE = {'comment', 'panel', 'fpic', 'swatch_bg'}   # decoration, not part of the signal graph

# Modern Max 8/9 defaults that the mined data gets wrong (old help files).
UI_OVERRIDES = {
    'message': (2, 1, [''], 50.0, 22.0),
    'live.gain~': (2, 5, ['signal', 'signal', '', 'float', 'list'], 48.0, 136.0),
    'live.dial': (1, 2, ['', 'float'], 44.0, 48.0),
    'umenu': (1, 3, ['int', '', ''], 100.0, 22.0),
    'kslider': (2, 2, ['int', 'int'], 336.0, 53.0),
    'meter~': (1, 1, ['float'], 80.0, 13.0),
    'number~': (2, 2, ['signal', 'float'], 56.0, 22.0),
    'comment': (1, 0, [], 100.0, 20.0),
    'panel': (1, 0, [], 128.0, 128.0),
    'live.text': (1, 2, ['', ''], 44.0, 20.0),
    'live.toggle': (1, 1, [''], 15.0, 15.0),
    'live.slider': (1, 2, ['', 'float'], 39.0, 48.0),
    'live.numbox': (1, 2, ['', 'float'], 44.0, 15.0),
    'live.menu': (1, 3, ['', '', 'float'], 100.0, 15.0),
    'live.button': (1, 1, [''], 15.0, 15.0),
    'ezdac~': (2, 0, [], 45.0, 45.0),
    'ezadc~': (1, 2, ['signal', 'signal'], 45.0, 45.0),
    'scope~': (2, 0, [], 130.0, 130.0),
    'spectroscope~': (2, 1, [''], 300.0, 100.0),
    'multislider': (1, 2, ['', ''], 140.0, 70.0),
    'function': (2, 4, ['float', '', '', 'bang'], 200.0, 100.0),
    'toggle': (1, 1, ['int'], 24.0, 24.0),
    'button': (1, 1, ['bang'], 24.0, 24.0),
    'number': (1, 2, ['', 'bang'], 50.0, 22.0),
    'flonum': (1, 2, ['', 'bang'], 50.0, 22.0),
    'dial': (1, 1, ['float'], 40.0, 40.0),
    'slider': (1, 1, [''], 20.0, 140.0),
    'gain~': (2, 2, ['signal', ''], 22.0, 140.0),
    'inlet': (0, 1, [''], 30.0, 30.0),
    'outlet': (1, 0, [], 30.0, 30.0),
}


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _objdb():
    global _OBJDB
    if _OBJDB is None:
        p = os.path.join(HERE, 'data', 'objdb.json')
        _OBJDB = read_json(p) if os.path.exists(p) else {'newobj': {}, 'ui': {}}
    return _OBJDB


def positional_args(text):
    toks = str(text).split()[1:]
    out, skip = [], False
    for t in toks:
        if t.startswith('@'):
            skip = True
            continue
        if skip:
            skip = False
            continue
        out.append(t)
    return out


def guess_ports(text):
    """Best guess (numinlets, numoutlets, outlettype) for an object box's text.

    Variadic objects get explicit rules; everything else comes from the mined
    database (class + positional-arg count), then class default.  Always let an
    explicit value in a spec win over this guess.
    """
    toks = str(text).split()
    if not toks:
        return 1, 1, ['']
    cls, args = toks[0], positional_args(text)
    n = len(args)
    sig = cls.endswith('~')
    T = {'b': 'bang', 'bang': 'bang', 'i': 'int', 'int': 'int', 'f': 'float', 'float': 'float',
         'l': 'list', 'list': 'list', 's': '', 'symbol': '', 'a': '', 'anything': ''}
    if cls in ('t', 'trigger'):
        a = args or ['b', 'b']
        return 1, len(a), [T.get(x, '') for x in a]
    if cls in ('pack', 'pak'):
        return max(n, 2), 1, ['']
    if cls == 'unpack':
        k = max(n, 2)
        return 1, k, [''] * k
    if cls in ('route', 'routepass', 'select', 'sel'):
        k = max(n, 1)
        # Max 7+: one inlet per argument (to reset it) plus the left input
        return (2 if k == 1 else k + 1), k + 1, (['bang'] * k + [''] if cls in ('select', 'sel') else [''] * (k + 1))
    if cls == 'gate':
        k = int(args[0]) if args and args[0].isdigit() else 1
        return 2, k, [''] * k
    if cls == 'switch':
        k = int(args[0]) if args and args[0].isdigit() else 2
        return k + 1, 1, ['']
    if cls in ('gate~',):
        k = int(args[0]) if args and args[0].isdigit() else 1
        return 2, k, ['signal'] * k
    if cls in ('selector~',):
        k = int(args[0]) if args and args[0].isdigit() else 1
        return k + 1, 1, ['signal']
    if cls in ('dac~', 'ezdac~'):
        k = n if n else 2
        return k, 0, []
    if cls == 'adc~':
        k = n if n else 2
        return 1, k, ['signal'] * k
    if cls in ('mc.dac~',):
        return 1, 0, []
    if cls in ('send', 's', 'send~', 'forward'):
        return (2 if cls == 'send~' else 1), 0, []
    if cls in ('receive', 'r'):
        return 0 if n else 1, 1, ['']
    if cls == 'receive~':
        return 1, 1, ['signal']
    if cls in ('+', '-', '*', '/', '%', '!-', '!/', '==', '!=', '>', '<', '>=', '<=', '&&', '||',
               '+~', '-~', '*~', '/~', '!-~', '!/~', '%~', '==~', '>~', '<~', 'pow~', 'atan2~'):
        return 2, 1, ['signal' if sig else ('int' if not args or '.' not in args[0] else 'float')]
    db = _objdb()['newobj'].get(cls)
    if db:
        key = str(min(n, 12))
        if key in db:
            return db[key][0], db[key][1], db[key][2]
        # nearest arg count
        best = min(db, key=lambda k: abs(int(k) - n))
        return db[best][0], db[best][1], db[best][2]
    if sig:
        return 2, 1, ['signal']
    return 1, 1, ['']


def ui_defaults(maxclass):
    """(numinlets, numoutlets, outlettype, w, h) for a UI maxclass."""
    if maxclass in UI_OVERRIDES:
        return UI_OVERRIDES[maxclass]
    d = _objdb()['ui'].get(maxclass)
    if d:
        return tuple(d)
    return 1, 1, [''], 50.0, 22.0


def text_width(text, fontsize=12.0):
    """Approximate rendered width of an object/message box for Arial at fontsize."""
    s = str(text)
    return math.ceil((5.6 * len(s) + 14) * fontsize / 12.0)


def pitch_width(min_w, nin, nout, pitch=PITCH, slack=1.6):
    """Smallest width >= min_w whose port spacing is a multiple of `pitch` for both
    inlets and outlets, so every port of every box can sit on the same column grid.
    Falls back to the side with more ports if the joint constraint would make the
    box absurdly wide."""
    def step_for(ns):
        m = 1
        for k in ns:
            if k > 1:
                m = m * (k - 1) // math.gcd(m, k - 1)
        return m * pitch
    cands = [[nin, nout], [max(nin, nout)], []]
    for c in cands:
        step = step_for(c)
        if step == pitch and not any(k > 1 for k in c):
            return float(math.ceil(min_w))
        w = 2 * PORT_INSET + step * math.ceil(max(0.0, min_w - 2 * PORT_INSET) / step)
        if w <= min_w * slack + 2 * pitch or not c:
            return float(w)
    return float(math.ceil(min_w))


class Box:
    __slots__ = ('d', 'patcher')

    def __init__(self, d, patcher=None):
        self.d = d
        self.patcher = patcher

    id = property(lambda s: s.d['id'])
    maxclass = property(lambda s: s.d.get('maxclass', 'newobj'))
    text = property(lambda s: s.d.get('text', ''))
    nin = property(lambda s: int(s.d.get('numinlets', 0)))
    nout = property(lambda s: int(s.d.get('numoutlets', 0)))

    @property
    def rect(self):
        return self.d.get('patching_rect', [0, 0, 0, 0])

    @rect.setter
    def rect(self, r):
        self.d['patching_rect'] = [float(v) for v in r]

    @property
    def is_node(self):
        return self.maxclass not in NON_NODE

    @property
    def is_background(self):
        return bool(self.d.get('background')) or self.maxclass == 'panel'

    def port_x(self, i, n, rect=None):
        x, y, w, h = rect or self.rect
        if n <= 1:
            return x + PORT_INSET
        return x + PORT_INSET + i * (w - 2 * PORT_INSET) / (n - 1)

    def outlet_xy(self, i, rect=None):
        x, y, w, h = rect or self.rect
        return self.port_x(i, self.nout, rect), y + h

    def inlet_xy(self, i, rect=None):
        x, y, w, h = rect or self.rect
        return self.port_x(i, self.nin, rect), y

    def outlet_type(self, i):
        ot = self.d.get('outlettype') or []
        return ot[i] if i < len(ot) else ''


class Line:
    __slots__ = ('d',)

    def __init__(self, d):
        self.d = d

    src = property(lambda s: (s.d['source'][0], int(s.d['source'][1])))
    dst = property(lambda s: (s.d['destination'][0], int(s.d['destination'][1])))

    @property
    def midpoints(self):
        m = self.d.get('midpoints') or []
        return [(m[i], m[i + 1]) for i in range(0, len(m) - 1, 2)]

    @midpoints.setter
    def midpoints(self, pts):
        if pts:
            self.d['midpoints'] = [round(float(v), 2) for p in pts for v in p]
        else:
            self.d.pop('midpoints', None)


class Patcher:
    def __init__(self, d, path='root'):
        self.d = d
        self.path = path
        self.boxes = [Box(b['box'], self) for b in d.get('boxes', [])]
        self.by_id = {b.id: b for b in self.boxes}
        self.lines = [Line(l['patchline']) for l in d.get('lines', [])
                      if l['patchline']['source'][0] in self.by_id
                      and l['patchline']['destination'][0] in self.by_id]

    @property
    def grid(self):
        g = self.d.get('gridsize', [GRID, GRID])
        return float(g[0]), float(g[1])

    def polyline(self, line):
        """Full list of points from outlet anchor through midpoints to inlet anchor."""
        s = self.by_id[line.src[0]]
        t = self.by_id[line.dst[0]]
        return [s.outlet_xy(line.src[1])] + line.midpoints + [t.inlet_xy(line.dst[1])]

    def subpatchers(self):
        """Yield (box, Patcher) for embedded subpatchers (p, poly~ inline, bpatcher embed...)."""
        for b in self.boxes:
            sp = b.d.get('patcher')
            if isinstance(sp, dict) and 'boxes' in sp:
                yield b, Patcher(sp, f"{self.path}/{b.id}:{b.text or b.maxclass}")


def walk_patchers(root_patcher_dict, recurse=True):
    p = Patcher(root_patcher_dict)
    yield p
    if recurse:
        stack = list(p.subpatchers())
        while stack:
            _, sp = stack.pop()
            yield sp
            stack.extend(sp.subpatchers())


def load(path):
    return read_json(path)


def save(doc, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent='\t', ensure_ascii=False)
        f.write('\n')


def net_kind(box, outlet):
    """Classify a cord by what flows through it: audio, mc, video, or control."""
    t = box.outlet_type(outlet)
    if t == 'signal':
        return 'audio'
    if t == 'multichannelsignal':
        return 'mc'
    if t in ('jit_matrix', 'jit_gl_texture'):
        return 'video'
    return 'control'


def snap(v, step):
    return round(round(v / step) * step, 2)


def snap_up(v, step):
    """Round up to the grid - for gaps that are minimums and must never shrink."""
    return round(math.ceil(v / step - 1e-9) * step, 2)


SHORT_HOP = (15.0, 200.0, 60.0)   # min |dx|, max |dx|, max dy (px)


def is_short_hop(a, b):
    """A cord short enough to draw as a plain diagonal (house style, from the user's
    own patch): one row down, a little to the side. Tiny offsets don't count - a 2px
    slant looks like a mistake, not a choice."""
    dx, dy = abs(b[0] - a[0]), b[1] - a[1]
    return SHORT_HOP[0] <= dx <= SHORT_HOP[1] and 0 < dy <= SHORT_HOP[2]


def comment_height(text, w, fontsize=12.0):
    """Height Max gives a comment once its text wraps to width w."""
    per = max(1, int((w - 8) / (5.6 * fontsize / 12.0)))
    lines = 0
    for para in str(text).split('\n'):
        words, cur, n = para.split(' '), 0, 1
        for wd in words:
            if cur and cur + 1 + len(wd) > per:
                n += 1
                cur = len(wd)
            else:
                cur = cur + (1 if cur else 0) + len(wd)
        lines += n
    return round(lines * fontsize * 1.2 + 6, 1)


def fix_comment_heights(P):
    for b in P.boxes:
        if b.maxclass == 'comment' and b.d.get('text') is not None:
            x, y, w, h = b.rect
            b.rect = [x, y, w, max(h, comment_height(b.text, w, float(b.d.get('fontsize', 12))))]


ID_RE = re.compile(r'^obj-(\d+)$')


def next_id(patcher_dict):
    """A new box id for this patcher. Max only uses ids of the form obj-<number>;
    anything else (e.g. 'obj-t1') makes its cords fail to load."""
    nums = [int(m.group(1)) for b in patcher_dict.get('boxes', [])
            for m in [ID_RE.match(str(b['box'].get('id', '')))] if m]
    return f"obj-{max(nums, default=0) + 1}"


IO_CLASSES = {'inlet': 'in', 'outlet': 'out'}


def io_order(P):
    """Inlet/outlet objects of a subpatcher in left-to-right order. This order IS the
    parent box's port numbering, so layout must never change it."""
    ins, outs = [], []
    for b in P.boxes:
        # Max saves these as their own maxclass ("inlet"/"outlet"), not as object text
        kind = IO_CLASSES.get(b.maxclass)
        if kind:
            (ins if kind == 'in' else outs).append(b)
    key = lambda b: (b.rect[0], b.rect[1])
    return [b.id for b in sorted(ins, key=key)], [b.id for b in sorted(outs, key=key)]


def restore_io_order(P, before):
    """Swap x positions so inlet/outlet objects regain their original left-to-right order."""
    fixed = 0
    for want, now in zip(before, io_order(P)):
        if want == now:
            continue
        xs = sorted(P.by_id[i].rect[0] for i in want)
        for i, x in zip(want, xs):
            r = P.by_id[i].rect
            if r[0] != x:
                P.by_id[i].rect = [x, r[1], r[2], r[3]]
                fixed += 1
    return fixed


# --------------------------------------------------------------------------- object knowledge
# What objects are and what their inlets/outlets carry, mined from Max's reference pages
# and from the cords in Cycling '74's own patches (see mine_refdb.py).
_REFDB = None
ALIASES = {'t': 'trigger', 'sel': 'select', 'b': 'bangbang', 'i': 'int', 'f': 'float',
           's': 'send', 'r': 'receive', 'v': 'value', 'del': 'delay', 'p': 'patcher',
           'pak': 'pak', 'ftom': 'ftom', 'mtof': 'mtof'}
# objects whose inlets depend on what's inside them - can't be judged from outside
OPAQUE = {'patcher', 'poly~', 'bpatcher', 'pfft~', 'gen~', 'mc.gen~', 'vst~', 'amxd~',
          'mcs.poly~', 'mc.poly~', 'js', 'jsui', 'node.script', 'outlet', 'v8', 'v8ui'}
# signal-only inlets that also take on/off style messages
TAKES_COMMANDS = {'dac~', 'adc~', 'ezdac~', 'ezadc~', 'mc.dac~', 'mc.adc~'}


def refdb():
    global _REFDB
    if _REFDB is None:
        p = os.path.join(HERE, 'data', 'refdb.json')
        _REFDB = read_json(p) if os.path.exists(p) else {}
    return _REFDB


def object_class(box_dict):
    """The Max class name of a box: 'cycle~' for [cycle~ 440], 'trigger' for [t b b]."""
    mc = box_dict.get('maxclass', 'newobj')
    if mc != 'newobj':
        return mc
    toks = str(box_dict.get('text', '')).split()
    cls = toks[0] if toks else ''
    return ALIASES.get(cls, cls)


def _port_type(entry, key, i):
    types = entry.get(key) or []
    if not types:
        return '?'
    return types[i] if i < len(types) else types[-1]


def inlet_accepts_signal(box_dict, i):
    """True / False / None (unknown) - can a signal cord plug into inlet i?"""
    cls = object_class(box_dict)
    if cls in OPAQUE:
        return None
    e = refdb().get(cls)
    if e is None:
        return None                      # external or abstraction: can't know
    if i in e.get('sig_in', []):
        return True
    t = _port_type(e, 'in', i)
    if 'signal' in t or 'multi' in t or t == 'auto':
        return True
    if cls.endswith('~') and t == '?':
        return None
    return False


def inlet_signal_only(box_dict, i):
    """True if inlet i only does something with signals (a number there is ignored)."""
    cls = object_class(box_dict)
    if cls in OPAQUE or cls in TAKES_COMMANDS:
        return False
    e = refdb().get(cls)
    return bool(e) and _port_type(e, 'in', i) in ('signal', 'multi-channel signal')


def is_signal_outlet(box_dict, i):
    ot = box_dict.get('outlettype') or []
    return i < len(ot) and ot[i] in ('signal', 'multichannelsignal')


def check_cord(src_box, outlet, dst_box, inlet):
    """(severity, message) for a cord Max would reject or that won't do anything, else None."""
    sname = str(src_box.get('text') or src_box.get('maxclass'))
    dname = str(dst_box.get('text') or dst_box.get('maxclass'))
    if is_signal_outlet(src_box, outlet):
        if inlet_accepts_signal(dst_box, inlet) is False:
            return ('error', f"[{sname}] outlet {outlet} is audio, but [{dname}] inlet {inlet} only takes "
                             f"messages - Max won't connect it. Use snapshot~ / peakamp~ / edge~ to "
                             f"turn audio into numbers, or feed an audio (~) object instead.")
    elif src_box.get('maxclass') != 'message' and inlet_signal_only(dst_box, inlet):
        return ('warning', f"[{sname}] outlet {outlet} sends messages, but [{dname}] inlet {inlet} only "
                           f"uses audio - numbers there are ignored. Put sig~ (constant) or line~ "
                           f"(ramp) in between.")
    return None


ARITH = {'+', '-', '*', '/', '%', '!-', '!/', 'pow', 'abs', 'clip', 'minimum', 'maximum'}
COMPARE = {'==', '!=', '>', '<', '>=', '<=', '&&', '||', '&', '|', '<<', '>>'}
FLOAT_OUT = {'mtof', 'ftom', 'dbtoa', 'atodb', 'scale', 'line', 'flonum', 'snapshot~', 'peakamp~',
             'avg~', 'sqrt', 'cos', 'sin', 'tan', 'log', 'exp', 'float', 'live.dial', 'live.slider',
             'live.numbox', 'zmap', 'rtin', 'translate'}
INT_OUT = {'random', 'counter', 'notein', 'ctlin', 'number', 'int', 'drunk', 'urn', 'toggle',
           'slider', 'kslider', 'umenu', 'change', 'stripnote', 'makenote'}
TRIGGER_TYPES = {'bang': 'b', 'int': 'i', 'float': 'f', 'list': 'l', 'symbol': 's'}


def outlet_datatype(box_dict, i):
    """'bang' / 'int' / 'float' / 'list' / 'symbol' / None (unknown) for a control outlet."""
    ot = box_dict.get('outlettype') or []
    t = ot[i] if i < len(ot) else ''
    if t in TRIGGER_TYPES:
        return t
    cls = object_class(box_dict)
    if cls in ARITH:
        args = positional_args(box_dict.get('text', ''))
        return 'float' if any('.' in a for a in args) else 'int'
    if cls in COMPARE:
        return 'int'
    e = refdb().get(cls) or {}
    rt = _port_type(e, 'out', i) if e.get('out') else '?'
    if rt in TRIGGER_TYPES:
        return rt
    if cls in FLOAT_OUT:
        return 'float'
    if cls in INT_OUT:
        return 'int'
    return None


def trigger_letter(box_dict, i):
    """The [t] argument that passes this outlet's data on unchanged (b i f l s), or
    None if the type is unknown. Max has no 'pass anything' type: guessing l would turn
    a bang into the list 0, so callers must not guess."""
    return TRIGGER_TYPES.get(outlet_datatype(box_dict, i))


def check_trigger(box_dict):
    """Problems with a [t]/[trigger]'s arguments: its types are only b i f l s
    (anything else is sent out as a constant)."""
    if object_class(box_dict) != 'trigger':
        return None
    for a in positional_args(box_dict.get('text', '')):
        if a == 'a':
            return ('error', f"[{box_dict.get('text')}]: 'a' is not a trigger type - Max's [t] types are "
                             f"b (bang), i (int), f (float), l (list), s (symbol). Match the type to what "
                             f"comes in, e.g. [t f f] after [mtof].")
        if len(a) == 1 and a.isalpha() and a not in 'bifls':
            return ('warning', f"[{box_dict.get('text')}]: '{a}' isn't a trigger type, so it is sent out as "
                               f"the constant symbol '{a}'. Types are b i f l s.")
    return None


def describe(name):
    """Human-readable summary of a Max object, its inlets and outlets."""
    cls = ALIASES.get(name, name)
    e = refdb().get(cls)
    if not e:
        return f"{name}: not in Max's reference (an external or abstraction?)"
    lines = [f"[{cls}]" + (f"  ({name} is an alias)" if cls != name else '') + f"  {e['digest']}"]
    for kind, key, dkey in (('inlet', 'in', 'in_digest'), ('outlet', 'out', 'out_digest')):
        for i, t in enumerate(e.get(key, [])):
            d = (e.get(dkey) or [''] * (i + 1))[i] if i < len(e.get(dkey, [])) else ''
            sig = ''
            if kind == 'inlet' and i in e.get('sig_in', []) and 'signal' not in t:
                sig = ' (takes audio)'
            shown = {'?': 'messages', 'auto': 'signal/float'}.get(t, t)
            lines.append(f"  {kind} {i}: {shown}{sig} - {d}")
    nin, nout, ot = guess_ports(name)
    lines.append(f"  in a patch: {nin} inlet(s), {nout} outlet(s) {ot}")
    return '\n'.join(lines)
