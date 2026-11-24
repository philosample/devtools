# Guarded same-process owned Live Set Save As

`ax live-set-save-as` preserves the current owned Set through one ordinary Live
Save As. It stays in the exact running process and returns before Quit or Open.
It reuses `live_set_lifecycle.swift`'s reviewed Save As/menu/name/Go To Folder
sequence; no CUA session, coordinates, AppleScript or generic prompt handler is
added. The existing lifecycle and same-process open modes keep their contracts.

The first call is read-only preflight. Execution requires the explicit flag and a
fresh destination directory, exact source file hash and private backup root:

```sh
python3 -m codex_devtools ax live-set-save-as \
  --pid 84769 --expected-bundle-id com.ableton.live \
  --source-set /private/owned/Outside.als --source-sha256 SOURCE_SHA256 \
  --destination-directory /private/owned/new-preservation \
  --destination-filename Preserved.als \
  --expected-final-set '/private/owned/new-preservation/Preserved Project/Preserved.als' \
  --app '/Applications/Ableton Live 12 Beta.app' \
  --backup-root /private/owned/private-backups
```

Review the `ready` receipt, then add `--execute` to that exact command. The backup
root must already be canonical, private, current-user-owned and outside Git.
The source is a bounded nonempty owned regular `.als` file, outside Git, with no
symlink components. Its complete hash/identity pin must remain unchanged. The app
path must match the actual exact PID's bundle. Play, Link, Arrangement Record and
Session Record are independently read from the exact source window and must all
be zero. No transport, recording, Link, Sync or routing control is changed.

## Exact Project-required entry mode

Default mode requires the normal source Set window and rejects pre-existing
prompts. `--project-required-dialog` is a separate explicit entry mode: preflight
must find the already present exact known warning, not any warning that appears
later. Its complete bounded AX subtree must have the observed six-element shape:

- One empty-title `AXWindow`, subrole `AXDialog`, one group and one empty static text.
- Exactly one `AXStaticText` identifier `DialogText` with the exact English message:
  “The Live Set you are about to save is located outside of a Project folder. Live
  must save the Set into a Project folder. Please choose a Project folder to save
  the Set into.”
- Exactly two enabled buttons exposing `AXPress`: description/identifier
  `Cancel` / `Cancel` and `Save As...` / `SaveAs...`.

Only that warning's Save As button may be pressed once. The exact source URL and
fresh stopped/Link/record-off values must still be readable behind the dialog.
Missing/duplicate controls, different text or localization, disabled buttons,
additional elements, another modal or unreadable guards stop without acceptance.
The command never presses Cancel, Replace, Allow, Discard or Don't Save. Existing
Set-open `--save-owned-source` still does not accept this warning or other prompts.

## Selected path and exact final path

The destination directory must not exist, even as a dangling symlink. Preflight
creates nothing. Execution reserves that one directory exclusively with mode
0700 and binds its device/inode, ownership and empty contents before Save. An
existing directory, Project, Set, collision or changed reservation is refused.
No ProjectInfo or other application metadata is manufactured by this tool.

The filename is one plain `.als` basename. `--expected-final-set` is mandatory
and must be exactly one of the known Save As helper forms:

- `<directory>/<filename>`; or
- `<directory>/<filename stem> Project/<filename>`.

The selected folder/name passed to Live are always the explicit destination
directory and filename. Live may create its normal Project subfolder. The caller
must choose the expected final form in advance, based on the reviewed workflow.
The command does not discover an output, switch forms, accept an alternate name,
or infer success from a substring. A different actual final URL stops unknown.
The existing exact document matcher may recognize `/tmp` versus `/private/tmp`
only when both identify the same observed file; it never searches for another Set.

The common native Save panel path retains exact filename/folder readback,
foreground/focus, unique enabled Save/OKButton, dismissed Go To Folder sheet and
no-overwrite guards. The Save-copy mode adds a 120-second monotonic admission,
checked at UI mutation boundaries, plus a 30-second post-Save observation budget.
The wrapper's 240-second outer deadline includes Swift startup. No menu, key,
Project button or Save action is repeated by a wait or retried after uncertainty.

## Preservation receipts and limits

Before any native action, execution saves the approved preimage and a flushed
private `codex-devtools-live-set-save-as-backup-v1` receipt under a fresh UUID in
the backup root. Native code verifies those source/backup bytes, hashes, file
identities, process/entry-mode and destination reservation again before Save.
The backup is never removed or restored automatically. It preserves the source's
pre-save disk bytes, not unsaved in-memory state.

After one Save press, success needs the original PID/app, exact final Set AX URL,
all four native flags off, unchanged complete source pin, unchanged destination
directory identity and three stable observations of the final nonempty file's
hash/size/device/inode/mtime/ctime. Python independently rechecks final/source and
backup pins. A `saved-copy` receipt records the produced final file pin and the
same-process observation, with Quit and Open both `not_attempted`. The wrapper
also saves `result.json` alongside the backup receipt. Receipt persistence failure
cannot return successful completion.

A stopped or timed-out attempt preserves the backup and reserved directory.
Unknown Save means inspect the current Live document, dialogs, exact target and
receipts before any later action; do not repeat the command or invent a new path
as an automatic retry. A source preflight failure with no actions is distinguished
from an uncertain mid-Save outcome. Neither failure causes cleanup, quit/reopen,
discard or restoration.

After a confirmed preservation, the new copy is the current Live document. A
separate reviewed `ax live-set-open` command can then open another owned Set using
that saved copy as its exact source, without `--save-owned-source`. Any Save As or
Set switch requires fresh SDK/LOM authority; same PID does not preserve handles.
These receipts establish host URL and file observations only. M4L runtime path,
state/recall, dependency resolution, startup silence, wire and hardware evidence
remain separate. No Max license or musical-device capability is added.

For future factory fixtures, create a legitimate Project through Live Save As
once, verify the saved seed, and make owned test copies through that reviewed
Project/seed workflow with fresh authority. A bare factory `.als` copy outside a
Project is not assumed to support ordinary Save. Do not copy or synthesize
ProjectInfo to bypass Live's Project creation. A newly saved seed can have
different `.als` bytes; its fixture profile/hash must be reviewed rather than
silently treated as the original factory hash. Studio normalization and its
protocol/acceptance gates remain owned by the studio repository.

## Source-only verification

```sh
.venv/bin/python -m pytest -q tests/test_live_set_save_as_native.py \
  tests/test_live_set_open.py tests/test_live_set_lifecycle.py
swiftc -framework ApplicationServices -framework AppKit -framework CoreGraphics \
  codex_devtools/live_set_lifecycle.swift -o /private/tmp/live-save-as-source-check
```

Tests cover filesystem reservations/backups, exact wrapper/native receipt schema,
wrong PID/URL/filename, overwrite refusal, deadlines and unknown outcomes. A pure
Swift policy/filesystem program compiles the production dialog matcher, deadline
and backup readers and exercises their ABI against Python's actual backup writer.
It contains no AX/AppKit entrypoint and never operates Live. Full Swift compilation
and source-contract tests also do not establish native UI acceptance. English
control identities, same-process Project creation and timing remain coordinator
native gates for the exact reviewed tool revision.
