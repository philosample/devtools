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

New baselines use v2 and record the exact PID, bundle and process launch time,
the unique Settings window identity, complete snapshot bounds, observation time,
sorted output Sync rows, and the complete ordered Settings checkbox inventory.
They are point-in-time records and must be no older than five minutes for
ordinary actions. Legacy v1 baselines remain valid for ordinary isolate/restore
within the same age limit; they cannot authorize historical restore because
they do not contain the complete checkbox state. A caller may use
`--max-baseline-age-seconds` to choose a shorter interval.

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

For an expired original v2 baseline, first restore MIDI input Track, Sync and
Remote Control settings while Output Sync remains isolated. The input baseline
for this step must have been captured while Output Sync was already off. Capture
a fresh complete input inventory and restore it against that original input
baseline; then capture a new complete `live-midi-sync` inventory and use it
only as the current-state evidence for restoring the original output baseline:

```sh
python3 -m codex_devtools ax live-midi-inputs \
  --pid 58198 --expected-bundle-id com.ableton.live --expected-count 8 \
  --out /private/tmp/live-midi-inputs-current.json

python3 -m codex_devtools ax live-midi-inputs \
  --pid 58198 --expected-bundle-id com.ableton.live --expected-count 8 \
  --execute --action restore --baseline /private/tmp/live-midi-inputs-original.json \
  --historical-current-inventory /private/tmp/live-midi-inputs-current.json \
  --receipt /private/tmp/live-midi-inputs-restore.json

python3 -m codex_devtools ax live-midi-sync \
  --pid 58198 --expected-bundle-id com.ableton.live --expected-count 21 \
  --out /private/tmp/live-midi-sync-current-v2.json

python3 -m codex_devtools ax live-midi-sync \
  --pid 58198 --expected-bundle-id com.ableton.live --expected-count 21 \
  --execute --action restore --baseline /private/tmp/live-midi-sync-original-v2.json \
  --historical-current-inventory /private/tmp/live-midi-sync-current-v2.json \
  --receipt /private/tmp/live-midi-sync-restore.json
```

The original file is never edited or retimestamped. Historical restore accepts
only v2 original and current inventories; current evidence must be no older
than five minutes and must match the original PID, bundle, process launch time,
Settings window identity, complete snapshot bounds, and ordered checkbox
identity set. All non-output-Sync checkbox values must still equal their
original values. An originally-on Output Sync checkbox may be either zero or
one, allowing recovery after a partial isolation; an originally-off checkbox
must remain zero. The same checks and current-inventory hash are repeated by the
native helper immediately before each press. Historical evidence is restore
only; ordinary isolate and restore keep their original five-minute baseline
freshness rule.

This proves only the point-in-time Accessibility state of these settings for
one Live process. It does not observe MIDI packets, establish transport/play
state, prove output silence or delivery, or substitute for MIDI capture,
hardware, or full recall acceptance evidence. The labels and exact window
contract are based on the current English macOS Live Settings UI; other UI
localizations or versions must be observed and reviewed before use.
