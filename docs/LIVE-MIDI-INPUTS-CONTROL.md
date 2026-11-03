# Live MIDI input isolation and exact restoration

`ax live-midi-inputs` is a separate command/schema from `ax live-midi-sync`.
It inventories **every input row's Track, Sync and Remote flag** and every other
Settings checkbox. Inspect is read-only; actions require explicit `--execute`.
No MIDI is sent. No Set, output setting, app lifecycle, keyboard, AppleScript or
screen-coordinate action is involved.

## Discover and inspect

The operator must independently discover the actual input row count using a
complete `ax snapshot --pid PID --window-title Settings`, grouping the exact
`: MIDI Input: Track`, `: MIDI Input: Sync`, and `: MIDI Input: Remote` checkbox
descriptions by device name. The known output count is **not** an input count.
A missing/partial/ambiguous group, duplicate identity, unreadable checkbox,
disabled On input, wrong PID/bundle or truncated tree blocks use. Disabled Off
inputs may be recorded and are never pressed.

```sh
python3 -m codex_devtools ax live-midi-inputs \
  --pid PID --expected-bundle-id com.ableton.live --expected-count INPUT_ROWS \
  --out /private/tmp/live-inputs-original.json
```

The new baseline records process PID, bundle, name and launch date, observation
time, Settings window AX identity, complete bounds, input name/order, and every
checkbox's traversal index, AX identity, exact description, role, enabled state
and exact 0/1 value. `inventory_sha256` hashes the canonical baseline content
excluding that field; action receipts additionally pin the original file bytes.
The schema is `codex-devtools-live-midi-inputs-baseline-v1`. AX identities are
opaque CFHash values: native verification must establish their continuity
across helper invocations on the actual Live/macOS version. Identity drift or
hash collisions stop use, rather than authorizing name-only matching.

## Explicit actions

Keep the original Settings window foreground and focused, with its inventory
unchanged. Both actions require the same original baseline, at most 300 seconds
old; `--max-baseline-age-seconds` may impose a stricter positive limit.

```sh
python3 -m codex_devtools ax live-midi-inputs \
  --pid PID --expected-bundle-id com.ableton.live --expected-count INPUT_ROWS \
  --execute --action isolate --baseline /private/tmp/live-inputs-original.json \
  --receipt /private/tmp/live-inputs-isolate.json

python3 -m codex_devtools ax live-midi-inputs \
  --pid PID --expected-bundle-id com.ableton.live --expected-count INPUT_ROWS \
  --execute --action restore --baseline /private/tmp/live-inputs-original.json \
  --receipt /private/tmp/live-inputs-restore.json
```

Isolation presses only originally On input flags. Restoration accepts a partial
isolation and presses only originally On flags currently Off. Originally Off
inputs and all non-input checkboxes must keep their exact original values.
Immediately before each single press the native helper re-enumerates the full
Settings tree and checks original identities, traversal order, descriptions,
roles, enabled states, bounds and all expected values, allowing only known
prior input changes. It rechecks focus, target identity and baseline freshness
before pressing. A complete post-press inventory must match the requested
single change and unchanged non-target controls. There are no automatic
retries or automatic cleanup presses after a failure.

Each new receipt is reserved before work and updated before invoking a press;
an interrupted helper leaves that attempt `unknown`. Receipts record each
candidate's attempted/known/unknown state, baseline file hash, timestamps,
final observation when successful, and `manual_inspection_required`. Any
uncertain or stopped-after-attempt receipt requires manual inspection before
an explicit recovery decision. Do not blindly repeat isolation or restore.
A native preflight rejection may conservatively mark an invocation unknown;
no success claim is inferred from absent readback.

The tree is bounded to 64 windows, 1500 elements, depth 12, 512 checkboxes and
1–128 input groups (at most 384 candidate presses). Actions have an overall
180-second wall-time budget; each native invocation is bounded by the remaining
budget. The existing two-second AX read timeout remains in force. Unknown or
unavailable values never become zero. Refuse a stale restoration and inspect
manually rather than creating a new baseline from an isolated state.

## Coordinator's native verification after exact-head approval

1. Hold the single desktop lease, discover exact PID/bundle/input count and
   verify a complete Settings tree. Device View may be hidden to avoid the
   unrelated main-window AX cap. Do not change Spaces during the sequence.
2. Save a read-only baseline and a second read-only inventory. Compare identities,
   groups and all checkbox values; opaque AX identity continuity is unverified
   until this succeeds on the installed version.
3. Within the baseline age window, explicitly isolate with a new receipt. Require
   complete status, all three flags Off for every input and unchanged outputs
   and unrelated Settings checkboxes. Independently save an AX snapshot.
4. Explicitly restore using the **original** baseline and another new receipt.
   Require exact original states for every recorded checkbox and save an
   independent AX snapshot. Inspect any failure; do not retry blindly.
5. Save exact source commit, command arguments, original baseline hash, receipts
   and independent snapshots locally. This establishes only native AX settings
   evidence. MIDI wire/hardware evidence remains a separate gate.

Source tests use mixed input/output matrices and fake native invocations, plus
Swift typechecking. They do not establish installed Live behavior, rendering,
MIDI silence, packet delivery or hardware acceptance.
