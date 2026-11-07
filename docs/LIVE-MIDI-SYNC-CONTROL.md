# Live MIDI Output Sync control

`ax live-midi-sync` inspects one exact Live process and its complete, bounded
Settings tree. It matches only enabled `AXCheckBox` controls whose descriptions
end in `: MIDI Output: Sync`. The caller supplies the expected app bundle ID and
the independently observed number of output rows; missing, extra, duplicate,
renamed, disabled, unreadable, or truncated rows stop the operation.

Inspect is the default and never changes Live:

```sh
python3 -m codex_devtools ax live-midi-sync \
  --pid 58198 --expected-bundle-id com.ableton.live --expected-count 21 \
  --out /private/tmp/live-midi-sync-baseline.json
```

The baseline records the exact PID and bundle, unique Settings window, complete
snapshot bounds, observation time, and sorted row names and values. It is a
point-in-time record and must be no older than five minutes for actions. A
caller may use `--max-baseline-age-seconds` to choose a shorter interval.

Actions require explicit `--execute`, the original caller-supplied baseline,
the same independently supplied expected count, and a new receipt path:

```sh
python3 -m codex_devtools ax live-midi-sync \
  --pid 58198 --expected-bundle-id com.ableton.live --expected-count 21 \
  --execute --action isolate --baseline /private/tmp/live-midi-sync-baseline.json \
  --receipt /private/tmp/live-midi-sync-isolate.json

python3 -m codex_devtools ax live-midi-sync \
  --pid 58198 --expected-bundle-id com.ableton.live --expected-count 21 \
  --execute --action restore --baseline /private/tmp/live-midi-sync-baseline.json \
  --receipt /private/tmp/live-midi-sync-restore.json
```

When a foreground/focus guard refuses before or between presses, the stopped
receipt may include an optional `focus_diagnostics` object. It records the
target process `isActive` result, the observed frontmost PID, and the focused
AX window's role, title, and exact match against the selected Settings window.
Each fact is tagged `observed`, `unavailable`, or `error`; missing AX values,
unsupported attributes, and AX error codes stay explicit. The diagnostic is
bounded to the focused window and these facts; it does not include the Settings
tree or checkbox values.

These are separate read-only observations captured immediately after refusal,
not an atomic snapshot or a claim about which value caused an earlier focus
change. They do not relax any guard, retry, activate Live, press a control, or
change `manual_inspection_required` semantics. A receipt without this optional
object remains valid under the existing v1 receipt contract.

Immediately before each press, the native helper re-enumerates the complete
bounded Settings tree and requires the same Settings window and output controls,
with every name, description, enabled checkbox state, AX element identity, and
current value still matching. It uses the freshly observed target element for
that press. The helper also rechecks the caller-selected baseline age limit
(default five minutes) before every press, so time spent starting the helper or
completing earlier actions cannot make a stale baseline authorize a later one.
It isolates only baseline-on rows, pressing each once and reading it back.
Restore requires every current row to be either its original value or a
partially isolated zero; it restores only original-on rows and never changes
original-off rows. The target Live process and Settings window must remain the
frontmost/focused target. Any unexpected state or uncertain press stops further
actions. Receipts are new files, never overwritten. A stopped receipt marked
`manual_inspection_required` must be inspected before another action; there is
no retry loop.

This proves only the point-in-time Accessibility state of these settings for
one Live process. It does not observe MIDI packets, establish transport/play
state, prove output silence or delivery, or substitute for MIDI capture,
hardware, or full recall acceptance evidence. The labels and exact window
contract are based on the current English macOS Live Settings UI; other UI
localizations or versions must be observed and reviewed before use.
