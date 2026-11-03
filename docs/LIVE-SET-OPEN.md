# Guarded same-process Live Set open

`ax live-set-open` is read-only preflight unless `--execute` is explicit. It uses
one fixed `--open-existing` mode in the existing lifecycle Swift helper, reusing
its exact AX document URL, path, menu, foreground/focus, dialog and NSWorkspace
operations. It does not duplicate a per-Set UI script or accept arbitrary keys,
AppleScript, prompt text, menu items or actions.

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
python3 -m unittest tests.test_live_set_open tests.test_live_set_lifecycle -v
swiftc -framework ApplicationServices -framework AppKit -framework CoreGraphics \
  codex_devtools/live_set_lifecycle.swift -o /tmp/codex-live-set-open-check
```

Those filesystem/receipt/source-contract tests and compilation do not operate
Live. The English `Save Live Set` menu label, actual Save file transition,
NSWorkspace same-process behavior and AX URL timing remain native acceptance gates
for the coordinator. Other app versions/localizations may fail closed.
