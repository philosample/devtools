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
