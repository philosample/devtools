# Patching-view layout and routing

## Placement

Build the main signal flow as a vertical chain with a common left edge. Keep displays one row below and beside their source. Put envelopes, modulation and feeds into right inlets on the right. Put housekeeping and remote connections on the left. Keep feedback loops visually distinct. Split a flow taller than roughly 800 px into titled columns, in signal-flow order.

Allow about 20 px vertically between directly chained objects, 45 px among other neighboring objects, and 90 px between module columns. Place concise labels about 10 px right of controls or non-obvious objects. Use whitespace and section labels for grouping. Use Max's default colors unless the user chose a palette.

## Cords and order

Prefer straight vertical cords and right-angle bends for longer runs. Widen a box modestly to line up ports when useful. Short diagonal side hops are fine when they avoid other objects. Preserve fan-out order, including implicit order that depends on destination geometry and explicit order where present. Preserve the left-to-right order of subpatcher inlets and outlets.

Use a typed `trigger` when the intended firing sequence needs to be explicit. Outlets fire from right to left. Use `b` for bang, `i` for integer, `f` for float, `l` for list, or `s` for symbol. A list sent through a non-list outlet is converted. For example, `t b f` sends the value to the cold inlet first and then bangs the hot inlet. Check the documented inlet behavior for the actual object. [Cycling '74 trigger reference](https://docs.cycling74.com/reference/trigger/)

Look up inlet and outlet types with `maxformat --object NAME` or the object reference. Use signal-compatible inlets for audio and explicit conversion for audio-to-message or message-to-signal boundaries where needed. Use MC-compatible objects for multichannel signals.

## Named connections

Consider a named connection when it replaces a run wider than 1280 px or taller than 800 px, removes unavoidable crossings, or makes a real dependency easier to read. Use lowercase source-to-destination names and preserve the required sharing scope. Match `send` and `receive` to messages and `send~` and `receive~` to audio. `#0` isolates abstraction instances and is shared by a parent and its inline subpatchers. For new device-local M4L buses use the documented `---` convention when it matches the existing device contract. [Cycling '74 symbols in Max for Live](https://docs.cycling74.com/userguide/m4l/live_symbols/)
