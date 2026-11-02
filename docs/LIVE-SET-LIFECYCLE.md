# Guarded Live Set lifecycle

`ax live-set-lifecycle` inspects one caller-selected Live PID, bundle identifier,
and exact `.als` URL. It is read-only unless `--execute` is explicitly supplied.
Execution first saves a new sibling Set path through Live's Save As panel, then
quits that Live process normally, waits for its exact PID to terminate, and opens
the saved URL with `NSWorkspace` using the selected Live.app bundle. It then
requires a new process identity and an AX window that reports the exact saved
document URL.

Run the read-only preflight first:

```sh
python3 -m codex_devtools ax live-set-lifecycle \
  --pid 84769 --expected-bundle-id com.ableton.live \
  --source-set /absolute/path/Source.als \
  --destination-set /absolute/path/Recall-copy.als \
  --app "/Applications/Ableton Live 12 Suite.app"
```

Only after reviewing a `ready` receipt, run the action sequence explicitly:

```sh
python3 -m codex_devtools ax live-set-lifecycle \
  --pid 84769 --expected-bundle-id com.ableton.live \
  --source-set /absolute/path/Source.als \
  --destination-set /absolute/path/Recall-copy.als \
  --app "/Applications/Ableton Live 12 Suite.app" --execute
```

The destination must be a new sibling `.als` path in the source Set directory,
outside Git, with no symlink path components. The tool refuses existing files.
It activates only the exact Live PID and verifies it is frontmost with the
source Set window focused. It opens the exact File menu item
`Save Live Set As...`, verifies the observed Save panel controls, uses the
targeted Go To Folder shortcut to set and read back the absolute destination
directory, then checks the filename, folder leaf, enabled Save control and
fresh absence of the destination immediately before one Save click. A late
Replace/security/discard prompt, missing or duplicate control, changed source
URL, unexpected dialog, or failed readback stops the operation. No prompt is
accepted and no action is retried automatically.
Before sending Go To Folder, it raises the exact Save window if needed, presses
the observed filename field, and requires that field to be the focused AX
element while the selected Live PID remains frontmost.
During Save panel scans, AX elements that have become invalid are skipped as
stale file-list rows; each required control must still match exactly once, and
other AX errors or traversal-bound violations stop the operation.

The native helper bounds Save, Quit, LaunchServices completion, and reopen
observation waits to a combined 180 seconds. The wrapper allows 240 seconds to
leave process startup and receipt time. If that outer deadline expires during
execution, it returns a `stopped` receipt with every action marked
`outcome_unknown` and requires manual inspection.

AX menu items are acted on only when the observed element is uniquely identified,
enabled and exposes `AXPress`. The Go To Folder chord and Return use
`CGEvent.postToPid` for the already verified PID; no CUA bridge or global
coordinate input is used. Save fields use `AXUIElementSetAttributeValue` and
exact readback. Save and quit are marked outcome-unknown as soon as their
respective actions are attempted. A stop receipt includes the phase and whether
manual inspection is required. After a Save attempt, never repeat the command
until the current UI and destination are inspected.

Reopen uses `NSWorkspace.open(_:withApplicationAt:configuration:)`, which opens
the exact URL in the selected app. The old `NSRunningApplication` must report
`terminated`, its PID must be absent, and no other process with the expected
bundle ID may remain before opening. The completion must return a new PID with
the expected bundle ID. No dialog is dismissed during load; the exact AX
document URL must appear on one new Live window before success.

The `reopened` result is host lifecycle evidence only. Verify M4L
`live_set.file_path`, silent recall state, and any runtime behavior through the
separate Max/Live harness gates. The native AX window URL and a stable
nonempty file observation do not establish M4L parameter restore, content
equivalence, dependency resolution, MIDI, or hardware behavior. A `stopped`
receipt after any UI action means inspect the target app before proceeding.

Offline contract checks and Swift compilation do not open Live or exercise the
native UI:

```sh
python3 -m unittest tests.test_live_set_lifecycle -v
swiftc -framework ApplicationServices -framework AppKit -framework CoreGraphics \
  codex_devtools/live_set_lifecycle.swift -o /tmp/codex-live-lifecycle
```

The menu labels and Save panel identifiers are a bounded observed contract for
the current English Live UI: `Save Live Set As...`, `saveAsNameTextField`,
`PathTextField`, `where popup`, `OKButton`, and `Quit Live`. Other versions or
localizations remain unverified and fail closed.

Apple API references: [`AXUIElementCopyActionNames`](https://developer.apple.com/documentation/applicationservices/1462053-axuielementcopyactionnames),
[`AXUIElementPerformAction`](https://developer.apple.com/documentation/applicationservices/1462091-axuielementperformaction),
[`kAXPressAction`](https://developer.apple.com/documentation/applicationservices/kaxpressaction),
[`CGEvent.postToPid`](https://developer.apple.com/documentation/coregraphics/cgevent),
[`NSRunningApplication.terminated`](https://developer.apple.com/documentation/appkit/nsrunningapplication/isterminated),
and [`NSWorkspace.open`](https://developer.apple.com/documentation/AppKit/NSWorkspace/open%28_%3AwithApplicationAt%3Aconfiguration%3AcompletionHandler%3A%29).
