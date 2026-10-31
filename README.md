# Codex DevTools

Small, portable utilities for inspecting Max files and recording source tree hashes. The suite is standard-library Python and does not require installation:

```sh
cd devtools
python3 -m codex_devtools --help
```

## Commands

```sh
python3 -m codex_devtools max inspect path/to/device.mxf --json
python3 -m codex_devtools max inspect path/to/device.amxd
python3 -m codex_devtools max inspect path/to/patch.maxpat --json
python3 -m codex_devtools max extract path/to/device.amxd --out /tmp/device-payloads
python3 -m codex_devtools max validate path/to/device.amxd --json
python3 -m codex_devtools hash manifest path/to/source --out /tmp/source-hashes.json
```

`max inspect` reports file and archive entry hashes, entry counts, and graph counts where the root payload is Max JSON. JSON output is available with `--json`. `max extract` accepts MXF archives and AMXDs with embedded MXF payloads. It writes archive names as stored, preserves bytes, and refuses to overwrite any entry path already present in the requested output directory. Extraction is read-only with respect to the source file and does not launch or mutate Max or Live.

`max validate` parses the root patch JSON and checks inline patcher IDs and source/destination port references recursively. It validates structure only; it does not establish Max object semantics, external dependencies, or host behavior. `hash manifest` records sorted relative file paths, byte lengths, and SHA-256 hashes. It excludes `.git`, Python/tool caches, `outputs`, `work`, symlinks, and the manifest being written. It records no timestamps.

The tools accept raw `mx@c` archives, AMXD files using the `meta`/`ptch` wrapper layout, and plain Max JSON patchers. The parser bounds-checks archive headers, directory records, offsets, and payload lengths. Archive paths must be single safe filenames and duplicate names are rejected.

Run the standard-library regression suite from this directory:

```sh
python3 -m unittest discover -s tests -v
```

## Local invariants

- Keep this package dependency-free and runnable with `python3 -m codex_devtools`; do not require installation or network access.
- Treat archive parsing, extraction, validation, and hashing as read-only workflows. Never open a Max/Live app or change a user library as a side effect.
- Extract exact entry bytes, including opaque/factory payloads. Do not normalize line endings or strip trailing bytes during extraction.
- Reject path traversal, duplicate entry names, invalid chunk sizes, truncated records, and writes over existing paths.
- Hash source files deterministically by sorted relative path; do not include timestamps, generated caches, `.git`, or the output manifest itself.
- Keep hardware mappings, device-specific Markov regressions, and runtime harness fixtures in their owning project. Promote only demonstrably reusable inspection and validation mechanics.
- Do not add application binaries, audio payloads, user presets/Sets, credentials, personal Codex configuration, or installed-environment data to this repository.
