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
python3 -m codex_devtools ableton inspect-set-device-refs path/to/selected.als
python3 -m codex_devtools ableton inspect-set-device-refs path/to/selected.als \
  --expect-amxd-path path/to/expected-device.amxd --expect-amxd-sha256 <sha256>
python3 -m codex_devtools ax snapshot --pid 1234 --window-title Settings
python3 -m codex_devtools ax live-midi-sync --pid 1234 \
  --expected-bundle-id com.ableton.live --expected-count 21 --out /private/tmp/live-sync.json
python3 -m codex_devtools ax verify-studio-capture before.json isolated.json restored.json \
  --expected-bundle-id com.ableton.live --max-age-seconds 300
python3 -m codex_devtools worktree start --repository /path/to/repo --worktree /path/to/worktree \
  --branch codex/story --base origin/main --owner-chat <exact-chat-id> --story STORY-1 --plan 'issue #123'
python3 -m codex_devtools worktree update --worktree /path/to/worktree --owner-chat <exact-chat-id> --pr 123
python3 -m codex_devtools worktree audit
python3 -m codex_devtools worktree release --worktree /path/to/worktree --owner-chat <exact-chat-id>
python3 -m codex_devtools worktree cleanup --worktree /path/to/worktree --owner-chat <exact-chat-id>
```

`worktree` stores local ownership metadata in `$XDG_STATE_HOME/codex-devtools/worktrees.json` (or `~/.local/state/codex-devtools/worktrees.json`); `CODEX_DEVTOOLS_WORKTREE_REGISTRY` overrides the path for testing. `register` records an existing named worktree. `update` refreshes its exact head and plan/PR. The owner calls `release` after work is complete; it does not delete anything. A worktree without a valid numeric PR may be released only when clean, including untracked and ignored files. `audit` only reads registered entries and flags missing or changed worktrees. `cleanup` requires that release, a clean worktree including untracked files, and a GitHub PR reported merged at the exact recorded head; it still refuses no-PR worktrees. It then removes that worktree and its local branch. It never discovers ownership from branch names or bulk-prunes historical worktrees. The PR/issue remains the execution-status record; this registry is local ownership metadata only.

`max inspect` reports file and archive entry hashes, entry counts, and graph counts where the root payload is Max JSON. JSON output is available with `--json`. `max extract` accepts MXF archives and AMXDs with embedded MXF payloads. It writes archive names as stored, preserves bytes, and refuses to overwrite any entry path already present in the requested output directory. Extraction is read-only with respect to the source file and does not launch or mutate Max or Live.

`max validate` parses the root patch JSON and checks inline patcher IDs and source/destination port references recursively. It validates structure only; it does not establish Max object semantics, external dependencies, or host behavior. `hash manifest` records sorted relative file paths, byte lengths, and SHA-256 hashes. It excludes `.git`, Python/tool caches, `outputs`, `work`, symlinks, and the manifest being written. It records no timestamps.

`max package-m4l-candidate` creates a new unfrozen MIDI-effect candidate from an explicit source `.maxpat`, a supported raw-patcher MIDI-effect `.amxd` template, and an exact loose-sidecar dependency allowlist. It preserves the accepted AMXD wrapper header, verifies the packaged root with `decode_file` and `graph_report`, hashes all inputs and outputs, and refuses existing output paths. This is offline packaging evidence only. See [M4L candidate packaging](docs/M4L-PACKAGING.md) for the supported narrow template signature and limits.

`max preview` inspects stored patcher rectangles for the root and inline nested patchers and can export a deterministic static SVG schematic. The JSON report includes source and SVG hashes, per-patcher bounds and rectangle overlaps. It uses visible presentation boxes when a patcher defines them, otherwise its patching rectangles. It never opens Max or executes JSUI. The SVG shows source-derived box labels and rectangles only; it is not a native Max render and does not draw patchline routing. See [static Max preview limits](docs/MAX-PREVIEW.md).

`max preview` inspects stored patcher rectangles for the root and inline nested patchers and can export a deterministic static SVG schematic. The JSON report includes source and SVG hashes, per-patcher bounds and rectangle overlaps. It uses visible presentation boxes when a patcher defines them, otherwise its patching rectangles. It never opens Max or executes JSUI. The SVG shows source-derived box labels and rectangles only; it is not a native Max render and does not draw patchline routing. See [static Max preview limits](docs/MAX-PREVIEW.md).

`deploy` is an explicit per-file deployment workflow. Its tracked manifest contains logical relative paths, while local roots and hash snapshots live in the plan outside the repository. Planning does not write into either root. Apply requires all planned hashes to remain current, creates exact-byte backups and a receipt, and atomically replaces only listed files. Rollback restores prior files or removes files created by that receipt only if their deployed hashes still match. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the manifest contract and recovery behavior.

`ableton inspect-set-device-refs` reads one explicitly selected gzip-compressed `.als` file. It reports the compressed Set hash, deduplicated raw `FileRef/Path` values, and current local file hashes for AMXD paths. Optional expected path/hash arguments require the Set's sole distinct AMXD path to resolve to that exact file and digest. The command does not scan folders, write a Set, or launch Live. See [Ableton Set reference inspection](docs/ABLETON-SET-DEVICE-REFS.md) for the supported XML subset and limits.

`ax snapshot` reads the current macOS Accessibility tree for one explicit PID. It reports application identity, all visible window titles and a bounded set of roles, titles, descriptions, identifiers, help, scalar values and enabled states. `--window-title` requires one exact window-title match before walking that window's tree. It performs attribute reads only, requires Accessibility permission to be already trusted, never requests permission, and applies a two-second AX message timeout. The timestamp is when the snapshot was assembled; separate AX reads are not an atomic transaction. A truncated tree is marked in the JSON. This is a diagnostic observation, not proof of a particular app setting: callers must match the exact control description/role/value and re-observe after any change. It does not click, press, set values, use AppleEvents, change preferences or establish MIDI delivery/isolation. See [native Accessibility snapshots](docs/AX-SNAPSHOT.md).

`ax verify-studio-capture` checks the six exact Live AX checkbox descriptions from before/isolation/restoration snapshots, requiring input Track off during isolation, all other Studio Capture switches at their specified Track-only states, unchanged non-target switches, exact restoration, one process identity, ordered timestamps and untruncated Settings trees. The optional `--max-age-seconds` adds a current-at-check-time age gate. The report hashes all three files and states that it proves saved AX values only; it does not inspect Live or prove MIDI transport. See [Studio Capture AX switch verification](docs/STUDIO-CAPTURE-AX.md).

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

## Ruff and Lefthook

`python3 -m codex_devtools.quality bootstrap` installs the pinned Ruff `0.17.3` and Lefthook `2.2.2` tools with a local rollback receipt. See [Python quality tooling](docs/QUALITY.md) for hook activation, staged-file behavior, manual and CI commands, and rollback.

Opt-in test harnesses can publish a local command mailbox for repeatable scripted control through their own existing driver inlet. See [HARNESS-CONTROL.md](docs/HARNESS-CONTROL.md) for exact-instance/pin selection, explicit completion, timeout fencing and real cross-process tests. This transport provides no general Max, Live or MIDI control.

For native CUA sessions, the explicit app-handle [Live Set Save As helper](docs/CUA-LIVE-SAVE-AS.md) guards a fresh preservation copy, exact source/final URLs, stopped transport and a single Save attempt. It is not a CLI command; its Node fake-app checks do not establish native Live behavior. Run `node tests/live_set_save_as.test.mjs` with an existing runtime.

`ax live-set-lifecycle` performs read-only preflight by default. Its explicit `--execute` mode uses native AX menu/control actions, a PID-targeted Go To Folder chord, normal Quit, and `NSWorkspace` to reopen one exact new sibling Set. It stops on ambiguous dialogs/actions or uncertain outcomes; offline tests and compile checks do not run Live. See [Guarded Live Set lifecycle](docs/LIVE-SET-LIFECYCLE.md).
