# Guarded Live transport control

ax live-transport inspects one exact Live PID, bundle, and open Set URL. The
default is read-only:

    python3 -m codex_devtools ax live-transport \
      --pid 58198 --expected-bundle-id com.ableton.live \
      --set '/private/tmp/tr1000-instance-scope-20261101/TR1000-instance-scope-r61 Project/TR1000-tooling-recall-r71.als'

Mutation requires both --execute and an action. Play is limited to 1–15
seconds and requires the independently supplied output count of 21:

    python3 -m codex_devtools ax live-transport \
      --pid 58198 --expected-bundle-id com.ableton.live \
      --set '/private/tmp/tr1000-instance-scope-20261101/TR1000-instance-scope-r61 Project/TR1000-tooling-recall-r71.als' \
      --execute --action play --play-for-seconds 5 --expected-output-count 21 \
      --receipt /private/tmp/live-transport-play.json

The wrapper compiles the native helper before taking the fresh MIDI Output Sync
inspection. Play requires all 21 controls to be readable and off in that
inspection. The helper checks the fresh baseline age and re-reads the complete,
bounded Settings tree before closing only the unique Settings window with its
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
