---
name: max-patch-layout
description: Generate, lay out, or review editable Max/MSP and Max for Live .maxpat graphs with MaxFormat. Use for patching-view readability, cord routing, or presentation layout when creating a new patch or deliberately changing an existing layout.
---

# Max patch layout

Use `maxformat` when installed. Run the checkout's `python3 maxformat/maxformat.py` from DevTools when the command is unavailable.

- For a new patch, put the main chain in a left-aligned vertical spine. Place branches on the right and utilities on the left. Use titled columns for tall patches. Label performer controls, use default colors, and provide a presentation view.
- For an existing patch, start with `maxformat input.maxpat --check`. Write a separate candidate. Preserve reviewed placement, presentation, parameter IDs, cord types, execution order, hidden wiring, and subpatcher port order. Prefer cord-only changes when placement already works.
- Read the output diff and any preview. A preview or static check establishes graph and layout facts. Use the owning project's Max or Live procedure for runtime behavior.

Read only the reference needed for the work:

- [Commands and review](references/commands.md) for choosing a mode, authoring a spec, and checking output.
- [Layout and routing](references/layout-and-routing.md) for placement, spacing, signals, fan-out, and send/receive names.
- [Max patch JSON](references/maxpat-format.md) for editing objects, patchlines, and subpatchers by hand.
- [Presentation](references/presentation.md) for building or reviewing the performer-facing view.
