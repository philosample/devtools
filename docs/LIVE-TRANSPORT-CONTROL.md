# Guarded Live transport control

ax live-transport inspects one exact Live PID, bundle, and open Set URL. The
default is read-only:

    python3 -m codex_devtools ax live-transport \
      --pid 58198 --expected-bundle-id com.ableton.live \
      --set '/private/tmp/tr1000-instance-scope-20261101/TR1000-instance-scope-r61 Project/TR1000-tooling-recall-r71.als'

Mutation requires both --execute and an action. Play is limited to 1–15
seconds and requires an independently supplied output row count from 1 to 128.
The example uses 23; supply the exact count established for the target Live
Settings inventory:

    python3 -m codex_devtools ax live-transport \
      --pid 58198 --expected-bundle-id com.ableton.live \
      --set '/private/tmp/tr1000-instance-scope-20261101/TR1000-instance-scope-r61 Project/TR1000-tooling-recall-r71.als' \
      --execute --action play --play-for-seconds 5 --expected-output-count 23 \
      --receipt /private/tmp/live-transport-play.json

The read-only `inspect` action reuses a compiled helper from the private
`~/Library/Caches/codex-devtools/live-transport` cache. Its content-addressed
identity includes the Swift source hash, resolved compiler path, compiler hash
and version, selected Xcode developer directory, macOS SDK path/version, fixed
compiler arguments, OS version, and architecture.
The source and toolchain identity are checked again across compilation. Cache
entries are published as complete directories and include a manifest with the
compiled binary hash. Before every invocation, the wrapper requires owner-only
regular files and directories and verifies the binary hash. A symlink, unsafe
mode, malformed manifest, or hash mismatch stops inspection without running
that helper. This protects against accidental or other-user cache tampering; it
does not attest against a process running as the same user that can alter the
cache between verification and execution. No transport observations are cached:
each `inspect` still starts a fresh native process and validates its current
PID, bundle, exact Set URL, and receipt. Play, Stop, focus-window, and
position-window continue compiling into a fresh temporary directory each time.

Play requires exactly the independently supplied number of output
Sync controls to be readable and all off in that inspection. The helper checks
the fresh baseline age and re-reads the complete, bounded Settings tree before
closing only the unique Settings window with its
unique enabled AXCloseButton. It confirms the exact Set window is still
focused, and requires Play, Link, Arrangement Record, and Session Record off
before starting.

After one Play press, the native helper uses a monotonic deadline and attempts
one Stop at that deadline from its own process. It verifies the exact Set URL,
Live PID, foreground and focused Set window, current desktop visibility, and
the exact enabled Stop button before pressing; it then verifies Play off. The
timed Stop runs even if the Python command is waiting for the helper. If focus,
process, or Set identity changes, the helper stops and marks the result for
manual inspection. It records any Link or recording state seen at the deadline
and asks for manual review if one is on. It never retries an uncertain action.

An explicit Stop can end an already playing Set without first closing Settings:

    python3 -m codex_devtools ax live-transport \
      --pid 58198 --expected-bundle-id com.ableton.live \
      --set '/private/tmp/tr1000-instance-scope-20261101/TR1000-instance-scope-r61 Project/TR1000-tooling-recall-r71.als' \
      --execute --action stop --receipt /private/tmp/live-transport-stop.json

The helper refuses to start Play if Link or either recording control is on.
Explicit Stop remains available for the exact Set even when those controls are
on. Stop is a verified no-op if Play is already off. Receipts are new
files and report each action as not attempted, known, or unknown. A receipt
requiring manual inspection must be inspected before another action.

## Focus one exact Live-owned window

`focus-window` first reads a fresh `ax snapshot` and exact-set inspection. Use
one snapshot from the target PID and copy its `observed_at_utc`, target
`launch_date`, and the selected window's `identity` into the preflight call.
The identity is the decimal CFHash value shown by the snapshot. Duplicate
titles are allowed only when that exact identity and current desktop geometry
select one window. The main Set window must also resolve uniquely by its exact
AXDocument URL. A child editor may have no AXDocument; if present, it must be a
valid local file URL, and its exact string is pinned and rechecked unchanged.
This does not assert that the child document belongs to the selected Set.

    python3 -m codex_devtools ax live-transport \
      --pid 58198 --expected-bundle-id com.ableton.live --set /path/current.als \
      --action focus-window --expected-generation 1794118619511410 \
      --snapshot-observed-at-utc '2026-11-08T06:20:00.000Z' \
      --snapshot-launch-date '2026-11-08T06:16:59.511Z' \
      --window-identity 1685072705 --receipt /private/tmp/focus-preflight.json

Review the preflight receipt and compute its SHA-256 before execution. The
receipt expires after 30 seconds; execution pins its hash and rechecks Live
generation, exact Set-window identity, selected AX identity, geometry, desktop
window number and focus before and after activation:

    python3 -m codex_devtools ax live-transport \
      --pid 58198 --expected-bundle-id com.ableton.live --set /path/current.als \
      --action focus-window --execute --expected-generation 1794118619511410 \
      --preflight-receipt /private/tmp/focus-preflight.json \
      --preflight-sha256 '<reviewed SHA-256>' \
      --receipt /private/tmp/focus-result.json

The operation only activates Live and calls AXRaise on the pinned window. It
does not open, close, click, press keys, change preferences, inspect transport
controls, or launch an app. Missing or stale snapshots, process or Set changes,
ambiguous geometry, minimized/off-desktop targets and unrecognized dialogs or
sheets are refused. The known Window Sharing overlay retains the existing
exact-signature allowlist. This helper has source and mocked-policy checks;
those do not establish a native AX focus result.

## Position one exact Live-owned window

`position-window` moves one AX window to a requested top-left X/Y while
preserving its size. First use a fresh `ax snapshot` for the exact PID and
copy its observation time, launch date, and selected window CFHash identity.
The read-only preflight also verifies the exact current Set, the local child
document when present, current desktop visibility, and that AXPosition is
settable. The entire requested rectangle must fit on one currently active
display. The preflight records the requested destination and original geometry.

    python3 -m codex_devtools ax live-transport \
      --pid 58198 --expected-bundle-id com.ableton.live --set /path/current.als \
      --action position-window --expected-generation 1794118619511410 \
      --snapshot-observed-at-utc '2026-11-08T06:20:00.000Z' \
      --snapshot-launch-date '2026-11-08T06:16:59.511Z' \
      --window-identity 1685072705 --position-x 1000 --position-y 350 \
      --receipt /private/tmp/position-preflight.json

Review that receipt and its SHA-256. Execute within 30 seconds using the same
PID, bundle, Set, and inspected process generation:

    python3 -m codex_devtools ax live-transport \
      --pid 58198 --expected-bundle-id com.ableton.live --set /path/current.als \
      --action position-window --execute --expected-generation 1794118619511410 \
      --preflight-receipt /private/tmp/position-preflight.json \
      --preflight-sha256 '<reviewed SHA-256>' \
      --receipt /private/tmp/position-result.json

Execution pins the destination from the hashed preflight, rechecks process,
Set, window identity, document, geometry, setter availability, and display
bounds, then issues exactly one AXPosition setter. It verifies the same window
at the requested X/Y with unchanged size and current-desktop visibility. It
does not activate Live or use clicks, keys, transport, preferences, devices, or
SDK calls. Any uncertain or partial result requires manual inspection before
another action; the helper never retries the position write. CLI, Swift policy,
and mocked caller checks do not establish a native positioning result.

## Recorded native findings

On 2026-11-02, a read-only inspection of the exact R71 Set in Live PID 58198
reported Play, Link, Arrangement Record, and Session Record off. An explicit
idle Stop stopped in preflight with “exact Live PID did not become frontmost”;
the receipt recorded all actions not attempted and manual inspection false, so
no transport or preference action was issued. A separate fresh Swift process
immediately reported `com.ableton.live` PID 58198 as frontmost. This is
consistent with a stale foreground observation in the helper after activation,
but does not prove the cause. The helper now pumps the current run loop during
bounded activation/focus waits and while waiting for the monotonic Stop
deadline. It does not change Spaces. This source correction still needs a
reviewed native retest before relying on it.

On 2026-11-02, a later run passed idle Stop with Play already off. A bounded
three-second Play then recorded Settings close and Play as known, attempted
Stop at its deadline, but read Play still on immediately afterward and marked
the Stop uncertain for manual review. A fresh independent inspection shortly
afterward read Play off and Link/recording controls off. This shows that the
immediate transport readback did not settle with the Stop press. The helper now
waits up to two seconds for Play-on readback, capped by the original Play
deadline, and up to two seconds after Stop for Play-off readback. Each read
rechecks PID, exact Set URL, focus, desktop visibility, unexpected dialogs, and
control identity while pumping the current run loop. Once Play AXPress is
attempted, including when its outcome is uncertain, the helper keeps the
original Stop deadline cleanup obligation active. It never retries Play or
Stop. The delayed observation is not evidence of MIDI or audible output.

This is a bounded Accessibility control for the current English Live UI and
macOS behavior. It establishes only the observed transport and preference
states of the exact Live Set. It does not prove MIDI packets, audible output,
external routing, hardware response, M4L recall, or full clean-recall
acceptance. CGWindowList current-desktop visibility checks do not switch or
create Spaces; a window that is not proven on the current desktop stops the
action.

## Exact macOS window-sharing overlay exception

The app-window guard recognizes one captured macOS window-sharing overlay
shape: an `AXWindow` with `AXDialog` subrole, title `Window`, identifier
`_NS:8`, and exactly one direct child, an enabled `AXButton` titled
`WindowSharingSessionButton` with no exposed AX identifier. This was derived
from a read-only Live window snapshot on 2026-11-05; the AX title and internal
identifier are version-specific observations, not stable public Apple API
contracts. If any field is absent or changes, the guard rejects it as an
unexpected dialog. Sheets, popovers, system dialogs, floating windows, ordinary
dialogs, and additional child controls remain rejected. The exception does not
remove the window from the bounded app-window inventory or change exact Set
URL, focus, visibility, or transport-control checks. It does not diagnose a
Space-placement cause.
