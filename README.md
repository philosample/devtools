# Codex DevTools

Small, portable utilities for inspecting Max files and recording source tree hashes. The suite is standard-library Python and does not require installation:

```sh
cd devtools
python3 -m codex_devtools --help
```

From another project, point `PYTHONPATH` at an existing devtools checkout; this uses the suite in place without installing it and keeps the current directory as the project root:

```sh
DEVTOOLS_CHECKOUT=/path/to/devtools
PYTHONPATH="$DEVTOOLS_CHECKOUT${PYTHONPATH:+:$PYTHONPATH}" python3 -m codex_devtools --help
```

## Commands

```sh
python3 -m codex_devtools max inspect path/to/device.mxf --json
python3 -m codex_devtools max inspect path/to/device.amxd
python3 -m codex_devtools max inspect path/to/patch.maxpat --json
python3 -m codex_devtools max extract path/to/device.amxd --out /tmp/device-payloads
python3 -m codex_devtools max validate path/to/device.amxd --json
python3 -m codex_devtools max package-m4l-candidate --help
python3 -m codex_devtools max preview path/to/device.maxpat --out /tmp/device-layout.svg
python3 -m codex_devtools max preview path/to/device.maxpat --out /tmp/device-layout.svg
python3 -m codex_devtools hash manifest path/to/source --out /tmp/source-hashes.json
python3 -m codex_devtools deploy plan deployment-manifest.json --source-root path/to/release --destination-root path/to/selected/library --out /tmp/deployment-plan.json
python3 -m codex_devtools deploy apply /tmp/deployment-plan.json --backup-root /tmp/deployment-backup
python3 -m codex_devtools deploy rollback /tmp/deployment-backup/receipt.json
```

`max inspect` reports file and archive entry hashes, entry counts, and graph counts where the root payload is Max JSON. JSON output is available with `--json`. `max extract` accepts MXF archives and AMXDs with embedded MXF payloads. It writes archive names as stored, preserves bytes, and refuses to overwrite any entry path already present in the requested output directory. Extraction is read-only with respect to the source file and does not launch or mutate Max or Live.

`max validate` parses the root patch JSON and checks inline patcher IDs and source/destination port references recursively. It validates structure only; it does not establish Max object semantics, external dependencies, or host behavior. `hash manifest` records sorted relative file paths, byte lengths, and SHA-256 hashes. It excludes `.git`, Python/tool caches, `outputs`, `work`, symlinks, and the manifest being written. It records no timestamps.

`max package-m4l-candidate` creates a new unfrozen MIDI-effect candidate from an explicit source `.maxpat`, a supported raw-patcher MIDI-effect `.amxd` template, and an exact loose-sidecar dependency allowlist. It preserves the accepted AMXD wrapper header, verifies the packaged root with `decode_file` and `graph_report`, hashes all inputs and outputs, and refuses existing output paths. This is offline packaging evidence only. See [M4L candidate packaging](docs/M4L-PACKAGING.md) for the supported narrow template signature and limits.

`max preview` inspects stored patcher rectangles for the root and inline nested patchers and can export a deterministic static SVG schematic. The JSON report includes source and SVG hashes, per-patcher bounds and rectangle overlaps. It uses visible presentation boxes when a patcher defines them, otherwise its patching rectangles. It never opens Max or executes JSUI. The SVG shows source-derived box labels and rectangles only; it is not a native Max render and does not draw patchline routing. See [static Max preview limits](docs/MAX-PREVIEW.md).

`max preview` inspects stored patcher rectangles for the root and inline nested patchers and can export a deterministic static SVG schematic. The JSON report includes source and SVG hashes, per-patcher bounds and rectangle overlaps. It uses visible presentation boxes when a patcher defines them, otherwise its patching rectangles. It never opens Max or executes JSUI. The SVG shows source-derived box labels and rectangles only; it is not a native Max render and does not draw patchline routing. See [static Max preview limits](docs/MAX-PREVIEW.md).

`deploy` is an explicit per-file deployment workflow. Its tracked manifest contains logical relative paths, while local roots and hash snapshots live in the plan outside the repository. Planning does not write into either root. Apply requires all planned hashes to remain current, creates exact-byte backups and a receipt, and atomically replaces only listed files. Rollback restores prior files or removes files created by that receipt only if their deployed hashes still match. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the manifest contract and recovery behavior.

The tools accept raw `mx@c` archives, AMXD files using the observed `ampf` + fixed-size device-tag/`meta` + `ptch` wrapper layout, and plain Max JSON patchers. The observed four-byte device tags are `aaaa` (audio), `iiii` (instrument), and `mmmm` (MIDI); other tags and wrapper variants are reported as unsupported. Encrypted `ciph` payloads are also unsupported. The AMXD metadata value is preserved as opaque wrapper metadata. `max inspect` reports wrapper parsing and graph validity separately; graph validity covers only the parsed JSON structure and ports. The parser bounds-checks archive headers, directory records, offsets, and payload lengths. Archive paths must be single safe filenames and duplicate names are rejected.

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
# MIDI capture

Timed Snoize MIDI Monitor capture, CoreMIDI endpoint inventory, JSON/CSV export and offline assertions are available under `python3 -m codex_devtools midi`. See [capture setup and evidence limits](docs/MIDI-CAPTURE.md).

For JavaScript tests, `python3 -m codex_devtools runtime node --json` discovers and verifies an existing Node runtime, including Live's bundled Max Node when PATH has none. See [runtime discovery](docs/RUNTIMES.md); no installation or app launch is performed.
