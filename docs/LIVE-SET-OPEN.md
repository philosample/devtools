# Guarded same-process Live Set open

`ax live-set-open` is read-only preflight unless `--execute` is explicit. It uses
the fixed `--open-existing` modes in the existing lifecycle Swift helper, reusing
its exact AX document URL, path, menu, foreground/focus, dialog and NSWorkspace
operations. The ordinary mode never answers prompts; the separate reconciliation
mode below admits only one exact Save control. The tool does not duplicate a
per-Set UI script or accept arbitrary keys, AppleScript, prompt text, menu items
or actions.

Both source and destination must already be nonempty regular `.als` files outside
Git, with canonical absolute paths and no symlink components or hardlink alias.
Both require independently reviewed SHA-256 values. Files are bounded to 128 MiB.
The selected source must be the actual current Set's AX document URL, not merely
a familiar window title. The exact running PID, bundle ID and actual app bundle
must agree; only one process for that bundle may be running.

```sh
python3 -m codex_devtools ax live-set-open \
  --pid 84769 --expected-bundle-id com.ableton.live \
  --source-set /private/owned/Current.als --source-sha256 SOURCE_SHA256 \
  --destination-set /private/owned/Existing.als --destination-sha256 DESTINATION_SHA256 \
  --app "/Applications/Ableton Live 12 Beta.app"
```

Read the `ready` receipt before repeating that exact command with `--execute`.
Without a Save permission, the command never saves the source. If Live presents a
Save-changes prompt during the one open attempt, the command stops and leaves it
untouched. It never presses Don't Save, Replace, Save As, or a prompt's Save button.
The caller must inspect and reconcile that pending operation before another action.

## Reconcile one already-open Save Changes dialog

`--reconcile-pending-open` is a separate explicit mode for a pending Open that
has already produced one exact Save Changes dialog. It requires
`--save-owned-source`, a private `--backup-root`, and
`--expected-generation` from a fresh read-only process inspection. Obtain the
token through the existing CLI before the dialog Save action:

```sh
python3 -m codex_devtools ax live-settings-tab \
  --pid 84769 --expected-bundle-id com.ableton.live \
  --window-title "Current Set title"
```

Use the `target.process_generation` field only after confirming the returned PID
and bundle ID. This command is read-only by default. A `stopped` status caused
by the already-open modal does not show that Settings was inspected or changed;
the process token alone is the evidence needed here. Collect it again immediately
before reconciliation so process restart or stale identity is caught. Preflight is
read-only and checks the current exact source Set, source/destination hashes,
process generation, focused dialog signature, and Play/Link/record transport
controls all off. The dialog must contain the exact source-derived text
`Save changes to "<source title>" before closing?` and exactly one enabled,
pressable Save, Cancel, and Don’t Save button. No other dialog is accepted.

After reviewing the ready receipt, add `--execute`. The helper creates and
rechecks the private exact-byte source backup, then rechecks process generation,
frontmost Live PID, current source window/URL, transport state, dialog identity,
and both Set pins immediately before one AXPress of that exact Save button.
It never presses Cancel or Don’t Save. Saving resumes the original pending
Open; this mode does not issue another Open. The receipt labels the pending
destination as unverified before Save because the dialog exposes no destination
URL. Completion requires the source file to show a positive transition and the
same Live process to show the exact pinned destination URL with stable file
identity/hash and stopped transport. The exact original dialog may remain
visible briefly after the one Save press; the helper waits for it to close without
pressing again. A changed/ambiguous dialog, a different destination, source
remaining current, or timeout is stopped/unknown and must be inspected manually.
The Save is never retried and no additional Open is sent.

```sh
python3 -m codex_devtools ax live-set-open \
  --pid 84769 --expected-bundle-id com.ableton.live \
  --source-set /private/owned/Current.als --source-sha256 SOURCE_SHA256 \
  --destination-set /private/owned/Existing.als --destination-sha256 DESTINATION_SHA256 \
  --app "/Applications/Ableton Live 12 Beta.app" \
  --save-owned-source --backup-root /private/owned/private-backups \
  --reconcile-pending-open --expected-generation PROCESS_GENERATION
```

Read the exact `ready` receipt before repeating with `--execute`. This does
not attest to the pending destination before Save, and `reconciled` proves only
the exact dialog Save, source file transition, and stable same-process
destination AX/file observations. A prior pending-open intent must be recorded
and reviewed by the caller. No M4L recall, runtime, wire, or hardware behavior
is established.

## Cancel one selected File menu and observe an original pending Open

`--cancel-selected-file-menu --observe-original-pending-open` is a separate
one-shot mode for a caller that already issued one Open and retained its exact
`stopped` receipt with `open=outcome_unknown`. The original receipt is accepted
only with its closed action/target schema and a caller-reviewed SHA-256. It does
not bind the pending destination; the receipt states
`pending_destination_binding_pre_action=unverified` and
`open_initiated_by_helper=false`.

Use the exact source and destination paths and hashes from the caller's existing
read-only inspection. Choose a new marker path inside an existing private
current-user directory outside Git. Preview reads only: it requires the original
source Set to remain focused, the same Live PID and inspected process generation,
the source/destination pins and stopped transport, no blocking windows, and
exactly one selected and enabled `File` menu item with `AXCancel`. If any part
is absent or ambiguous, it stops without action.

```sh
python3 -m codex_devtools ax live-set-open \
  --pid 84769 --expected-bundle-id com.ableton.live \
  --source-set /private/owned/Current.als --source-sha256 SOURCE_SHA256 \
  --destination-set /private/owned/Existing.als --destination-sha256 DESTINATION_SHA256 \
  --app "/Applications/Ableton Live 12 Beta.app" \
  --expected-generation PROCESS_GENERATION \
  --original-open-receipt /private/owned/original-unknown-open.json \
  --original-open-receipt-sha256 ORIGINAL_RECEIPT_SHA256 \
  --attempt-marker /private/owned/private-state/file-menu-cancel-once.json \
  --cancel-selected-file-menu --observe-original-pending-open
```

Review the exact `ready` receipt and its SHA-256. Execute once with the same
inputs plus `--preflight-receipt` and `--preflight-receipt-sha256`, then
`--execute`. The helper rechecks all process, file, focus, menu and transport
guards, creates the private marker exclusively, and repeats the guards after
the marker is flushed immediately before one `AXCancel` on that exact selected
`File` menu item. If the post-marker guard refuses, the receipt says
`file_menu_cancel=not_attempted`; the marker remains consumed, so the attempt
cannot be retried. If the AXCancel call began and its result is uncertain, the
receipt says `outcome_unknown`. It never calls AXPress, Save, Quit, or another
Open. An existing marker refuses any retry.

After cancellation, the helper only observes the already-pending Open. If the
same selected File menu remains visible briefly, it waits for that exact menu to
close without cancelling again. A changed/ambiguous menu or a blocking dialog
stops the operation. Transient unavailable AX reads can wait only within the
90-second monotonic bound; they never count as success. If no destination is
visible, the helper waits only while the source remains the sole focused
document, its pin stays unchanged, and transport stays stopped.
Success requires three stable observations of the exact destination AX URL in
the same Live process, destination focus, unchanged source bytes/identity,
destination bytes/identity matching the approved pin (allowing only ctime),
stopped transport and no blocking windows. If cancellation or completion is
uncertain, the result is `stopped`/unknown and the marker prevents another
attempt. Final success rereads the process generation, deadline, closed menu,
destination URL/file identity and stopped transport. This proves only the
same-process AX URL and file observations; it does not prove M4L recall, runtime,
wire, or hardware behavior.

## Explicitly preserve an owned source before opening

Only `--save-owned-source` authorizes one `File` → `Save Live Set` action on the
exact already-owned source. It also requires `--backup-root` pointing to an
existing canonical, private, current-user-owned directory outside Git. Use both
flags during preflight to review the plan; preflight creates no backup or receipt.
Add `--execute` only after reviewing source/destination hashes and the current Set:

```sh
python3 -m codex_devtools ax live-set-open \
  --pid 84769 --expected-bundle-id com.ableton.live \
  --source-set /private/owned/Current.als --source-sha256 SOURCE_SHA256 \
  --destination-set /private/owned/Existing.als --destination-sha256 DESTINATION_SHA256 \
  --app "/Applications/Ableton Live 12 Beta.app" \
  --save-owned-source --backup-root /private/owned/private-backups --execute
```

Before any native action, execution creates a new private UUID directory with
exclusive `source-before-save.als` and `receipt.json` files. The backup contains
the exact approved pre-save bytes; readback hashes, byte count and identity are
verified, and the backup receipt is flushed to disk. The native helper verifies
that proof again before Save. The backup is never automatically restored or
removed, even on failure. It is a source-file preservation proof, not a claim that
Live's unsaved in-memory state was already saved.

Save completion needs a positive file metadata/byte-identity transition, followed
by three stable observations of the exact source URL and bytes, with the same
process/focus and no dialogs. The condition-driven monotonic budget is 30 seconds;
no-op or unchanged Save stops honestly rather than guessing a dirty-state API.
The menu/control uses positive enabled/action readiness; a missing or disabled
item never authorizes Save. No Save or menu action is retried.

Before opening, the helper freshly rechecks source/destination hashes, identities,
current source AX URL, foreground/focus and absence of prompts. A source changed
without explicit Save permission is rejected. It then issues one exact destination
URL through NSWorkspace with the exact running app and new-instance creation off.
The callback must return the original PID. The exact destination AX document URL,
focus, file hash and identity must be observed before `opened`; a new process,
changed file, unexpected prompt or uncertain callback stops without cleanup.
The complete destination pin remains strict before open. After open only a
destination ctime change is permitted: path, hash, size, device/inode and mtime
must still match. Receipts retain the approved `destination_file`, full
`destination_file_after_open` and `destination_metadata_delta` (`[]` or `["ctime"]`),
which the Python boundary verifies against fresh file observations. This records
an observed metadata delta without attributing its cause. The source pin remains
strict, including ctime. Missing or inconsistent before/after proof stops unknown.
The native operation has a 180-second monotonic budget with a 240-second outer
process deadline. Timeout or invalid native completion retains unknown action
outcomes and the backup; neither implies cancellation or permits retry.

## Evidence limits and checks

The receipt format is `codex-devtools-live-set-open-v1`. `opened` proves the
same-process AX URL and file observations only. SDK/LOM handles can be invalidated
by any Set-switch attempt, including an uncertain one, even though the PID did not
change. Rebind and independently verify the harness's actual `live_set.file_path`,
full graph/session, quiet state and component pins. This tool does not prove M4L
recall, silence, dependency resolution, wire events, hardware or loaded-memory
artifact identity. There is no force quit, restart, Space switch, coordinate input
or prompt adoption. The existing Save As/Quit/reopen command remains unchanged.

```sh
.venv/bin/python -m pytest -q tests/test_live_set_open.py tests/test_live_set_lifecycle.py
swiftc -framework ApplicationServices -framework AppKit -framework CoreGraphics \
  codex_devtools/live_set_lifecycle.swift -o /tmp/codex-live-set-open-check
```

Those filesystem/receipt/source-contract tests and compilation do not operate
Live. The English `Save Live Set` menu label, actual Save file transition,
NSWorkspace same-process behavior and AX URL timing remain native acceptance gates
for the coordinator. Other app versions/localizations may fail closed.
