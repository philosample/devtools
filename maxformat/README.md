# maxformat

A Claude skill, plus the generator it uses, for Max/MSP patches that are clean and
readable. Modules are titled columns, signal flows top to bottom, spacing is
generous, cords run at right angles, and every patch gets a presentation view.

- **The skill:** [`max-patch-layout/SKILL.md`](max-patch-layout/SKILL.md) is the house
  style Claude follows whenever it writes a patch.
- **The generator:** `maxformat.py` applies that style precisely, building a new
  patch from a spec or cleaning up an existing one.

```
./maxformat.py input.maxpat output.maxpat [options]
```

Python 3.9+, no dependencies. PNG previews need macOS.

## Files

| File | What it does |
|---|---|
| `maxformat.py` | The command: formats a patch, scores it, or builds one from a spec. |
| `maxlayout/cli.py` | Parses options and runs the format / check / build steps. |
| `maxlayout/build.py` | Builds a house-style patch from a short JSON spec. |
| `maxlayout/layout.py` | Places boxes so sources sit above everything they feed. |
| `maxlayout/route.py` | Redraws every cord as right-angle segments that avoid boxes and each other. |
| `maxlayout/present.py` | Lays out the presentation view in titled sections. |
| `maxlayout/lint.py` | Scores tidiness and proves formatting didn't change the patch's behaviour. |
| `maxlayout/style.py` | Optional colour themes, used only when asked for. |
| `maxlayout/render.py` | Draws a patch to a PNG so it can be checked without opening Max. |
| `maxlayout/maxpat.py` | Reads and writes `.maxpat` files and knows Max's port geometry. |
| `maxlayout/mine_objdb.py` | Rebuilds the object port database from a local Max install. |
| `maxlayout/mine_refdb.py` | Rebuilds what each object does and what its inlets/outlets carry, from Max's reference. |

## Examples

Score the intentionally messy example without changing anything:

```sh
./maxformat.py examples/messy-synth.maxpat --check
```

Lay it out (sources on top, everything they feed below them), with a preview image:

```sh
./maxformat.py examples/messy-synth.maxpat examples/messy-synth.clean.maxpat --png
```

Keep its existing arrangement, just tidier:

```sh
./maxformat.py examples/messy-synth.maxpat examples/messy-synth.clean.maxpat --keep-layout
```

Open the result in Max:

```sh
open examples/messy-synth.clean.maxpat
```

Build a new patch from a spec:

```sh
./maxformat.py examples/fm-voice.spec.json examples/fm-voice.maxpat --png
```

Only redraw cords, without moving any boxes:

```sh
./maxformat.py examples/messy-synth.maxpat examples/messy-synth.clean.maxpat --route-only
```

Look up what a Max object does and what each inlet/outlet carries:

```sh
./maxformat.py --object cycle~
```

Run the tests:

```sh
python3 -m unittest discover -s tests -t .
```

## Safety

`maxformat.py` never writes a file if any connection, fan-out firing order, or
subpatcher inlet/outlet order would change. Max fires one outlet's cords right to
left by position, so moving boxes can silently change what a patch does.
When that would happen, it adds a `[t]` object to keep the order explicit
(or, with `--no-triggers`, refuses to write anything).

## Using the skill

Point Claude Code at this repo, or symlink `max-patch-layout/` into
`~/.claude/skills/`. Claude then follows the house style whenever it creates or
edits a Max patch.
