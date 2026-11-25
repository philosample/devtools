# The .maxpat format (Max 8/9) - what matters for layout

A .maxpat (and .maxhelp) file is JSON. Max writes it tab-indented with `" : "`
separators, but reads any valid JSON, so `json.dump(..., indent='\t')` is fine.

## Contents
1. Top level and patcher attributes
2. Boxes
3. Patchlines and midpoints
4. Port geometry (calibrated)
5. Colours
6. live.* parameters
7. Subpatchers, bpatchers, abstractions
8. Gotchas

## 1. Top level

```json
{ "patcher": {
    "fileversion": 1,
    "appversion": {"major": 8, "minor": 6, "revision": 5, "architecture": "x64", "modernui": 1},
    "classnamespace": "box",
    "rect": [x, y, w, h],              // window position/size on screen
    "openinpresentation": 0,            // 1 = opens in presentation view
    "gridsize": [15.0, 15.0], "gridonopen": 1, "gridsnaponopen": 1,
    "default_fontsize": 12.0, "default_fontname": "Arial",
    "editing_bgcolor": [r,g,b,a],       // optional: patching (unlocked) background
    "locked_bgcolor":  [r,g,b,a],       // optional: locked / presentation background
    "boxes": [ {"box": {...}}, ... ],
    "lines": [ {"patchline": {...}}, ... ]
}}
```
Colours are `[r, g, b, a]` floats 0-1.

## 2. Boxes

```json
{"box": {
  "id": "obj-12",                 // unique within this patcher; lines refer to it
  "maxclass": "newobj",           // newobj = object box; message, comment, panel, toggle, live.dial, ...
  "text": "cycle~ 440",           // newobj/message/comment content
  "numinlets": 2, "numoutlets": 1,
  "outlettype": ["signal"],       // per outlet: "signal", "multichannelsignal", "bang", "int", "float", "list", "" (anything), "jit_matrix"...
  "patching_rect": [x, y, w, h],  // position in the patching view
  "presentation": 1,              // optional: shown in presentation view
  "presentation_rect": [x, y, w, h],
  "background": 1,                // optional: drawn behind everything (panels)
  "hidden": 1,                    // optional: hidden when locked
  "varname": "osc1",              // optional scripting name (required for live.* params)
  "fontsize": 12.0, "fontface": 0 // 0 plain, 1 bold, 2 italic, 3 bold-italic
}}
```
- `numinlets`/`numoutlets`/`outlettype` are a cache: Max recomputes them from `text`
  when it loads the object, and silently drops any cord whose port no longer exists.
  Get them right (see `guess_ports()` in `scripts/maxpat.py`).
- Typical heights: object/message boxes 22, comments 20 per line (they wrap to their
  width), toggle/button 24x24, number/flonum 50x22, live.dial 44x48, live.gain~ 48x136.
- Panels: `{"maxclass": "panel", "background": 1, "bgcolor": [...], "border": 1,
  "bordercolor": [...], "rounded": 6}`.

## 3. Patchlines and midpoints

```json
{"patchline": {
  "source": ["obj-3", 0],         // [box id, outlet index]
  "destination": ["obj-7", 1],    // [box id, inlet index]
  "midpoints": [x1, y1, x2, y2],  // optional bend points, flat list
  "color": [r, g, b, a],          // optional
  "hidden": 1,                    // optional
  "order": 0                      // optional; written by Max for fan-outs (see Gotchas)
}}
```
Max draws: outlet anchor -> (x1,y1) -> (x2,y2) -> ... -> inlet anchor, all straight
segments. For a right-angle cord, the first midpoint must share the outlet's x and the
last must share the inlet's x. A straight vertical cord needs no midpoints at all.

Typical orthogonal "Z" cord from outlet (sx, sy) to inlet (tx, ty):
`midpoints = [sx, lane_y, tx, lane_y]` with `sy < lane_y < ty`.

## 4. Port geometry (calibrated)

Measured against 31,000 segmented cords in Max 8.6's own help files (99.9% match):

```
port_x(i, n) = x + 9.5                          if n == 1
             = x + 9.5 + i * (w - 19) / (n - 1)  if n > 1
outlet anchor y = y + h        inlet anchor y = y
```
Width rule of thumb for a 12pt object/message box: `w ~ 5.6 * len(text) + 14`.

**Pin pitch:** choose `w` so `(w - 19) / (n - 1)` is a multiple of 7.5 for both the
inlet and outlet counts, and put `x` on a multiple of 7.5. Then every port anchor is at
`9.5 + 7.5k`, so any two ports can be vertically aligned. `pitch_width()` does this.

Exceptions: `gain~` and `meter~` style sliders deviate slightly; UI objects keep the
size they're given.

## 5. Colours used by the style script

| where | key |
|---|---|
| a cord | patchline `color` |
| patcher background (editing / locked) | `editing_bgcolor`, `locked_bgcolor` |
| panel fill / border | `bgcolor`, `border`, `bordercolor`, `rounded` |
| comment text | `textcolor`, `fontface` |
| object box fill / text | `bgcolor`, `textcolor` (use sparingly) |

## 6. live.* parameters

```json
{"maxclass": "live.dial", "varname": "Cutoff", "parameter_enable": 1,
 "saved_attribute_attributes": {"valueof": {
    "parameter_longname": "Cutoff",      // unique name; match varname
    "parameter_shortname": "Cutoff",     // the label drawn on the control
    "parameter_type": 0,                 // 0 float, 1 int, 2 enum
    "parameter_mmin": 20, "parameter_mmax": 20000,
    "parameter_initial": [1000], "parameter_initial_enable": 1,
    "parameter_unitstyle": 3             // 0 int,1 float,2 ms,3 Hz,4 dB,5 %,6 pan,7 st,8 MIDI,9 custom,10 native
    // enum: "parameter_enum": ["saw","square"], parameter_type 2
 }}}
```

## 7. Subpatchers, bpatchers, abstractions

- `[p name]` and `[poly~]`-less subpatchers: the box has an embedded `"patcher": {...}`
  with its own boxes/lines - a full patcher, laid out independently.
- `bpatcher` with `"embed": 1` also embeds a `"patcher"`; otherwise it references a file.
- Abstractions (an object whose text names another .maxpat) are separate files - edit
  those files directly if asked.
- Subpatcher inlets and outlets are their own box types, not object boxes with text:
  `{"maxclass": "inlet", "index": 1, "comment": "", "numinlets": 0, "numoutlets": 1,
  "outlettype": [""], "patching_rect": [x, y, 30, 30]}` and
  `{"maxclass": "outlet", "index": 1, "comment": "", "numinlets": 1, "numoutlets": 0, ...}`.
  The same `inlet`/`outlet` carry audio too: there is no `inlet~`/`outlet~` in Max
  (those are Pure Data). They map to the parent box's ports in order of their
  **x position** (left to right). Moving inlets/outlets horizontally inside a
  subpatcher can reorder the parent's ports! The layout scripts don't special-case this
  yet: after a `full` layout of a subpatcher, check that inlet/outlet objects kept their
  left-to-right order, and fix by hand if not.

## 8. Gotchas

- **Fan-out order.** One control outlet feeding several inlets fires right-to-left by
  the inlets' x position (Max may save this as `order`, but position rules when the
  patch runs). Moving boxes can change behaviour. Use `[t ...]` when order matters.
  Audio (signal) fan-outs are unaffected.
- **Comments wrap.** A comment's text wraps to its `patching_rect` width, so a long
  comment in a narrow rect is a tall block. Budget its real height.
- **Hidden objects/cords** stay hidden; don't reveal or delete them.
- **IDs.** Every box id must be `obj-<number>` (e.g. `obj-25`) and unique within its
  patcher. Max uses nothing else (checked across 21,945 boxes); a box with any other id
  (like `obj-t1`) loads, but its cords don't. New boxes take the next unused number.
- **.amxd** (Max for Live) files are JSON behind a binary header; don't edit as text.
