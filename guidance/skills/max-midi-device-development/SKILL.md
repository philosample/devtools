---
name: max-midi-device-development
description: Create, repair, review, or validate Max for Live MIDI devices, mappings, frozen AMXDs, and their Live rack/session dependencies.
---

# Max MIDI device development

Use this skill for Max for Live MIDI device work. Keep each task limited to the requested device, files, and verification level. Read the repository's `AGENTS.md` first when present; it owns local canonical tables, hardware routes, and project-specific commands.

## Mapping and patch structure

- Start from the official MIDI implementation chart and applicable firmware/manual version. Record source URL, version, and a checksum when practical. Build structured mapping data first, then keep CSV/Markdown renderings and device routing in parity. Do not invent CC or note numbers for undocumented features.
- Give each device-specific abstraction a distinct name and explicit inlet/argument contract. Don't repurpose a generic or legacy abstraction if other devices may resolve it from a shared Max search path.
- Treat the editable `.maxpat` and shipped `.amxd` as a source/build pair. Rebuild through the project's supported tooling, preserve frozen assets/dependencies, and verify source/device parity plus archive round-trip before delivery.
- Preserve established Live parameter IDs, names, order, ranges, and defaults. Add new parameters without remapping old ones; update the parent parameter registry where the project uses one.
- A `live.button` can emit `bang`, which is not a numeric CC value. Convert its state to the intended numeric MIDI value explicitly and verify the chosen semantics against the official mapping before `ctlout`.

## Racks, Sets, and releases

- Inventory only user-selected `.als`, `.adg`, and `.adv` locations before dependency changes. Record device paths, hashes, and missing references. Keep file-reference relinking separate from saved parameter state and MIDI/audio routing; compare preserved state when preparing a copy.
- Keep the canonical installed device path stable and version source/release changes with Git tags or the repository's existing release process. Prepare opt-in, separately named rack/Set copies for migration. Don't bulk-rewrite existing Sets, aliases, or library presets.
- Authorization for a device edit does not automatically cover overwriting user presets/Sets, installing an MCP, publishing, or changing external hardware state. Respect the task's specific authorization and use copies for review.

## Choose the right verification level

Use the least costly evidence that answers the request, and report each level accurately:

1. Static table, graph/port, parameter-map, binary dependency, and source/device checks.
2. Actual Max execution with captured MIDI messages when the patch can run safely outside Live.
3. Live-host load/save, parameter visibility, recall, and routing checks.
4. MIDI Monitor or equivalent wire capture for outgoing channel, status, controller, and value bytes.
5. Physical hardware response on the stated firmware and connection.

Lower levels do not prove higher ones. A captured sink replacing `ctlout` proves captured values, not physical MIDI. Don't run a full test cascade or broaden changes automatically; follow the task's requested checks and stop when evidence is sufficient.

## Tools and UI recovery

- Prefer a local CLI for source, mapping, archive, hashing, and selected-directory inventory work. Use CUA for actions that genuinely require the Live UI. Use an MCP only when an external connected service materially helps with the requested task; first verify that the relevant MCP exists and is connected. Do not claim MCP availability or install one by default.
- When a CUA file picker exposes a settable path field, use its value-setting API and verify the resulting path; `typeText` can silently lose characters in paths with spaces.
- If CUA returns error `10005`, one bounded recovery may reset only the CUA JS runtime, then call `getState()` first and reacquire fresh app/tab handles before retrying the failed action once. A runtime reset clears JS bindings but does not close native apps. A transient success restores access; it does not prove the root cause or a permanent fix. If the same action fails again, preserve the app/session, stop retrying, record the blocker, and continue independent work without force-quitting or broad permission changes.

## Recovered archives and runtime adapter checks

- A standalone MXF may contain a complete `mx@c` archive with a `dlst` directory. Inventory the directory before declaring nested patchers or assets missing; extract entries by their recorded offsets and sizes. The first/root entry defines the boot patch, which may differ from another similarly named archived host patch. Keep baseline payload hashes and required factory presets/assets separate from editable overrides and local state.
- Do not insert generated CC bytes into an unfinished raw MIDI note. Buffer complete events or parse/reformat complete channel messages, and document any lost release velocity, SysEx, or realtime forwarding. Verify event order separately from physical timing.
- Use actual Max object contracts in generated patches: `gate` controls/data inlets differ, and `pak` emits from every inlet while `pack` has cold inlets. Check controller/value ordering and that changing channel or enable does not emit stale controls. Pure graph-port checks do not establish those semantics.
- Max JS dispatches a named message directly to a matching function before `anything()`. Separate timestamp-taking decoder functions from zero-argument polling adapters and test the adapter path, not only the decoder.
- When replacing a file through Max JS `File`, reset its position and truncate it before writing; otherwise a shorter JSON result/preset may leave old trailing bytes. Verify a long-to-short overwrite.

## Reusable inspection tools

For MXF/AMXD archive inventory/extraction, Max graph checks or deterministic hash manifests, read [Devtools entrypoints](references/devtools.md) and reuse the tested suite when its capabilities fit. Keep instrument tables, encoders, behavior tests and capture fixtures in their device/studio repository.

## Current studio license and deployment policy

The user owns Max for Live, not full Max. New performance/sequencing/control deliverables must run and be editable under that license inside Live. Launch the editor from Live’s Edit in Max command; supported deliverables are appropriate AMXD devices with their dependencies. Plain Max patches/JS can be shared modules or fixtures, but independent Max authoring or standalone application rebuilding is not an assumed workflow.

Cycling ’74 documents separate Max/M4L licenses and restrictions on saving devices using Gen, MC and Jitter Geometry. Existing devices may run without granting authoring rights. Avoid those authoring dependencies in new work unless verified under the user’s license. The recovered Host has Gen/MC content: keep it a preserved reference/existing app until a specific M4L port or licensed-build path is proven. Do not recommend buying Max without a demonstrated required feature and evaluation of an M4L-compatible alternative.

Canonical Git repos are under ~/Code; each agent works in its own worktree. Keep Ableton library paths stable and deploy only a reviewed file manifest with source/destination hashes, backup receipt and rollback. Max validation must include an actual Live-host device load/save/reopen and dependency check; standalone execution or offline AMXD packaging alone does not prove M4L compatibility.

Sources: https://support.cycling74.com/hc/en-us/articles/360049845974-Common-questions-about-Max-for-Live and https://www.ableton.com/en/manual/max-for-live/ .

## Existing controllers and reuse research

Before implementing a substantial new controller/sequencing module, inspect user-selected local assets and run a bounded GitHub/Max-community survey. Read actual source, license and dependency information for promising candidates; record specific reusable patterns and M4L-only authoring limits. A forum post or repository title is a lead, not proof of compatibility. Do not install or execute unknown code merely to survey it.

An existing AMXD is already a device-format candidate: its problem may be bridge/Set routing, unresolved references or host-version compatibility rather than a file-format conversion. Inspect saved Set MIDI-to/from routes and explicit remote mappings separately. `MidiControllerRange` fields can describe clip data and are not proof of a global learned controller mapping. Preserve the original and test a separately named migration copy in Live.
