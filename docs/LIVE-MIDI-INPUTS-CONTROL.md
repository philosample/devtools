# Live MIDI input isolation and exact restoration

`ax live-midi-inputs` is a separate command/schema from `ax live-midi-sync`.
It inventories **every input row's Track, Sync and Remote Control flag** and
every other Settings checkbox. Inspect is read-only; actions require explicit `--execute`.
No MIDI is sent. No Set, output setting, app lifecycle, keyboard, AppleScript or
screen-coordinate action is involved.

## Discover and inspect

The operator must independently discover the actual input row count using a
complete `ax snapshot --pid PID --window-title Settings`, grouping the exact
`: MIDI Input: Track`, `: MIDI Input: Sync`, and
`: MIDI Input: Remote Control` checkbox descriptions by device name. `Remote Control` is the raw English AX label,
matching the existing six-switch verifier; the shorter `Remote` alias is rejected.
The known output count is **not** an input count.
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
unchanged. By default, both actions require the original baseline to be at most
300 seconds old; `--max-baseline-age-seconds` may impose a stricter positive
limit. A stale baseline is refused by default.

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

### Explicit historical restore after the original baseline expires

If the original baseline has expired, keep that file unchanged and capture a
new complete read-only inventory immediately before the restore. The separate
`--historical-current-inventory` option is valid only with `--execute
--action restore`; it opts into this reconciliation path. The supplied current
inventory must be at most 300 seconds old (or the stricter configured limit),
have the same PID, bundle, process generation, Settings window, bounds, and
complete ordered checkbox identities as the original baseline, and differ only
where an originally On MIDI input Track, Sync, or Remote Control flag is now
Off. Originally Off inputs, currently On original inputs, output controls, and
all other checkboxes must retain their original values.

```sh
python3 -m codex_devtools ax live-midi-inputs \
  --pid PID --expected-bundle-id com.ableton.live --expected-count INPUT_ROWS \
  --out /private/tmp/live-inputs-restore-current.json

python3 -m codex_devtools ax live-midi-inputs \
  --pid PID --expected-bundle-id com.ableton.live --expected-count INPUT_ROWS \
  --execute --action restore \
  --baseline /private/tmp/live-inputs-original.json \
  --historical-current-inventory /private/tmp/live-inputs-restore-current.json \
  --receipt /private/tmp/live-inputs-historical-restore.json
```

The receipt records hashes for both the immutable original baseline and the
fresh current observation. Before each press, the native helper verifies the
same process generation, focus and complete current checkbox inventory, checks
the remaining action deadline, and allows only that one originally On MIDI
input flag to change from Off to On. It reads back the full inventory after the
press. It never retimestamps the original baseline. Without the separate
current-inventory option, expired-baseline behavior is unchanged and refuses
the action.

The tree is bounded to 64 windows, 1500 elements, depth 12, 512 checkboxes and
1–128 input groups (at most 384 candidate presses). Actions have an overall
180-second wall-time budget; each native invocation is bounded by the remaining
budget. The existing two-second AX read timeout remains in force. Unknown or
unavailable values never become zero. The default path refuses a stale
restoration and requires the explicit historical reconciliation path above.

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
