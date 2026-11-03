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
- Live's compact device view has a fixed 169-pixel height ([Cycling '74 UI guide](https://docs.cycling74.com/userguide/m4l/live_userinterfaces/)). Declare logical viewport and preview render scale separately; larger panels need an expanded window plus a compact view. Static bounds do not prove host fit, window opening, input or recall.
- A `live.button` can emit `bang`, which is not a numeric CC value. Convert its state to the intended numeric MIDI value explicitly and verify the chosen semantics against the official mapping before `ctlout`.
- For send/receive buses meant to stay inside one M4L device instance, use Live's device-local `---` symbol prefix consistently on every sender and receiver; `#0` is an abstraction identifier and should not be assumed to isolate top-level AMXD copies. Cycling '74 documents the distinction in [Using Symbols in Max for Live](https://docs.cycling74.com/userguide/m4l/live_symbols/). A TR-1000 two-instance Live Beta test on 2026-11-01 found cross-instance BD Decay mutation with `#0` buses and independent 64/91 Live parameters after changing those buses to `---`; the observation covers that exact candidate and dial, not every device or parameter.

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

- Build repeatable Live/Max tests around terminal actions and structured receipts where supported: Extension calls for Live-exposed parameters and topology, M4L callbacks/logs for private execution, and local CLI for source, archive, hashing and assertions. Record any exact UI-only action and the missing API capability before using CUA; do not rely on screenshots or coordinate clicks as the routine test interface. Use an MCP only when an external connected service materially helps with the requested task; first verify that the relevant MCP exists and is connected. Do not claim MCP availability or install one by default.
- For Live/Max CUA sequences on macOS, choose one active Desktop/Space at the start and keep Live Beta, its dialogs, Live-owned Max windows, and the Console there. Avoid full-screen mode or moving a related window to another Space. After opening a picker or editor, confirm the foreground window is on that same Desktop before the next action. On a missing window or `10005`, check focus and Space placement once before retrying; do not switch among Desktops or ask the user to hunt for the window. If CUA cannot verify placement, record it as unknown and use process/log/Extension evidence. Space placement is a possible contributor, not a diagnosed cause for every timeout.
- Treat CUA `10005:noWindowsAvailable` as a desktop-control failure, not evidence that Live timed out or failed to load a Set. Check the Live process, exact Set path/title, completion lines in Live's log and any Extension response independently, and allow the Set to finish loading before assessing device state. If name-based app lookup misses a running Beta, try its verified app path or bundle ID once; a readable AX tree or screenshot does not prove click input works. After the bounded CUA recovery, preserve the app/session and report the bridge as blocked if the action still fails. Request a Live relaunch only when a process restart is the acceptance check or independent evidence shows Live is unhealthy.
- For switching to an existing Set in the same Live process, use devtools `ax live-set-open`; read [Devtools entrypoints](references/devtools.md) and `docs/LIVE-SET-OPEN.md`. Review its read-only preflight with the exact PID, active source URL, and source/destination SHA-256 values. Independently confirm stopped transport with the read-only `ax live-transport` inspection for that exact Set. `--save-owned-source` is separate explicit permission: it requires a private backup root and preserves the approved pre-save bytes before one ordinary Save. Inspect the `ready` receipt before `--execute`; stop on uncertain effects and do not retry blindly. A Set-switch attempt invalidates SDK/LOM and device-bound M4L handles even if Live keeps the same PID; rebind and verify the exact Set path, current graph/session, component pins, routing and quiet state. An `opened` receipt proves the same-process document URL and file observations only.
- For a true process-lifecycle or recall test that requires a normal quit and a new Live PID, keep using devtools `ax live-set-lifecycle`; read `docs/LIVE-SET-LIFECYCLE.md` and inspect its read-only `ready` receipt before `--execute`. After a stopped/unknown outcome, inspect the current state before any further action; do not retry automatically. This is distinct from same-process Set switching; a `reopened` receipt proves host lifecycle, not M4L recall or silence. Do not quit or restart Live just to recover a CUA or SDK bridge.
- Close Live through its application Quit command and handle any save dialog before a restart test. Do not use `SIGTERM` as a graceful quit: on Live Beta 12.4.25b1 it produced an unexpected-quit recovery prompt and a crash report for a saved disposable Set. A Set loaded after declining recovery proves only recovery-path reopen, not clean quit/relaunch recall. Preserve the crash report and label that evidence accurately; do not send it without authorization.
- For authorized test-Set preservation, locate a reviewed devtools checkout/version through [Devtools entrypoints](references/devtools.md) that contains `cua/live_set_save_as.mjs`; read its `docs/CUA-LIVE-SAVE-AS.md` rather than assuming the canonical checkout is current. Supply an explicit CUA app handle, exact source/final URLs and a fresh destination. On failure inspect `phase`, `saveAttempted` and `effect`; do not automatically retry a possible Save. Independently check source/destination hashes and saved AMXD FileRefs with `ableton inspect-set-device-refs`. The documented R32 result verifies one Live 12.4.16/macOS sequence, not other hosts, crash durability or recall.
- For Live-hosted M4L diagnostics, identify the console's product/process context before interpreting posts or ports. The verified Live workflow is the device's **Show Options → Open Max Window**, which opened a Live-owned **Max for Live** window. A separate Max editor application's **Max Console** can be a different execution context: absent posts or its apparent 1/1 ports do not establish the Live runtime's compilation or ports, even with matching source URLs. Record the context, exact artifact/source pins, unique runtime tags and readiness separately. See [Live runtime console evidence](references/live-runtime-console.md) when diagnosing this distinction.
- Preserve unrelated console history; filter exact unique tags instead of clearing it. Prefer the existing structured receipt or a verified console read/copy mechanism for evidence. In the verified Live-owned window, the File menu offered **Save Live Set**, not console export; do not use that action to capture logs.
- When a CUA file picker exposes a settable path field, use its value-setting API and verify the resulting path; `typeText` can silently lose characters in paths with spaces.
- If CUA returns error `10005`, one bounded recovery may reset only the CUA JS runtime, then call `getState()` first and reacquire fresh app/tab handles before retrying the failed action once. A runtime reset clears JS bindings but does not close native apps. A transient success restores access; it does not prove the root cause or a permanent fix. If the same action fails again, preserve the app/session, stop retrying, record the blocker, and continue independent work without force-quitting or broad permission changes.

## Recovered archives and runtime adapter checks

- A standalone MXF may contain a complete `mx@c` archive with a `dlst` directory. Inventory the directory before declaring nested patchers or assets missing; extract entries by their recorded offsets and sizes. The first/root entry defines the boot patch, which may differ from another similarly named archived host patch. Keep baseline payload hashes and required factory presets/assets separate from editable overrides and local state.
- Do not insert generated CC bytes into an unfinished raw MIDI note. Buffer complete events or parse/reformat complete channel messages, and document any lost release velocity, SysEx, or realtime forwarding. Verify event order separately from physical timing.
- Use actual Max object contracts in generated patches: `gate` controls/data inlets differ, and `pak` emits from every inlet while `pack` has cold inlets. Check controller/value ordering and that changing channel or enable does not emit stale controls. Pure graph-port checks do not establish those semantics.
- For reusable subpatch/abstraction interfaces, check exposed inlet/outlet ordinals against native ordering and the consumer's numeric patchlines. Object IDs and JSON box-array order are not a port contract. Cover role/index swaps with a consumer fixture when changing an interface; source checks still do not prove native execution.
- Max JS dispatches a named message directly to a matching function before `anything()`. Separate timestamp-taking decoder functions from zero-argument polling adapters and test the adapter path, not only the decoder.
- When replacing a file through Max JS `File`, reset its position and truncate it before writing; otherwise a shorter JSON result/preset may leave old trailing bytes. Verify a long-to-short overwrite.

## Reusable inspection tools

For saved MIDI Monitor documents, timed captures, IAC isolation or host-log correlation, use the `midi-capture-testing` skill. Prefer offline reports and assertions when saved evidence answers the check. Use repository testing instructions for the actual branch; zero discovered tests and static graph validity do not establish behavior.

For MXF/AMXD archive inventory/extraction, Max graph checks or deterministic hash manifests, read [Devtools entrypoints](references/devtools.md) and reuse the tested suite when its capabilities fit. Keep instrument tables, encoders, behavior tests and capture fixtures in their device/studio repository.

## Current studio license and deployment policy

The user owns Max for Live, not full Max. New performance/sequencing/control deliverables must run and be editable under that license inside Live. Launch the editor from Live’s Edit in Max command; supported deliverables are appropriate AMXD devices with their dependencies. Plain Max patches/JS can be shared modules or fixtures, but independent Max authoring or standalone application rebuilding is not an assumed workflow.

Cycling ’74 documents separate Max/M4L licenses and restrictions on saving devices using Gen, MC and Jitter Geometry. Existing devices may run without granting authoring rights. Avoid those authoring dependencies in new work unless verified under the user’s license. The recovered Host has Gen/MC content: keep it a preserved reference/existing app until a specific M4L port or licensed-build path is proven. Do not recommend buying Max without a demonstrated required feature and evaluation of an M4L-compatible alternative.

Canonical Git repos are under ~/Code; each agent works in its own worktree. Keep Ableton library paths stable and deploy only a reviewed file manifest with source/destination hashes, backup receipt and rollback. Max validation must include an actual Live-host device load/save/reopen and dependency check; standalone execution or offline AMXD packaging alone does not prove M4L compatibility.

Sources: https://support.cycling74.com/hc/en-us/articles/360049845974-Common-questions-about-Max-for-Live and https://www.ableton.com/en/manual/max-for-live/ .

## Existing controllers and reuse research

Before implementing a substantial new controller/sequencing module, inspect user-selected local assets and run a bounded GitHub/Max-community survey. Read actual source, license and dependency information for promising candidates; record specific reusable patterns and M4L-only authoring limits. A forum post or repository title is a lead, not proof of compatibility. Do not install or execute unknown code merely to survey it.

An existing AMXD is already a device-format candidate: its problem may be bridge/Set routing, unresolved references or host-version compatibility rather than a file-format conversion. Inspect saved Set MIDI-to/from routes and explicit remote mappings separately. `MidiControllerRange` fields can describe clip data and are not proof of a global learned controller mapping. Preserve the original and test a separately named migration copy in Live.
