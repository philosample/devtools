# MIDI capture tools

Requires macOS, Snoize MIDI Monitor (tested 1.5.4), Python 3 and Accessibility permission for the process launching `osascript`. Endpoint inventory/template creation additionally uses Apple's Swift developer tools. No third-party Python dependencies. Offline export/report does not launch an app.

```sh
python3 -m codex_devtools midi endpoints
python3 -m codex_devtools midi template --source 'IAC Driver Studio Capture' --out /absolute/local/capture-template.mmon
python3 -m codex_devtools midi capture --template /absolute/local/capture-template.mmon --seconds 10 --out /absolute/local/new-run
python3 -m codex_devtools midi source-capture --source-uid 123456 --seconds 5 --max-events 256 --max-bytes 4096 --out /private/tmp/raw-source-capture.json
python3 -m codex_devtools midi loopback --out /absolute/local/native-loopback.json --timeout 3
python3 -m codex_devtools midi export /absolute/local/new-run/capture-new-run.mmon --format csv --out /absolute/local/export.csv
python3 -m codex_devtools midi report /absolute/local/new-run/capture-new-run.mmon --receipt /absolute/local/new-run/receipt.json --expect /absolute/local/expectations.json
```

The template resolves exactly one online CoreMIDI source by name/displayName. Names can be ambiguous: inventory first. Templates contain machine-specific endpoint IDs and belong outside Git. Recreate them after MIDI configuration changes. A single input source is the default: multi-source/spying documents must be configured and saved in MIDI Monitor deliberately before use as templates. Capture opens a fresh copy, retains a preflight saved document, sets the event buffer (100,000 default), clears events, waits, disconnects that document's sources, saves and closes. It does not clear existing user windows, alter global preferences, send MIDI, or configure Live.

`midi source-capture` is a passive alternative for byte-level cross-checks. First run `midi endpoints`, select the intended online `source` row, and pass its exact numeric `uniqueID`; the command never resolves by name or connects to a destination. It binds one CoreMIDI input port to that source and records each received `MIDIPacket` as its exact byte array with CoreMIDI timestamp, callback monotonic timestamp, sequence and source UID. The requested duration is 0.05–30 seconds; packet and byte limits are mandatory bounded caps (defaults 256 and 4096). `MIDIPacket` stores at most 256 payload bytes; larger lengths fail before copying rather than being clipped. Hitting either packet or total-byte cap also fails the receipt as potentially truncated. After connecting and rechecking the exact source handle/UID, the child emits one compact `READY` JSON line with schema, run UUID, source UID, child PID, connected flag and monotonic timestamp. The CLI checks the exact field set and identities while the child remains live, then forwards that marker to stdout immediately before waiting for the final receipt. Callers can wait for this line before producing a test stimulus; the final receipt repeats run/PID/source identity. Missing, partial, stale, unknown or uncorrelated markers fail closed. The listener disconnects the source and disposes its input port/client before accepting a capture. It creates no output port and has no send API. Evidence includes the Swift source hash and toolchain/SDK pins. This is a passive source-side CoreMIDI observation; it does not prove what Max/Live emitted before the IAC source or whether a MIDI Monitor display/export changed the representation. It sends no MIDI and does not configure routes or Live.

Each new run directory contains the original `.mmon`, `preflight.mmon`, parsed `events.json`/`events.csv`, and `receipt.json`. After MIDI Monitor closes the capture document, the CLI waits up to 30 seconds for a parseable save with a `streamSettings` mapping, a `portInputStream` list, all stream lists disconnected, and bytes unchanged for 0.5 seconds. A still-connected, malformed, unreadable, or changing document fails capture; it is never accepted as a completed capture. The receipt's capture hash is tied to the stable bytes that passed this validation, and a later file change causes failure. The receipt records configured and resolved source settings/IDs, requested interval, buffer size, elapsed time, app version when available, and SHA-256 hashes for the template, AppleScript adapter, native capture, preflight, and exports. Failure receipts hash any partial artifacts that were saved. Output directories and export files are created exclusively; existing paths are refused.

`midi report` always prints the `.mmon` file hash, parser source hash, native document version and event count. Pass `--receipt` for a run-directory receipt to verify the exact capture bytes and path, event count, matching configured/resolved source identities, and either that the saved source settings still match them or that every validated stream list in the saved capture is empty. Receipt status `inconclusive` remains inspectable but causes report exit 1; receipt mismatches and malformed evidence exit 2. Reports without a receipt remain useful for historical captures but cannot prove the capture's source identity. `--expect` requires at least one explicit event-count rule; empty rules are rejected so a vacuous assertion cannot pass. Report exits 1 when an expectation fails. A full buffer produces an inconclusive capture result (exit 1); adapter or configuration failures exit 2. Partial evidence is retained on failure; the test document may remain open. A successful capture is not automatically a passing behavioral test. Use expectations requiring positive event counts when silence is not expected.

Example expectations:

```json
{"expectations": [
  {"type": "clock", "origin": "From TR-8S", "min_count": 1},
  {"type": "invalid", "max_count": 0},
  {"type": "control_change", "channel": 10, "controller": 22, "value": 127, "min_count": 1}
]}
```

Rules match exact subsets; each needs at least one selector and count bound. `report` exits 1 on failed assertions. It reports note balance within the captured window, CC ranges, invalids and per-origin clock intervals. Unmatched notes can be explained by capture boundaries; they do not by themselves prove stuck notes. Clock summaries use only known reference-second timestamps; historical host ticks are preserved without guessing units. Clock intervals describe archived MIDI timestamps, not physical delivery latency or guaranteed arrival order. UI capture windows have start/stop uncertainty and input-disconnect overhead; they are not sample-accurate timing gates. No loss detector beyond the buffer warning is claimed.

Every capture run also exposes `capture-ready.json` in that new run directory.
MIDI Monitor writes this JSON marker exclusively and atomically immediately after
the selected document's Clear action and capture start boundary. It identifies
the run UUID, owning CLI PID, capture filename and document URL, template hash,
and configured source IDs. This raw marker is a boundary observation only; it
is never permission to release stimuli because it precedes Python's validation
of the saved preflight document.

An external orchestrator must wait until `receipt.json` contains a valid `ready`
object, then admit stimuli only while its exact capture child is still live. It
must require `receipt.status == "running"`, `ready.schema ==
"devtools-midi-capture-ready-v1"`, and match `ready.run_id` and
`ready.capture_process_pid` to the marker and top-level receipt. It must also
match `ready.automation_process_pid` to the exact `osascript` child PID it
started and confirm that child has not exited. The `ready.source_identity` must
match `receipt.configured_source_identity` and the marker's configured source
IDs; `ready.preflight_sha256` must match the current `preflight.mmon` bytes, and
the marker's template hash must match `receipt.template_sha256`. If the receipt
is incomplete or any comparison fails, keep waiting or fail closed. A malformed,
stale, mismatched or partial raw marker never authorizes a trigger. The handshake
establishes the post-Clear capture boundary and validated source configuration;
it does not prove that a MIDI source is delivering events or that a downstream
app receives them.

## Studio Capture IAC setup

Create a dedicated port in Audio MIDI Setup → MIDI Studio → IAC Driver. The studio machine now has `Studio Capture`, with the IAC driver online. Bringing IAC online also exposes pre-existing ports; no old ports were removed. CoreMIDI inventory verified the new source.

For a disposable Live test Set, disable **all input Track/Sync/Remote** switches for this bus. Enable **output Track** only for note/CC tests; keep **output Remote and Sync off**. Enable output Sync explicitly for clock tests with a single clock authority. Route only the test track to this bus, and select its source in MIDI Monitor. Verify switch state after reconnects. These Live settings are a required setup gate: they have not yet been changed or verified by this implementation because Live accessibility timed out. Do not send synthetic probes until the input isolation is verified.

IAC proves the Live output path. A physical device's MIDI output can provide stronger evidence if it returns a documented acknowledgement, parameter dump/readback or other device-generated response. A MIDI Thru echo proves a return route, not that the device applied the parameter. Record firmware, physical ports, response semantics and request/response matching separately. Sound/state checks remain necessary where the protocol has no readback.

## Runtime evidence and next improvements

### Live playback isolation and restoration

No Output on test tracks does not disable Live's global output Sync switches.
Before a playback test, inspect the complete applicable output inventory in
Tempo & MIDI Settings. Preserve exact port names and original Sync values.
If authorized, disable only the enabled Sync outputs and verify all applicable
outputs are off before starting. Keep the test tracks isolated separately.
Stop transport before restoring the exact original values; verify restoration
even if the test fails. Do not infer current isolation from an earlier run.
This procedure does not disable control-surface scripts or prove MIDI silence.

For an exact native AX inspection and explicit guarded isolate/restore of these
Sync checkboxes, see [Live MIDI Output Sync control](LIVE-MIDI-SYNC-CONTROL.md).
It uses the caller's fresh baseline and independently supplied full output
count; its receipt establishes only observed Settings checkbox state.

The read-only saved-AX verifier avoids rewriting the comparison logic:

```sh
python3 -m codex_devtools.live_sync_ax before.txt isolated.txt restored.txt \
  --expected-output-count 25
```

Supply the actual independently inspected inventory count, not this example's
25. It rejects missing, duplicate, renamed or unsupported rows, requires every
isolated Sync value off, compares restoration exactly and hashes all three
UTF-8 snapshots. Full AX rows and `+`/`~` diff rows are accepted only when the
explicit count and exact identities agree; a partial diff normally fails.
Exit 0 means saved values match the declared inventory/checks, 1 means isolation
or restoration differs, and 2 means invalid input. It does not discover ports,
drive CUA, change settings, authorize playback or prove complete inventory,
current Live state or wire behavior. Keep machine-specific snapshots local.

R39 on Live 12.4.16/macOS verified this workflow with 25 enumerated outputs and
six temporarily disabled/restored Sync switches; original values matched after
stopping. Studio-control PR242 records the exact native observation receipt.
That single-host result is not a portable preference API or a timing guarantee.

### Native isolated CoreMIDI loopback

`midi loopback` compiles a small Swift program with the installed Apple toolchain and runs a bounded, same-process transport check. It creates one ephemeral CoreMIDI client, a uniquely named MIDI 1.0 virtual destination, and an output port. It sends a fixed note-on, note-off, CC, and realtime clock fixture only to the `MIDIEndpointRef` returned directly by that destination creation call. It never enumerates existing destinations, selects an endpoint by name, publishes a virtual source, or calls `MIDIReceivedEventList`. The destination callback records its received MIDI bytes in order and captures `DispatchTime.uptimeNanoseconds` separately from each CoreMIDI event's scheduled timestamp. All created resources are disposed before the result is returned. The command writes a JSON receipt locally and refuses to overwrite an existing path. Compilation and process execution have hard bounds; the callback wait defaults to 3 seconds and accepts 0.05–30 seconds.

The receipt includes the Swift source SHA-256, macOS, Swift and SDK versions, fixture, sent/received counts, callback observation values, scheduled timestamps and cleanup result. A pass requires a positive count of four events, exact bytes and order, and successful disposal of the client, port and endpoint. A no-callback timeout and process timeout are failures. This establishes only local CoreMIDI byte transport to the test's ephemeral virtual destination. It does not establish timing precision, MIDI Monitor/Max/Live transport, external routing, hardware delivery or device behavior. See Apple's documentation for [`MIDIDestinationCreateWithProtocol`](https://developer.apple.com/documentation/coremidi/mididestinationcreatewithprotocol%28_%3A_%3A_%3A_%3A_%3A%29), [`MIDISendEventList`](https://developer.apple.com/documentation/coremidi/midisendeventlist%28_%3A_%3A_%3A%29), and [`MIDIReceiveBlock`](https://developer.apple.com/documentation/coremidi/midireceiveblock).

On 2026-10-31: isolated Studio Capture 2-second requested run saved/exported zero events; passive TR-8S input 3-second requested run saved/exported 158 events. No output MIDI was sent. Existing 2022 `.mmon` parsed 1,000 events. The GUI adapter was tested after the user enabled Accessibility. Initial attempts exposed mixed category checkboxes enabling unrelated inputs; the final adapter expands categories and disables selected inputs without clicking mixed category controls. Source resolution and the final disconnected state are checked from saved documents. Evidence remains local, not in Git. Adapter hash in each receipt identifies the exact tested source.

Next: verify Live IAC input isolation; capture known note/CC fixtures from the M4L harness; add documented device response matchers per hardware profile; compare deterministic seeded runs; correlate run IDs with M4L transport logs. Prefer this CLI and file contract before adding an MCP wrapper.

## Format provenance

- [Snoize documentation](https://www.snoize.com/MIDIMonitor/docs): documents preserve settings and displayed events, configurable event buffer.
- [Snoize source](https://github.com/krevis/MIDIApps): MIDI Monitor `Document.swift` and SnoizeMIDI `Message.swift`, voice/system message subclasses define plist/NSKeyedArchive fields. The parser uses `plistlib` and a class-name whitelist; it never instantiates archived objects. Unsupported formats fail explicitly. Regression fixtures are synthetic; personal captures are not tracked.
