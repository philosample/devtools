---
name: midi-capture-testing
description: Capture, export and assert MIDI evidence with Snoize MIDI Monitor and the local devtools suite, or coordinate M4L transport logs with MIDI captures.
---

# MIDI capture testing

Use the existing private `philosample/devtools` checkout located through `~/Code/WORKSPACES.local.json`. Read that checkout's `docs/MIDI-CAPTURE.md` for exact CLI commands and current tested limitations. If the commands are on an unmerged branch, locate its owning worktree and report the revision; do not silently use stale canonical main or install another copy. Scripts live in devtools, not duplicated in this skill.

Choose the cheapest evidence that answers the acceptance check:

- Saved `.mmon` available: use `midi export` or `midi report` without opening UI.
- Need only native byte-transport evidence: use `python3 -m codex_devtools midi loopback --out <new-local-receipt.json>` on macOS. It sends four fixed events only to its own temporary destination and checks exact bytes/order plus cleanup. It does not test MIDI Monitor, Max/M4L/Live, IAC, external routing or hardware. Reuse its saved source-pinned receipt while that native component is unchanged.
- Need a new MIDI observation: `midi endpoints`, create a local exact-source template, then explicit `midi capture` to a new run directory. Keep the raw document, preflight document, JSON/CSV and receipt together outside Git.
- Need Live transport/tempo evidence: use max-host's M4L harness and validator inside Live; Node handles logging only, the Live API remains in Max. Follow that repository's test instructions. Offline synthetic logs are not a hosted test.

Before capture, declare expected source/direction, bytes/events, success conditions and evidence level. Require positive event counts when silence is not the intended result. Check receipt status, source identity and buffer warning before interpreting assertions. A successful file save or empty capture is not a behavioral pass. Preserve exact source/artifact hashes, application versions and run identifiers in the issue/PR; personal captures and machine endpoint IDs stay local.

The AppleScript adapter requires macOS Accessibility access for its runner. If blocked, record the exact error and ask the user to grant it; do not broaden permissions yourself. UI-driven capture has boundary uncertainty and input-disconnect overhead. Do not use its requested duration as a precision timing measurement. Use known timestamp units and per-origin streams; output-spy scheduled timestamps do not necessarily describe physical arrival timing.

For Studio Capture IAC tests, verify Live input Track/Sync/Remote are off before any synthetic send; enable only the required output Track or explicit Sync case, with one clock authority. Do not infer isolation from bus existence. Preserve existing Sets and monitor windows. Never toggle a mixed source category to disconnect: it can enable unrelated inputs; use the reviewed adapter and verify resolved/final source settings.

Before Live playback tests, inspect global output Sync separately from test-track No Output. Follow the reviewed checkout's `docs/MIDI-CAPTURE.md` playback-isolation procedure; preserve original settings, temporarily change only authorized switches, stop transport before exact restoration and verify it even after failure. Reuse `python3 -m codex_devtools.live_sync_ax` for saved before/isolated/restored AX comparisons with an independently verified output count. Its saved-value report does not establish current isolation, complete inventory or MIDI silence; control-surface scripts remain separate.

Separate output-path capture, MIDI Thru echo, documented device acknowledgement/readback and verified applied device state. A hardware return is useful evidence only for what its documented semantics establish. Do not invent response decoders. All new musical devices must remain M4L-compatible under the user's M4L-only license.

For a repeatable failure, add a focused regression at the owning layer. Run the focused check first, then required integration checks once. Reuse saved evidence until the source, artifact, environment or unresolved concern changes; don't rerun every unrelated suite or drive the UI for checks the logs can answer.
