# Guarded Live normal quit

`ax live-quit` inspects one explicitly identified Ableton Live process and its
current Set, then optionally requests one normal application termination. The
default is read-only. It never launches Live, writes preferences, sends a force
quit, or accepts a save/discard dialog.

Run the read-only preflight first, using a process generation token from a
recent Live inspection and the reviewed pin for the exact saved `.als` file:

```sh
python3 -m codex_devtools ax live-quit \
  --pid 92330 --expected-bundle-id com.ableton.live \
  --app "/Applications/Ableton Live 12 Beta.app" \
  --expected-generation 1793752334412305 \
  --set "/private/tmp/Owned Project/Owned.als" \
  --source-sha256 <reviewed-sha256> --source-device <device> --source-inode <inode>
```

Only after reviewing a `ready` receipt, repeat the exact command with
`--execute`. A successful execution reports `requested`, `accepted`, and
`terminated` separately. The wrapper starts one 25-second monotonic deadline at
command entry and passes that absolute deadline into Swift; helper startup,
Set pinning, AX inspection, the final guard immediately before
`NSRunningApplication.terminate()`, and termination readback share it. Set
hashing happens inside the deadline-bounded helper process. Each AX request is
capped by the time remaining. Python and Swift read the same Darwin
`CLOCK_MONOTONIC` coordinate; a clock-only compiled probe is covered by the
focused tests. A refusal before termination is `stopped/not_attempted`. After
the request, the helper observes only the target process's termination state
under that same deadline. AX window disappearance during shutdown does not
establish termination or a prompt; if the process is still running at the
deadline, the result is `stopped/unknown` and requires manual inspection before
any retry.

The helper verifies the PID, `com.ableton.live` bundle, exact `.app` and
`Contents/MacOS/Live` paths, and launch-date generation token. It opens the
selected Set without following symlinks and confirms its regular-file status,
stable SHA-256, device and inode. It requires the exact current `AXDocument`
URL, one matching Set window, no modal/dialog/sheet/popover windows, and
readable stopped Play, Link, Arrangement Record, and Session Record controls.
Unsupported or valueless `AXDescription` attributes on unrelated tree elements
are skipped while the bounded traversal continues; the four exact control
descriptions, identifiers and stopped values remain required. It repeats all
of those checks before the single normal quit request. No AX activation or
other UI action is sent. A pre-request refusal reports
`not_attempted`; a dialog or timeout after the request reports `unknown` and
requires manual inspection.

The Set hash proves only which saved disk file was selected. It does not prove
that Live's in-memory document matches those bytes or that there is no unsaved
work. The caller must provide a pre-saved Set and establish that condition
independently. If an unsaved-work prompt appears, this command leaves it alone
and reports the quit outcome as unknown.

The structured JSON receipt is written to stdout. It records the process and
Set pins, observed transport state, normal quit request/acceptance/termination,
and whether manual inspection is required. It records `force_quit: false`.
Malformed or stale pins stop before termination. A native timeout after
`--execute` is conservatively reported with an unknown request/termination
outcome, even if Live may have terminated at the same time.

Policy tests and Swift typechecking are source-only; they do not open, quit, or
otherwise control Live:

```sh
python3 -m unittest tests.test_live_quit -v
swiftc -framework AppKit -framework ApplicationServices -framework CoreGraphics \
  -typecheck codex_devtools/live_quit.swift
```

Source checks and typechecking do not establish native AX availability, app
identity on another machine, prompt behavior, or successful termination in
Live. Native qualification remains a separate evidence gate.

Apple API reference: [`NSRunningApplication.terminate()`](https://developer.apple.com/documentation/appkit/nsrunningapplication/terminate()).
