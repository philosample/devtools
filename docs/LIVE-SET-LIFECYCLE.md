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

## Pause after Save As and normal Quit

Add `--stop-before-reopen` to the existing preflight and action commands to leave
Live closed at the exact PID-exit boundary. Preflight stays read-only. Only the
explicit action command saves and quits:

```sh
python3 -m codex_devtools ax live-set-lifecycle \
  --pid 84769 --expected-bundle-id com.ableton.live \
  --source-set /absolute/path/Source.als \
  --destination-set /absolute/path/Recall-copy.als \
  --app "/Applications/Ableton Live 12 Suite.app" \
  --stop-before-reopen --execute > /absolute/private/evidence/paused.json
```

Review the exit status and receipt before proceeding. A successful pause emits
`codex-devtools-live-set-paused-v1`, mode `stop-before-reopen`, status
`saved-and-quit`, and `open: not_attempted`. The closed receipt records the exact
source/destination URLs, old PID/bundle ID, timestamp, complete saved Set file
pin, and app path/bundle ID plus pins for `Contents/Info.plist` and its direct
`Contents/MacOS` executable. File pins include SHA-256, size, device/inode and
nanosecond modification/change times. The Set is bounded to 128 MiB, app metadata
to 1 MiB, executable to 512 MiB, and the receipt to 64 KiB. Symlink components and
Git-contained evidence/selected files are refused. Failure receipts remain
`stopped`; an uncertain Save/Quit is never a resumable pause receipt.

The tool returns promptly after verifying normal Quit and exact PID exit; it
never waits for an external signal. The operator can use this closed interval
for separately authorized work. This tool does not read, replace, toggle, archive
or restore Live MIDI preferences. Retaining or independently verifying the
working MIDI configuration remains the operator's responsibility.

Resume uses the same command with a separate, exclusive option set. Pin the
reviewed receipt's exact bytes, then run the read-only resume preflight:

```sh
shasum -a 256 /absolute/private/evidence/paused.json
python3 -m codex_devtools ax live-set-lifecycle \
  --resume-receipt /absolute/private/evidence/paused.json \
  --receipt-sha256 <reviewed-receipt-sha256>
```

After a `ready` resume receipt, explicitly reopen:

```sh
python3 -m codex_devtools ax live-set-lifecycle \
  --resume-receipt /absolute/private/evidence/paused.json \
  --receipt-sha256 <reviewed-receipt-sha256> --execute
```

Resume accepts no PID, source/destination, app or pause flags: those identities
come exclusively from the caller-pinned receipt. Both wrapper and native helper
validate the closed pause schema, exact action/mode, saved Set bytes/identity and
app metadata/executable pins. Immediately before the sole open effect, the native
helper rereads the receipt and pins, requires the old PID absent (including
non-GUI PID reuse or uncertain `kill(pid, 0)` results), and refuses any running
application with the expected Live bundle ID. There is no age-only expiry for an
unchanged paused state. A changed receipt, Set, app or process condition is stale
authority and fails closed. The original source Set is not reopened or saved.

The resume preflight performs no activation, launch, Save or Quit. Execution uses
the shared `NSWorkspace` reopen/AX document verification path once, requires the
selected app and a fresh PID, and never repeats Save As or Quit. Resume receipts
use `codex-devtools-live-set-resume-v1`, mode `resume`; all actions except `open`
remain `not_attempted`. An attempted open with missing callback, wrong identity,
missing exact AX document, or invalid/changed post-open evidence remains
`stopped` with `open: outcome_unknown` and manual inspection required. Only a
post-open ctime delta is tolerated; Set bytes, inode, size and mtime must match.
No restart retry, prompt dismissal, rollback or cleanup is automatic.

The SHA pin records caller approval of exact receipt bytes; it does not
authenticate the writer or establish independent native provenance. Process and
file checks are separate observations, so an external actor can still race after
the final check. Native lifecycle, retained preferences, SDK/LOM path and recall,
MIDI/wire and hardware remain distinct acceptance gates.

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
Before sending Go To Folder, it raises the exact Save window if needed, sets
`AXFocused=true` on the observed filename field, and verifies both the field's
focus value and the app's focused-element/window identity. It rechecks the
focused Path field and window before Return; it uses no `AXPress` fallback for
the filename field.
While Go To Folder is open, the focused AX window must be the unique
`AXSheet` identifier `GoToWindow` that is a direct child of the `Save` window;
the exact `PathTextField` must be found inside that sheet and be the focused
element before Return. The command verifies that the sheet child disappears
after Return before continuing to Save controls.

After the sheet disappears, Save readiness has a five-second monotonic budget
within the unchanged 240-second outer command deadline. Each condition-driven
poll refreshes the same `save-panel`, exact Live PID/bundle and foreground/focus,
filename, Where folder leaf, source URL/file metadata and absent destination.
Missing, duplicate or changed controls, a reappearing sheet, modal prompt, changed
identity or file drift stop immediately. Only positive enabled state on the one
`Save`/`OKButton` authorizes the single Save press; disabled or unavailable enabled
state waits for a fresh observation and never grants permission. No menu, field,
keyboard or Save action is repeated by this wait. A readiness timeout leaves Save
`not_attempted` and the prior menu outcome unchanged; the panel is left for
operator inspection. This handles a later enabled observation after valid field
readback without asserting why an earlier panel state was disabled. Source
contracts and compilation do not establish native timing or Save success.

During Save panel scans, AX elements that have become invalid are skipped as
stale file-list rows. Browser `AXScrollArea`, `AXTable`, `AXOutline`, `AXRow`, and
`AXColumn` subtrees are omitted because they do not contain the Save-panel chrome
controls. Each required control must still match exactly once, and unsupported
optional `AXTitle`/`AXIdentifier` reads make only that element a non-match.
Required controls still need the exact role and each selector/readback required
by that control's guard; other AX errors or traversal-bound violations stop the
operation and identify the failing container's role, subrole and identifier
where available.

The native helper bounds Save, Quit, LaunchServices completion, and reopen
observation waits to a combined 180 seconds. The wrapper allows 240 seconds to
leave process startup and receipt time. If that outer deadline expires during
execution, it returns a `stopped` receipt with every action marked
`outcome_unknown` and requires manual inspection.

AX menu items are acted on only when the observed element is uniquely identified,
enabled and exposes `AXPress`. The Go To Folder chord and Return use one
`CGEvent.post(tap: .cgSessionEventTap)` route after rechecking the exact Live
PID is frontmost; the chord carries Command and Shift flags on both key-down
and key-up events. The Save window and focused field are checked immediately
before each key sequence. A matching key-up is deferred immediately after each
key-down so it is posted before any later focus-loss error is reported. The
session event stream is not PID-exclusive: foreground can change between the
check and delivery, so that race remains and requires manual inspection if the
post-stroke check detects focus loss. There is no alternate event route or
blind retry. No CUA bridge or coordinate input is used. Save fields use
`AXUIElementSetAttributeValue` and exact readback. Save and quit are marked
outcome-unknown as soon as their respective actions are attempted. A stop
receipt includes the phase and whether
manual inspection is required. After a Save attempt, never repeat the command
until the current UI and destination are inspected.

Before File-menu interaction, the helper rechecks that the exact Live PID is
frontmost and the owned Set window is focused. It recognizes an already expanded
or selected File menu and does not toggle it; otherwise it presses File once and
waits up to two seconds for positive expanded/selected state and the exact enabled
`Save Live Set As...` menu item with `AXPress`. AX children are not proof that the
menu is open because collapsed menus may retain a static subtree. If expansion
attributes are unsupported, it makes at most one best-effort show attempt, then
fails closed unless expansion is positively observed; it never presses Save As
based on subtree presence alone. The
modal guard includes AXModal windows and the observed `open-panel` window
identifier; it does not close modeless Settings windows or reject windows only because
of an AXDialog subrole. A stopped menu-phase receipt may include bounded,
timestamped focus, blocker, File-menu and target-item readbacks. These diagnostics
explain the observed AX state; they do not establish why a prior menu state was
disabled. The focused unit checks are source-contract and receipt-model checks,
not native AX execution; native confirmation remains a separate gate.

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

Offline contract/CLI checks, the compiled fixture state machine with mocked
trust/process/launch interfaces, and Swift compilation do not open Live or
exercise the native UI:

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
[`CGEvent.post(tap:)`](https://developer.apple.com/documentation/coregraphics/cgevent/post%28tap%3A%29),
[`CGEventTapLocation.cgSessionEventTap`](https://developer.apple.com/documentation/coregraphics/cgeventtaplocation/cgsessioneventtap),
[`CGEventFlags`](https://developer.apple.com/documentation/coregraphics/cgeventflags),
[`NSRunningApplication.terminated`](https://developer.apple.com/documentation/appkit/nsrunningapplication/isterminated),
and [`NSWorkspace.open`](https://developer.apple.com/documentation/AppKit/NSWorkspace/open%28_%3AwithApplicationAt%3Aconfiguration%3AcompletionHandler%3A%29).
