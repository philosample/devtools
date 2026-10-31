# Offline M4L MIDI-effect candidate packaging

`python3 -m codex_devtools max package-m4l-candidate` builds an unfrozen
candidate directory from a caller-supplied root `.maxpat`, a caller-supplied
MIDI-effect `.amxd` template, and an explicit dependency allowlist. The command
does not start Max or Live, install an artifact, write to an application
library, or make a runtime compatibility claim.

## Supported template

The template must decode through the shared `codex_devtools.maxfiles`
`decode_file` reader as a raw-patcher AMXD with no embedded archive entries.
Packaging accepts only the locally observed 32-byte wrapper:

- `ampf`, little-endian version `4`
- `mmmm` device type and `meta`, little-endian version `4`
- the observed metadata flag `0` or `1`
- `ptch` with a payload length matching the file

The template root must declare `patcher.project.amxdtype` as FourCC `mmmm` and
contain top-level `newobj` boxes named `midiin` and `midiout`. The FourCC alone
does not identify a MIDI effect. Unknown, encrypted, archived/frozen, or
non-MIDI templates fail closed. This narrow signature is not a general AMXD
format specification.

The candidate preserves the template's first 28 wrapper bytes and writes the
source root JSON plus its Max terminator as an unfrozen `ptch` payload. It does
not inherit the template patch, dependency cache, or archived assets. If the
source omits `patcher.project.amxdtype`, the package copies the validated
template's project metadata and records that change in the manifest. A
conflicting source marker is rejected. The source and template remain
unchanged.

## Dependencies and outputs

The dependency allowlist is a JSON array of exact `{ "name", "source" }`
rows. The set of output names must match the source root's `dependency_cache`
exactly, including no unreferenced extras. Each output name is one safe flat
filename; source paths are relative to the declared root and resolve to
regular files within it. Duplicate or case/Unicode-colliding names, duplicate
source paths, missing files, symlink escapes, nonlocal bootpaths, frozen Max
containers, and collisions with the candidate or manifest are rejected.

The output directory must be new and outside the source root. It is created
exclusively; each artifact, loose dependency sidecar, and `manifest.json` is
created without overwriting. The deterministic manifest includes source,
template, dependency, candidate, and canonical root-patch SHA-256 hashes; graph
counts and roundtrip status; and `freeze.performed: false`. It contains no
machine-specific root paths or timestamps.

Example:

```sh
python3 -m codex_devtools max package-m4l-candidate \
  --source-root . \
  --source generated/my-device.m4l-midi-effect.maxpat \
  --template /path/to/Max-MIDI-Effect.amxd \
  --dependency-allowlist /path/to/dependencies.json \
  --out /tmp/my-device-candidate
```

From another repository, use the existing devtools checkout in place without
installing it:

```sh
DEVTOOLS_CHECKOUT=/path/to/devtools
PYTHONPATH="$DEVTOOLS_CHECKOUT${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m codex_devtools max package-m4l-candidate \
    --source-root . --source generated/my-device.m4l-midi-effect.maxpat \
    --template /path/to/Max-MIDI-Effect.amxd \
    --dependency-allowlist /path/to/dependencies.json \
    --out /tmp/my-device-candidate
```

This verifies offline archive preservation, root graph validity, and declared
dependency closure. It does not establish that Max or Live can load, save,
reopen, or resolve the candidate; that Live exposes the intended controls; or
that MIDI reaches an endpoint. A candidate is not a fully frozen AMXD or a
runtime-tested device.
