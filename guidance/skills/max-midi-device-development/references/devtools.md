# Devtools entrypoints

Private source: https://github.com/philosample/devtools . Locate an existing checkout through the project/workspace registry; do not install packages or clone another copy just to run a command when it is already available. From the checkout:

```sh
python3 -m codex_devtools max inspect /absolute/path/to/file.mxf --json
python3 -m codex_devtools max extract /absolute/path/to/file.mxf --out /explicit/new/output
python3 -m codex_devtools max validate /absolute/path/to/device.amxd --json
python3 -m codex_devtools max preview /absolute/path/to/device.maxpat --out /explicit/new/layout.svg
python3 -m codex_devtools hash manifest /absolute/source/root --out /explicit/manifest.json
python3 -m unittest discover -s tests -v
python3 -m codex_devtools runtime node --json
```

Inspection and graph validation are static evidence only. Extraction preserves archived bytes and refuses existing destination files. Graph checks do not prove object semantics or runtime behavior. Keep the installed app/user library read-only unless the task specifically authorizes deployment. Read the repo README for the actual current command interface.

For source layout review, `max preview` reports root/inline patcher geometry, invalid rectangles, bounds and rectangle intersections, with source hashes and optional deterministic SVG. Omit `--out` for a read-only JSON report. It executes no Max/JSUI code and does not reproduce native widget paint, text metrics, SVG backgrounds or runtime state. Overlapping background rectangles can be intentional; inspect the actual roles before treating intersections as defects. Use device-owned preview generators for faithful custom artwork. See devtools `docs/MAX-PREVIEW.md`.

If Node is missing from PATH, use `runtime node --json` before installing or repeatedly searching: it checks existing PATH and Live's bundled Max Node, verifies its version, and reports the selected executable/provenance. See devtools `docs/RUNTIMES.md` for an alternate app bundle. Execute the quoted returned path for focused JS suites; Node execution does not prove Max/Live behavior.

For `midi endpoints/template/capture/export/report`, read devtools `docs/MIDI-CAPTURE.md` and use the `midi-capture-testing` skill. These commands may initially be in a reviewed feature worktree; check the selected revision before invoking. Capture is explicit UI automation; export/report are offline. Never infer Live-to-IAC isolation from an online endpoint or hardware state from a Thru echo.

After an explicit `deploy plan`/`deploy apply`, verify the saved plan with `python3 -m codex_devtools deploy verify /absolute/saved-plan.json`. Require `passed: true` and a positive total with every listed file matching. This read-only check compares current source and deployed bytes with the planned source hashes and rejects missing/drifted files or symlink substitutions; it proves file deployment, not a loaded Max instance. An unchanged AMXD hash does not establish unchanged external sidecars: attribute the complete bundle and its loaded path.

For one saved Live Set, `python3 -m codex_devtools ableton inspect-set-device-refs /absolute/test.als --expect-amxd-path /absolute/candidate.amxd --expect-amxd-sha256 <sha256>` checks exact saved references and current disk bytes. It does not identify the active Live/Max instance. Keep Set-path, active-instance and runtime-receipt evidence distinct. These entrypoints were verified in devtools PRs #18 and #21; use a checkout containing them rather than silently falling back to a stale canonical checkout.

For an already loaded, explicitly opted-in test device, prefer the local mailbox tools over repeated UI clicks: `python3 -m codex_devtools harness discover`, `harness status`, and `harness submit`. Read `docs/HARNESS-CONTROL.md` for required arguments and the owning fixture's command contract. Select an exact descriptor and independently prepared artifact/full-component pins; never select the first discovered instance. Discovery freshness is not process-liveness or loaded-artifact proof. A command ACK proves only the domain's documented completion condition, not native MIDI delivery.

The helper is transport plumbing: a Max consumer must correlate request/sequence with actual driver completion. An enqueued command or synchronous JavaScript callback return is insufficient. Preserve asynchronous `node.script` ordering in tests; a synchronous mock can hide a missing handshake. Treat `unknown_effect` as unresolved effects: do not automatically retry, reset ownership, or infer cancellation. Reconcile using the consumer's documented procedure before further commands. Studio-control PR #197 is the reviewed PERF consumer example; its offline tests and staged package are not a hosted-runtime pass.

## Live Set switching

For switching to an existing Set without quitting Live, use `ax live-set-open`. It defaults to read-only preflight and requires the exact Live PID/bundle/app, the source's current AX document URL, and independently reviewed hashes for both existing `.als` files. The destination is not created by this command. Read `docs/LIVE-SET-OPEN.md` for the exact file, focus and receipt guards.

```sh
python3 -m codex_devtools ax live-set-open \
  --pid <exact-PID> --expected-bundle-id com.ableton.live \
  --source-set /absolute/current.als --source-sha256 <source-sha256> \
  --destination-set /absolute/existing.als --destination-sha256 <destination-sha256> \
  --app "/Applications/Exact Live.app"
```

Inspect the `ready` receipt before repeating the exact invocation with `--execute`, and only when the requested action is authorized. To save an explicitly owned source before switching, include `--save-owned-source --backup-root /absolute/private/backups` in both preflight and execution. The existing backup directory must be private, outside Git and current-user-owned; execution records the exact pre-save bytes before attempting one ordinary Save. This does not create a Save As copy or assert that unsaved in-memory state was already saved. A prompt, mismatch, stopped/unknown receipt or timeout requires inspection and reconciliation; do not accept prompts or retry an uncertain action.

`live-set-open` does not inspect transport. Before execution, independently run the read-only transport inspector for the exact source Set and confirm Play, arrangement/session record and Link are all off. Read `docs/LIVE-TRANSPORT-CONTROL.md` for its scope and receipt fields:

```sh
python3 -m codex_devtools ax live-transport \
  --pid <exact-PID> --expected-bundle-id com.ableton.live \
  --set /absolute/current.als
```

After any Set-switch attempt, invalidate SDK/LOM and device-bound M4L handles even when Live retains the same PID. Rebind through fresh observations and verify the exact `live_set.file_path`, full graph/session, component pins, routing and quiet state. `opened` proves the same-process document URL and file observations only; it does not prove M4L recall, silence, dependency resolution, wire events or hardware behavior.

For a separate clean quit/reopen lifecycle test that requires a new process, use `ax live-set-lifecycle` and read `docs/LIVE-SET-LIFECYCLE.md`. It saves a new sibling through Save As, quits normally, and requires a new PID for reopen; its receipt proves host lifecycle, not recall. A stopped/unknown receipt requires current-state inspection before any repeat. Do not restart Live solely to recover a CUA or SDK bridge.

```sh
python3 -m codex_devtools ax live-set-lifecycle \
  --pid <exact-PID> --expected-bundle-id com.ableton.live \
  --source-set /absolute/source.als \
  --destination-set /absolute/new-sibling.als \
  --app "/Applications/Exact Live.app"
```

Review the `ready` receipt before repeating the exact command with `--execute` when a process-boundary recall test is authorized. This path intentionally saves a new sibling, quits the current process normally, and reopens it through `NSWorkspace`; it is not a recovery step for an SDK, CUA or mailbox bridge. After `reopened`, verify M4L path, dependencies, silent state recall and deterministic continuation independently.
