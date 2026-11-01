# MIDI capture tools

Requires macOS, Snoize MIDI Monitor (tested 1.5.4), Python 3 and Accessibility permission for the process launching `osascript`. Endpoint inventory/template creation additionally uses Apple's Swift developer tools. No third-party Python dependencies. Offline export/report does not launch an app.

```sh
python3 -m codex_devtools midi endpoints
python3 -m codex_devtools midi template --source 'IAC Driver Studio Capture' --out /absolute/local/capture-template.mmon
python3 -m codex_devtools midi capture --template /absolute/local/capture-template.mmon --seconds 10 --out /absolute/local/new-run
python3 -m codex_devtools midi loopback --out /absolute/local/native-loopback.json --timeout 3
python3 -m codex_devtools midi export /absolute/local/new-run/capture-new-run.mmon --format csv --out /absolute/local/export.csv
python3 -m codex_devtools midi report /absolute/local/new-run/capture-new-run.mmon --receipt /absolute/local/new-run/receipt.json --expect /absolute/local/expectations.json
```

The template resolves exactly one online CoreMIDI source by name/displayName. Names can be ambiguous: inventory first. Templates contain machine-specific endpoint IDs and belong outside Git. Recreate them after MIDI configuration changes. A single input source is the default: multi-source/spying documents must be configured and saved in MIDI Monitor deliberately before use as templates. Capture opens a fresh copy, retains a preflight saved document, sets the event buffer (100,000 default), clears events, waits, disconnects that document's sources, saves and closes. It does not clear existing user windows, alter global preferences, send MIDI, or configure Live.

Each new run directory contains the original `.mmon`, `preflight.mmon`, parsed `events.json`/`events.csv`, and `receipt.json`. The receipt records configured and resolved source settings/IDs, requested interval, buffer size, elapsed time, app version when available, and SHA-256 hashes for the template, AppleScript adapter, native capture, preflight, and exports. Failure receipts hash any partial artifacts that were saved. Output directories and export files are created exclusively; existing paths are refused.

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

## Studio Capture IAC setup

Create a dedicated port in Audio MIDI Setup → MIDI Studio → IAC Driver. The studio machine now has `Studio Capture`, with the IAC driver online. Bringing IAC online also exposes pre-existing ports; no old ports were removed. CoreMIDI inventory verified the new source.

For a disposable Live test Set, disable **all input Track/Sync/Remote** switches for this bus. Enable **output Track** only for note/CC tests; keep **output Remote and Sync off**. Enable output Sync explicitly for clock tests with a single clock authority. Route only the test track to this bus, and select its source in MIDI Monitor. Verify switch state after reconnects. These Live settings are a required setup gate: they have not yet been changed or verified by this implementation because Live accessibility timed out. Do not send synthetic probes until the input isolation is verified.

IAC proves the Live output path. A physical device's MIDI output can provide stronger evidence if it returns a documented acknowledgement, parameter dump/readback or other device-generated response. A MIDI Thru echo proves a return route, not that the device applied the parameter. Record firmware, physical ports, response semantics and request/response matching separately. Sound/state checks remain necessary where the protocol has no readback.

## Runtime evidence and next improvements

### Native isolated CoreMIDI loopback

`midi loopback` compiles a small Swift program with the installed Apple toolchain and runs a bounded, same-process transport check. It creates one ephemeral CoreMIDI client, a uniquely named MIDI 1.0 virtual destination, and an output port. It sends a fixed note-on, note-off, CC, and realtime clock fixture only to the `MIDIEndpointRef` returned directly by that destination creation call. It never enumerates existing destinations, selects an endpoint by name, publishes a virtual source, or calls `MIDIReceivedEventList`. The destination callback records its received MIDI bytes in order and captures `DispatchTime.uptimeNanoseconds` separately from each CoreMIDI event's scheduled timestamp. All created resources are disposed before the result is returned. The command writes a JSON receipt locally and refuses to overwrite an existing path. Compilation and process execution have hard bounds; the callback wait defaults to 3 seconds and accepts 0.05–30 seconds.

The receipt includes the Swift source SHA-256, macOS, Swift and SDK versions, fixture, sent/received counts, callback observation values, scheduled timestamps and cleanup result. A pass requires a positive count of four events, exact bytes and order, and successful disposal of the client, port and endpoint. A no-callback timeout and process timeout are failures. This establishes only local CoreMIDI byte transport to the test's ephemeral virtual destination. It does not establish timing precision, MIDI Monitor/Max/Live transport, external routing, hardware delivery or device behavior. See Apple's documentation for [`MIDIDestinationCreateWithProtocol`](https://developer.apple.com/documentation/coremidi/mididestinationcreatewithprotocol%28_%3A_%3A_%3A_%3A_%3A%29), [`MIDISendEventList`](https://developer.apple.com/documentation/coremidi/midisendeventlist%28_%3A_%3A_%3A%29), and [`MIDIReceiveBlock`](https://developer.apple.com/documentation/coremidi/midireceiveblock).

On 2026-10-31: isolated Studio Capture 2-second requested run saved/exported zero events; passive TR-8S input 3-second requested run saved/exported 158 events. No output MIDI was sent. Existing 2022 `.mmon` parsed 1,000 events. The GUI adapter was tested after the user enabled Accessibility. Initial attempts exposed mixed category checkboxes enabling unrelated inputs; the final adapter expands categories and disables selected inputs without clicking mixed category controls. Source resolution and the final disconnected state are checked from saved documents. Evidence remains local, not in Git. Adapter hash in each receipt identifies the exact tested source.

Next: verify Live IAC input isolation; capture known note/CC fixtures from the M4L harness; add documented device response matchers per hardware profile; compare deterministic seeded runs; correlate run IDs with M4L transport logs. Prefer this CLI and file contract before adding an MCP wrapper.

## Format provenance

- [Snoize documentation](https://www.snoize.com/MIDIMonitor/docs): documents preserve settings and displayed events, configurable event buffer.
- [Snoize source](https://github.com/krevis/MIDIApps): MIDI Monitor `Document.swift` and SnoizeMIDI `Message.swift`, voice/system message subclasses define plist/NSKeyedArchive fields. The parser uses `plistlib` and a class-name whitelist; it never instantiates archived objects. Unsupported formats fail explicitly. Regression fixtures are synthetic; personal captures are not tracked.
