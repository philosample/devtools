# Guarded Live Set Save As preservation (CUA only)

`cua/live_set_save_as.mjs` preserves the current Set through Live's **Save As**
UI into a caller-selected fresh directory. It requires an already selected,
explicit native CUA app handle. It is **not a CLI command**: Node alone has no
native app handle, and this module never discovers or launches Live, uses
AppleScript/shell UI, changes permissions, sends MIDI, or closes/discards a Set.
Importing the module performs no UI action.

The coordinator's persistent predecessor used these app operations successfully
in preservation runs R24/R25/R26. Issue [#31](https://github.com/philosample/devtools/issues/31)
records R24 on Live 12.4.16: one approximately six-second CUA call, with external
SHA/XML checks of the resulting Set references. That is provenance for the
mechanic, not a native validation of this persisted module or its added guards.
The corrected module at `181d859` (JS SHA-256
`5e4221b820551c8bfe98e57187201f4d08aa01af35af217b8db9e56b0680c272`)
was then imported into CUA and completed one native preservation on Live12.4.16
in about7.5seconds. Separate hash/FileRef checks found the source unchanged and
the destination byte-identical, retaining both expected AMXD references. This
verifies one observed host/AX sequence, not other versions, locales, crash
durability, unsaved-state combinations or recall. Native evidence belongs in
[issue31](https://github.com/philosample/devtools/issues/31); simulated guard
checks remain separate evidence.

Use it from an initialized CUA runtime that supports local ES modules and Node's
standard filesystem/path/URL modules. Select the app using the runtime's documented
entrypoint, retain single-operator ownership, and read its current API documentation
before invoking it. With that explicit app handle named `liveApp`, an example is:

```javascript
var liveSaveAs = await import('file:///absolute/devtools/cua/live_set_save_as.mjs');
var preserved = await liveSaveAs.preserveLiveSetAs(liveApp, {
  expectedSourceURL: 'file:///private/tmp/source-project/Source.als',
  destinationDirectory: '/private/tmp/new-preservation-run',
  destinationFilename: 'Preserved.als',
  expectedFinalURL: 'file:///private/tmp/new-preservation-run/Preserved%20Project/Preserved.als'
});
```

These are example paths, not discovered defaults. The destination directory must
**not exist**; its parent must exist. The helper creates that one directory
exclusively with mode `0700`, refuses Git ancestors/worktree markers and symlink
components, and binds its identity through the operation. No existing directory,
file or Project folder is reused. Use resolved absolute POSIX paths (`/private/tmp`
rather than the `/tmp` symlink on macOS). Filenames must be plain `.als` basenames.
A comma in the destination directory leaf, or AX `Value:`/`ID:` delimiter text in
a filename, is unsupported because the text snapshot does not quote those fields.

The **expected final URL is mandatory**. Live can create
`<filename without .als> Project/<filename>.als` within the selected directory;
a direct `<directory>/<filename>.als` is also supported when explicitly expected.
Only those two exact forms are accepted. The helper never searches for a resulting
Set or accepts another file because its name/path is a substring match.

The only native app calls are the documented operations supplied by the coordinator:

- `getAXState({emit: false, disableDiffing: true})`
- `performSecondaryAction(windowId, 'Raise')`
- `pressKey('super+shift+s')`, `pressKey('super+shift+g')`, `pressKey('Return')`
- `setValue(elementId, value)` for the filename and Go To Folder text fields
- `click(saveButtonId)` exactly once, after the final guards

Every UI action uses a fresh full AX observation. The source and post-Raise
observations require one exact standard-window file URL, no dialog/sheet/alert,
and exactly one stopped `Transport.Play` checkbox (`Value: 0`). URLs are parsed
as complete local file identities, with percent decoding and rejection of dot
segments, encoded separators, queries/fragments and control characters. Window
titles do not establish identity. A Save panel can hide the source/transport
subtree; if either is visible, it must still match the source/stopped state.
The final observation requires the expected destination URL and stopped transport.
This is a sequence of observations, not continuous transport/liveness proof.

The English `dialog Save` shape is required for Save decisions. During the two
Go To Folder decisions only, the separately observed macOS `sheet ID: GoToWindow`
is also accepted, with one unique `PathTextField`. That sheet can hide the entire
Save/source/transport subtree; the preceding filename and source checks still
apply. Unknown sheets, extra modals and wrong/missing/duplicate path fields stop
the operation. The first persisted-module native attempt reached this sheet and
stopped before Save; the pending copy was then completed manually. The later
corrected full module run is qualified separately above.
Controls must have unique numeric IDs and exact identifiers/roles. Native `(disabled)` flags
immediately after a targeted role are rejected, including already-correct
filename/path text fields; literal `(disabled)` text inside a filename is not a
role flag. This is guard grammar coverage, not a new native execution claim.
Filename `setValue` is verified before opening Go To Folder and again before Save. The full folder path
is verified in `PathTextField` before Return; the exact `Where:` popup leaf
and filename are then checked before the Save click. The fresh directory must still be empty,
unchanged and outside Git before that final observation. The new Set must appear
as a nonempty regular file at the exact expected path, and the original source
file's identity/size/modification time must remain unchanged.

Unexpected Replace, security/permission, Save-changes or discard prompts stop the
operation. Missing, duplicate, disabled or unrecognized controls, changed URLs,
playing transport, destination drift and native-call errors stop it too. No Escape,
Cancel, overwrite acceptance, ordinary Save, discard, close, restart or permission
operation is attempted on failure. Partial directories and the current dialog/Set
remain for the coordinator to inspect. There are no waits, automatic retries or
CUA runtime resets. Unknown AX shapes fail closed rather than trigger a fallback.

`LiveSaveAsGuardError` exposes `phase`, `saveAttempted`, `effect` and the destination
directory. `save_effect_unknown` means the Save click was attempted; do not infer
cancellation or repeat it automatically. Before that click, `save_not_attempted`
does not mean no UI changes occurred: Raise/Save As/Go To Folder may already have
run. One helper call at a time may own a given app handle.

A returned `destination_observed` record proves only the fresh AX URL and nonempty
regular-file observation. It does not prove finalized/durable Set bytes, copied
unsaved state, unchanged source content, embedded AMXD references, routing or MIDI.
Use separate exact-source/destination hashes and the existing offline
`ableton inspect-set-device-refs` command for the required content/reference checks.
The filesystem metadata check is not a content hash. The caller must keep the
fresh destination and single-operator UI session under its control. Save As is not
an atomic filesystem no-clobber transaction against another actor. A late Replace prompt is never accepted.

Run the pure guard/fake-app tests with an existing Node runtime:

```sh
node tests/live_set_save_as.test.mjs
node --check cua/live_set_save_as.mjs
.venv/bin/python -m pytest -q tests/test_live_set_save_as.py
```

When Node is absent from PATH, discover the existing executable using
`python3 -m codex_devtools runtime node --json`. The thin Python entrypoint uses
existing runtime discovery and requires a positive Node check count. Tests use only synthetic AX strings, an injected app fake and
disposable files; they never initialize CUA or open Live.
