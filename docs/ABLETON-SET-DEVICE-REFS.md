# Read-only Ableton Set device-reference inspection

Inspect one explicitly selected gzip-compressed `.als` file:

```sh
python3 -m codex_devtools ableton inspect-set-device-refs \
  "/path/to/selected.als"
```

To assert that its one distinct AMXD `FileRef` points to an exact local file:

```sh
python3 -m codex_devtools ableton inspect-set-device-refs \
  "/path/to/selected.als" \
  --expect-amxd-path "/path/to/expected-device.amxd" \
  --expect-amxd-sha256 "<64-character SHA-256>"
```

Both expectation arguments are required together. The assertion fails for a
missing or unreadable reference, more than one distinct AMXD path, a different
resolved file path, or a digest mismatch. Comparing exact paths prevents two
same-basename files in different folders from being treated as the same device.
Repeated identical `Path.Value` entries are deduplicated, with their occurrence
count retained.

The output preserves each raw `Path.Value`, `RelativePath.Value`, and
`RelativePathType.Value`. It does not search by basename or infer Ableton's
library resolution rules. For an absolute local `.amxd` path, it reports
whether that literal reference resolves to a regular file and hashes the bytes
there. Relative or foreign-platform paths remain unresolved on this host.
Non-AMXD `FileRef` paths are listed but not opened or hashed.

The reader accepts only a gzip stream whose bounded UTF-8 XML has an `Ableton`
root, one `LiveSet`, and `FileRef` elements containing exactly one direct,
non-empty `Path Value`. It rejects DTD/entity declarations, malformed XML,
unknown roots/namespaces, Sets with no `FileRef`, and Sets with no AMXD
references. Compressed input is limited to 32 MiB, decompressed XML to 128 MiB,
the Set to 100,000 `FileRef` entries, and each AMXD hash target to 512 MiB.
These limits and the observed FileRef subset keep parsing bounded; they do not
claim support for every Ableton Set schema variant.

This is saved-file evidence only. It proves what paths the selected `.als`
currently contains and the bytes currently present at a literal local AMXD
path. It does not prove which device instance is active in Live, whether Live
successfully resolved the dependency, or what the hosted device is doing. No
folder scan, Set write, relocation, application launch, MIDI endpoint action,
or installation is performed.

## Explicit sibling copy with one replacement

`ableton copy-with-device-ref SOURCE.als FRESH.als --source-sha256 HASH
--old-amxd OLD.amxd --old-sha256 HASH --new-amxd NEW.amxd --new-sha256 HASH`
previews a narrow reference change. Add `--execute` to create the new file.
This separate action never changes the source or opens Live. `--expected-occurrences`
declares how many exact `Path.Value` matches to select; it defaults to `1` and
accepts only integers from `1` through `16`.

Both Set paths must be canonical siblings in the same directory, preserving
the original Project location. The destination must not exist. The source must
contain exactly the declared number of FileRefs whose absolute Path exactly
matches `--old-amxd`. The default requires one; duplicate exact references are
refused unless their exact count is explicitly declared. Counts above 16,
missing matches, or any count mismatch refuse the copy. Every selected
FileRef is independently validated before output. In strict mode each must
have a type-1 relative path equal to that device's path relative to the Set
directory. Other FileRefs, including repeated references to other AMXDs, are
preserved in their original order. Only each selected FileRef's `Path` and
`RelativePath` attribute values change; all other decompressed XML bytes are
preserved. The preview reports declared and actual match counts, each selected
FileRef's zero-based index and old/new paths, exact old/new relative attribute
bytes in hexadecimal, and two XML attribute edits per selected reference.
For one selected reference, the existing `selected_file_ref` receipt field is
retained; `selected_file_refs` contains the per-reference receipt list. For
multiple references, the list identifies every edit and the singular field is
omitted. Unsupported layouts fail instead of being normalized.
The original Set and both device files must match their SHA-256 pins. Execution
rechecks the pins, creates the destination exclusively, flushes it and its
directory, and inspects the new reference and hash. A write/readback error can
leave a new partial file; preserve it for inspection and never blindly retry.

The JSON report records the output hash, replacement file hash and whether a
copy occurred. This does not attest sidecars or replace complete package
verification. A copied reference is not Live load, dependency resolution,
parameter recall or performance evidence; qualify those independently.

### Diagnostic sibling-bundle transformation (explicit opt-in)

The default above remains strict. `--diagnostic-sibling-bundle` permits a
separate, **diagnostic-only syntactic transformation** when a pinned owned Set
contains a type-1 relative path whose base is unknown. It does not select,
infer, normalize or verify that base. For example:

```sh
python3 -m codex_devtools ableton copy-with-device-ref \
  "/canonical/Project/Owned.als" "/canonical/Project/Fresh-Diagnostic.als" \
  --source-sha256 "<source Set hash>" \
  --old-amxd "/canonical/bundles/old-bundle/Harness.amxd" \
  --old-sha256 "<old AMXD hash>" \
  --new-amxd "/canonical/bundles/new-bundle/Harness.amxd" \
  --new-sha256 "<new AMXD hash>" \
  --diagnostic-sibling-bundle
```

Preview first; `--execute` explicitly creates only the fresh sibling Set.
All existing source/device pins, canonical paths, exact declared occurrence
count (default one, maximum 16), per-reference validation, exclusive
destination creation and execution/readback checks still apply. Each selected
reference is transformed independently; distinct safe relative prefixes are
preserved independently. The receipt lists the old/new relative attribute
bytes for every selected reference as well as a prefix/tail transform for each.
This opt-in additionally requires:

- The AMXDs have the same basename in distinct canonical sibling bundle
  directories under the same canonical grandparent. Both are regular files.
- The selected relative value is type `1`, literal safe ASCII in the XML
  attribute, at most 4,096 characters and 64 slash-separated components.
  Up to 32 leading `..` components are permitted and preserved literally;
  `..` elsewhere and `.` components are refused.
- Ordinary components begin and end with an ASCII letter, digit, `_` or `-`.
  Interior characters may also be `.`, space, `_` or `-`. This intentionally
  refuses absolute/drive paths, backslashes, NUL/control characters, XML
  entity encodings, Unicode, empty components and dot/space terminal names.
- The exact final two components are `OLD_BUNDLE_BASENAME/AMXD_BASENAME`.
  The prefix may not repeat the old/new bundle basename or AMXD basename.
  Both new tail components must satisfy the same safe syntax.

Only the final bundle/file tail is replaced with
`NEW_BUNDLE_BASENAME/SAME_AMXD_BASENAME`; every original relative-prefix byte
is retained. The other edit sets the selected absolute `Path` to the pinned
new canonical AMXD. No other XML byte or FileRef changes. The source Set hash
pins the original relative attribute bytes, so no separate relative pin is
required. The receipt identifies `relative_path_policy` as
`diagnostic_sibling_bundle` and records original/replacement relative values,
the preserved prefix, their exact ASCII bytes in hexadecimal, and both tails.

These constraints limit syntax and edits; **they do not prove relative
resolution**. A counterexample is canonical `/G/old/Harness.amxd` and
`/G/new/Harness.amxd`, with an unknown base `/B` where `/B/old` is a symlink to
`/G/old`, but `/B/new` does not exist. The original `old/Harness.amxd` resolves
to the old canonical file under that base; `new/Harness.amxd` fails under the
same base despite canonical sibling targets. The regression suite preserves
this counterexample and accepts only the diagnostic transformation, with an
unverified-resolution receipt. Unknown Live lookup rules can likewise remain
unverified even where ordinary filesystem paths have no aliases.

The receipt states: relative base/resolution unverified; no Live
load/dependency/recall proof; native exact component/manifest pins required
before effects. After independent source review, a separate authorized native
operator must open only the new owned copy in a fresh harness session and
reject any loaded component/manifest pin mismatch before SDK, route,
transport or MIDI actions. AMXD disk hashes alone do not attest sidecars or
which components Live loaded. This CLI performs none of those native actions
and does not authorize them.
