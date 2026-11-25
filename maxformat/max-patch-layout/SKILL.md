---
name: max-patch-layout
description: House style and generator for Max/MSP patches (.maxpat) that are clean and readable - one left-aligned vertical spine for the main signal chain with branches to the side, signal flowing top to bottom, no crossing cables, correctly typed cords (audio vs messages), right-angle routing, source-destination named send/receive for long connections, labelled controls, and a presentation view every time. Use this skill whenever you create, generate, write, modify, or clean up a Max patch or write .maxpat JSON, including Max for Live patchers, jitter patches, and subpatchers - even if the user only says "make me a patch that does X" and never mentions layout.
---

# Max patch house style

Max patches are diagrams that people debug. A patch that works but looks like
spaghetti is unfinished. Every patch you produce must follow the house style below.
These rules are the user's explicit preferences, not suggestions.

The rules come first because they are what matters. The generator
(`maxformat.py`) exists to apply them precisely, because hand-computing
coordinates and cord bend points is error-prone. Use it whenever you can run
Python, and follow the same rules by hand when you can't.

## The house style

### Arrangement: a spine with branches
These rules come from patches the user arranged and fixed by hand
(`examples/messy-synth.human.maxpat`, `examples/messy-synth.newer.maxpat`). When in
doubt, look at those patches.
- **One main spine.** Find the main signal chain (e.g. `loadbang` → `metro` → `random`
  → `mtof` → oscillator → gain → `dac~`) and stack it vertically with **every spine
  object sharing the same left edge**. Left-outlet-to-left-inlet cords are then
  perfectly straight vertical lines with no bends.
- **Signal flows top to bottom.** An object sits below everything that feeds it.
  An object with nothing coming in sits above what it feeds. Feedback is the only
  cord that runs upward, and it should look exceptional.
- **Branches go to the right of the spine:**
  - displays (`number`, `flonum`, meters) sit just right of the spine, one row
    below the object they show;
  - secondary chains (an envelope, a modulator) get their own column further
    right, placed at the height where they're needed;
  - anything feeding a **right** inlet arrives **from the right**, so it never
    crosses the spine.
- **Utility goes to the left of the spine:** `[s]`/`[r]` pairs and housekeeping such
  as `[r …]` → `startwindow` → `dac~`.
- **Too tall for the screen (over ~800px)?** Split the patch into titled module
  columns, left to right in signal-flow order (CONTROLS, CLOCK, OSC, FILTER,
  OUTPUT...). Each column is its own top-to-bottom spine. Rows are shared across
  columns, so cross-column cords run down and to the right.

### Spacing
| between | distance |
|---|---|
| objects **directly chained on the spine** (outlet → inlet) | **~20px** |
| everything else: branches, neighbours, side objects, comments vs. objects (**hard minimum**) | **45px** |
| module columns (big patches only) | 90px |

A label sits about 10px right of its object, on the same row, and counts as part of
that object. The patch title sits on the top row, right of the first object. Never
let anything overlap.

### Cords
- **Right angles for anything long.** A long cord leaves its outlet going down, runs
  horizontally and vertically, and drops straight into its inlet. Max stores the bends
  in the patchline's `"midpoints"`. Spine cords need no midpoints, because the ports
  line up.
- **Stretch objects so ports line up.** A cord is straightest when the outlet sits
  exactly above the inlet. Widen an object to move its right-hand ports. Examples
  from the user's own fix (`examples/messy-synth.newer.maxpat`):
  - `[t f f f]` widened so its right outlet sits directly above the display it feeds;
  - `[+~]` widened so its right inlet sits directly below the trigger outlet feeding it.

  Both cords become perfectly vertical. Keep it reasonable: up to ~3× the box's
  natural width. Port x positions are given in "Writing .maxpat by hand" below.
- **Short diagonal hops are fine.** A cord to a nearby side object (one row down,
  within ~200px sideways, not passing through a box) may be a plain diagonal: no midpoints.
- **Avoid crossing cables.** Order things so cords don't cross: whatever feeds a left
  inlet sits left of whatever feeds a right inlet, and branches entering right inlets
  come down on the right. Route around rather than across. When placement can't
  avoid a crossing, use a named `[s]`/`[r]` pair instead. A short **diagonal** hop
  crossing another cord is acceptable (e.g. right under a `[t]`); long cords crossing
  each other is not.
- **Fan-out** cords from one outlet may share a trunk (e.g. down the spine), then
  branch off. **Fan-in** cords merge into one trace into the inlet. Unrelated cords
  never run on top of or right beside each other.
- **Execution order is sacred.** Max fires the cords leaving one outlet **right to
  left** by the destination inlets' x position, and **bottom to top** when two share
  the same x. This was verified against the `order` values Max saved in 16,000 of its
  own cords. Moving objects can therefore change what a patch does.
  - **Never change an existing patch's firing order.** When moving objects would
    change it, make the order explicit with a `[t]` instead, wired so the original
    order is kept. Then lay the objects out around that `[t]`.
  - In patches you write, put a `[t]` (trigger) directly under the source wherever
    order matters. It
  fires its outlets **right to left**. Its arguments set what each outlet sends:
  - `b` sends a bang;
  - `i` passes an integer (a float gets truncated);
  - `f` passes a float;
  - `l` passes a list, `s` a symbol;
  - any other value is sent as that constant, e.g. `[t b 0 stop]`.

  **Those are the only types.** There is no `a`: `[t a a]` doesn't pass things through,
  and Max breaks its connections. Pick the type to match what comes in: `[t b b]`
  after `[metro]`, `[t f f f]` after `[mtof]` (it outputs floats), `[t i i]` after
  `[random]`. Look a source up with `maxformat.py --object NAME` if unsure.
  - **Each outlet can have its own type**, chosen for what its destination needs:
    `[t b i]` passes the number (right outlet) first, then bangs (left); `[t i f]` gives
    one destination an int and another the same value as a float.
  - **Floats through `i` are truncated**: 261.63 becomes 261. Use `f` for float sources.
  - **Lists only survive `l` outlets.** Per Max's reference, a list arriving at a
    `[t]` comes out of `b`/`i`/`f`/`s` outlets as `bang`/`0`/`0.`/empty, **not** as its
    first element. So use `[t l l]`, and `[unpack]` or `[zl]` after an `l` outlet to
    take a list apart. `[t b l]` is "deliver the list, then trigger".
- **Hot and cold inlets.** On most objects the **left inlet is hot**: a message there
  makes the object output. **Right inlets are cold**: they only store a value for
  later. Patches use this to control order:
  - `[int]`/`[i]` and `[float]`/`[f]` store a number sent to their right inlet; a bang
    in the left inlet outputs it. `i` truncates floats.
  - A **message box** stores content sent to its right inlet (or via `set ...`); a bang
    outputs it.
  - So "remember this value now, send it later" is: value → right inlet of `[f]`, then
    bang → left inlet. A `[t b f]` above it does both, in the right order (right outlet
    first).
  - Layout follows from this: cold right-inlet feeds come from the right side, and
    the hot left-inlet path runs straight down the spine.

### Cord types: what can plug into what
Every outlet carries audio (signal), messages (numbers, lists, bangs, symbols), or
Jitter matrices. Get this right before thinking about layout: a wrongly typed cord
either won't connect at all or silently does nothing.
- **Objects ending in `~` work with audio.** Their outlets are audio (with rare
  exceptions such as `snapshot~`, which outputs numbers).
- **Audio can only go into an inlet that accepts audio**: another `~` object's signal
  inlet, `[send~]`, `[dac~]`, or a subpatcher/`[outlet]`. Audio into a message object
  (`[+]`, `[mtof]`, `[number]`, `[print]`, a message box) is impossible: Max refuses the
  cord. To turn audio into numbers, use `[snapshot~]`, `[peakamp~]`, `[edge~]`, or
  `[number~]`.
- **Messages into audio inlets:** fine where the inlet is `signal/float`. A number sets
  a constant, e.g. a frequency into `[cycle~]` or an offset into `[+~]`'s right inlet.
  Cycling '74's own patches do this hundreds of times. Two cases need `[sig~]`
  (constant) or `[line~]`/`[curve~]` (ramps) in between:
  - the inlet is **audio-only** (its reference type is `signal`): a number there is
    ignored;
  - the inlet **also has an audio cord** plugged in: while audio is connected, Max
    ignores the numbers.

  When in doubt, using `[sig~]` is never wrong, and it makes the conversion visible.
- `mc.` objects carry multichannel audio. Use `mc.` objects (or `mc.unpack~`) to read it.
- **When unsure what an object is or what an inlet takes, look it up instead of
  guessing:** `maxformat.py --object NAME` prints the object's description and the type
  of every inlet and outlet, from Max's own reference. The generator rejects audio cords
  into message-only inlets and warns about numbers sent to audio-only inlets.

### Send / receive
Replace a cord with a `[s name]`/`[r name]` pair (`[send~]`/`[receive~]` for
audio) when **any** of these is true:
1. the cord would span more than a screen: **wider than 1280px or taller than 800px**;
2. it would cross other cords and placement can't avoid it (always if **3 or more**);
3. a name would document the connection better than a line would.

Naming:
- Name it **source-destination**, lowercase with hyphens: `#0-loadbang-dac`,
  `#0-lfo-filter`, `clock-sequencer`. The name tells the reader where the connection
  comes from and where it goes.
- **`#0-name` when the connection is private to one instance** of the patcher,
  for example `[s #0-filter-cutoff]`. Max replaces `#0` with a number unique to
  each loaded abstraction instance, so copies don't talk to each other.
- **A plain name, with no `#0-`, when instances are meant to share it** (a global
  clock or transport).
- Note: a `[p name]` subpatcher shares its parent's `#0`. `#0-` isolates copies
  of an abstraction (a separate .maxpat file), not a `[p]` from its parent.

### Comments
- **No panel objects.** Grouping comes from whitespace and placement.
- In a big patch split into module columns, each column gets a **bold title comment**
  (short, UPPERCASE) at its top. A small single-spine patch needs no section titles.
- **Label every user control** and every **non-obvious object** with a short comment
  just to its right ("cutoff", "tempo", "ms per step"). Obvious objects (`*~`,
  `dac~`, `loadbang`) need no label.
- Optional: one short explanatory sentence where a section's purpose isn't obvious.
- The patch title (with its version) sits on the top row, to the right of the first
  object.

### Colour
- **Max defaults.** Don't set background, box, or cord colours unless the user asks.

### Presentation mode: always
- Every patch gets a presentation view and opens in it.
- The patch title and version sit across the top.
- Each module (from the spec) with controls becomes a section: its title, then its
  controls in a row, each with its label underneath. Sections run left to right
  in signal-flow order.
- No controls at all: the presentation shows the device name and version, plus
  anything else identifying that is available, such as the author.
- Prefer `live.*` UI objects (`live.dial`, `live.numbox`, `live.text`,
  `live.gain~`) with a parameter name. They label themselves and save their state.

## Generating a patch

The generator is `maxformat.py` at the root of the repository that contains
this skill (the parent of this file's directory, resolving symlinks). Run `maxformat.py --help` for its options.

1. **Describe the patch as a spec**: objects, cords, modules. You decide what the
   patch is and how it's grouped; the generator decides where everything goes.
   ```json
   {
     "title": "FM Voice", "version": "1.0",
     "objects": [
       {"id": "pitch", "ui": "live.dial", "param": {"name": "Pitch", "min": 20, "max": 2000, "init": 220, "unit": "Hz"}},
       {"id": "sig",   "text": "sig~"},
       {"id": "car",   "text": "cycle~"},
       {"id": "gain",  "ui": "live.gain~", "param": {"name": "Level", "min": -70, "max": 6, "init": -12, "unit": "dB"}},
       {"id": "dac",   "text": "dac~"},
       {"id": "dsp",   "ui": "toggle", "label": "audio on"},
       {"id": "go",    "message": "startwindow"}
     ],
     "cords": ["pitch:0 -> sig:0", "sig:0 -> car:0", "car:0 -> gain:0",
               "gain:0 -> dac:0", "gain:1 -> dac:1", "dsp:0 -> go:0", "go:0 -> dac:0"],
     "modules": [
       {"title": "CONTROLS", "members": ["pitch", "dsp"]},
       {"title": "OSC", "members": ["sig", "car"]},
       {"title": "OUTPUT", "members": ["gain", "go", "dac"],
        "note": "Toggle audio on, then raise Level."}
     ]
   }
   ```
   - `text` = object box, `message` = message box, `ui` = a UI maxclass,
     `comment` = free note under the patch title. `label` = the short comment
     beside an object.
   - Group objects into modules (they become presentation sections, and the
     columns if the patch is too tall for one spine). List members roughly in
     flow order; spec order only breaks ties.
   - Inlet and outlet counts are looked up for ~1,100 Max objects. For externals,
     abstractions, or unusual arguments, give `"inlets"`, `"outlets"`, and
     `"outlettype"` explicitly. The generator rejects a cord to a port that
     doesn't exist.
   - Use `[s]`/`[r]` objects in the spec wherever the send/receive rules apply.
2. **Generate:** `maxformat.py spec.json out.maxpat --png`
3. **Look at the PNG** (Read tool) and the printed score and advice. The generator
   flags any cord that should become a send/receive. Fix the **spec** (regroup
   modules, add a `[t]`, add a send/receive) and regenerate. Don't hand-edit
   coordinates in the output.
4. Tell the user what you built, and that the PNG is an approximation: open the
   patch in Max to see it for real.

## Cleaning up an existing patch

`maxformat.py in.maxpat out.maxpat` (never overwrite the user's file unless asked)
- Default: re-lays out as a spine with branches (see the house style). Objects
  with no inputs go on top, and everything they feed goes below them. If moving
  boxes would change which cord from an outlet fires first, it adds a `[t]` so the
  patch behaves the same. Tell the user things moved.
- `--keep-layout`: keeps the author's arrangement; snaps to the grid, opens up
  cramped spots, straightens cords. Use it when the existing arrangement means
  something to its author.
- `--route-only` only redraws cords; `--check` only scores the patch.
- Act on its `advice:` lines: they name cords that should become a named `[s]`/`[r]` pair.
- It never writes a result that would behave differently from the input.

## Writing .maxpat by hand (no Python available)

Follow the house style with these facts. `references/maxpat-format.md` has the
full JSON format.
- A cord attaches to port `i` of `n` at `x + 9.5 + i*(w-19)/(n-1)` (or `x + 9.5` if
  `n == 1`). Outlets are at `y + h`, inlets at `y`.
- Spine: give every spine object the same `x`, so left outlets line up with left
  inlets. Each spine object's `y` is the previous object's `y + h + 20`.
- Right-angle cord from outlet `(sx, sy)` to inlet `(tx, ty)`:
  `"midpoints": [sx, lane, tx, lane]`, with `lane` in the gap between the rows,
  at least 6px from each box. Put each cord crossing the same gap in its own lane,
  7.5px apart.
- Subpatcher inlets/outlets are `"maxclass": "inlet"` / `"outlet"` boxes with an
  `"index"` (30x30), not object text. They carry audio as well: Max has no
  `inlet~`/`outlet~`. In a spec, `{"text": "inlet"}` produces the right box.
- Object box width is about `5.6 × characters + 14`; height is 22. Positions go on
  the 15px grid.
- To make a cord perfectly vertical, choose the lower box's `x` so its inlet `x`
  equals the outlet `x` above it.
- Apply the spacing table exactly, then check that nothing not directly chained comes
  within 45px of anything else.

## Before you hand over a patch
- [ ] Main chain is one left-aligned spine (titled columns only if taller than ~800px); branches right, utility left
- [ ] Signal runs top to bottom; no cord runs upward except feedback
- [ ] Spine neighbours ~20px apart; everything else at least 45px; nothing overlaps
- [ ] Every cord is the right type: no audio into message-only inlets; numbers into
      audio-only inlets go through `sig~`/`line~` (check with `maxformat.py --object`)
- [ ] Long cords never cross; long cords are right-angled; short side hops may be diagonal (and may cross)
- [ ] Long or otherwise-crossing connections are `[s]`/`[r]` named source-destination (`#0-` if private)
- [ ] Controls and non-obvious objects are labelled; no panels; default colours
- [ ] A presentation view exists, with a title and version
- [ ] Order-sensitive fan-outs go through `[t]`
