"""Colour a Max patch like a circuit board: net colours by signal type, module
panels as silkscreen outlines, optional board background.

Usage:
  python3 style.py in.maxpat -o out.maxpat [--theme pcb|graphite|light|none]
                   [--nets] [--modules] [--no-background] [--no-recurse]

  --nets      colour cords by what flows through them (audio / mc / video / control)
  --modules   restyle panels as module outlines (translucent fill, 1px border, rounded)
  --theme     palette; 'none' leaves the patcher background alone
  --no-background  keep the patcher's own background colour even with a theme

Colours are written as explicit attributes (patchline "color", panel "bgcolor"/
"bordercolor", patcher "editing_bgcolor"/"locked_bgcolor"), so they survive in any
Max style.  Nothing here changes behaviour.
"""
import argparse, os, sys
from .maxpat import load, save, walk_patchers, net_kind


def hexc(h, a=1.0):
    h = h.lstrip('#')
    return [round(int(h[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)] + [a]


THEMES = {
    # green soldermask, gold audio traces, tinned control traces, white silkscreen
    'pcb': {'bg': '#1d3b2a', 'locked': '#16301f', 'audio': '#d9a441', 'mc': '#e07b39',
            'video': '#5ec4b6', 'control': '#c9d1cc', 'panel': '#ffffff', 'panel_a': 0.05,
            'border': '#e8efe9', 'border_a': 0.55, 'title': '#f4f7f4'},
    # neutral dark board, high-contrast nets
    'graphite': {'bg': '#2b2d31', 'locked': '#232529', 'audio': '#f2c14e', 'mc': '#f78154',
                 'video': '#4fb3bf', 'control': '#b8bcc6', 'panel': '#ffffff', 'panel_a': 0.04,
                 'border': '#9aa0aa', 'border_a': 0.6, 'title': '#e6e8eb'},
    # paper schematic
    'light': {'bg': '#e9e7e1', 'locked': '#f2f0ea', 'audio': '#b5651d', 'mc': '#c2410c',
              'video': '#0f766e', 'control': '#4b5563', 'panel': '#000000', 'panel_a': 0.035,
              'border': '#6b7280', 'border_a': 0.7, 'title': '#1f2937'},
}
THEMES['none'] = dict(THEMES['graphite'])


def apply(doc, theme='graphite', nets=True, modules=True, background=True, recurse=True):
    T = THEMES[theme]
    for P in walk_patchers(doc['patcher'], recurse):
        if background and theme != 'none':
            P.d['editing_bgcolor'] = hexc(T['bg'])
            P.d['locked_bgcolor'] = hexc(T['locked'])
        if nets:
            for ln in P.lines:
                kind = net_kind(P.by_id[ln.src[0]], ln.src[1])
                ln.d['color'] = hexc(T[kind])
        if modules:
            panels = [b for b in P.boxes if b.maxclass == 'panel']
            for p in panels:
                p.d.update({'bgcolor': hexc(T['panel'], T['panel_a']), 'border': 1,
                            'bordercolor': hexc(T['border'], T['border_a']), 'rounded': 6,
                            'background': 1})
                p.d.pop('bgfillcolor_type', None)
                px, py, pw, ph = p.rect
                for c in P.boxes:      # module titles: comment just inside the top edge
                    if c.maxclass == 'comment' and px <= c.rect[0] <= px + pw and py <= c.rect[1] <= py + 24:
                        c.d.update({'textcolor': hexc(T['title']), 'fontface': 1})
            for c in P.boxes:
                if c.maxclass == 'comment' and 'textcolor' not in c.d and background and theme != 'none':
                    c.d['textcolor'] = hexc(T['title'], 0.85)
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('-o', '--output')
    ap.add_argument('--theme', choices=list(THEMES), default='graphite')
    ap.add_argument('--nets', action='store_true')
    ap.add_argument('--modules', action='store_true')
    ap.add_argument('--no-background', action='store_true')
    ap.add_argument('--no-recurse', action='store_true')
    a = ap.parse_args()
    nets, modules = a.nets, a.modules
    if not (nets or modules):
        nets = modules = True
    doc = load(a.input)
    apply(doc, a.theme, nets, modules, not a.no_background, not a.no_recurse)
    save(doc, a.output or a.input)
    print(f"styled ({a.theme})")


if __name__ == '__main__':
    main()
