# Codex DevTools

Command-line tools for inspecting Max and Ableton Live artifacts, assembling offline
Max for Live candidates, and collecting reproducible test evidence. The Python CLI
uses the standard library and runs directly from a checkout.

## Quick start

From the repository root on macOS, with Python 3.14:

```sh
python3 -m codex_devtools --help
python3 -m codex_devtools max inspect path/to/device.amxd --json
python3 -m codex_devtools ableton inspect-set-device-refs path/to/selected.als
python3 -m codex_devtools hash manifest path/to/source --out /tmp/source-hashes.json
```

From another project, point `PYTHONPATH` at your checkout and keep the current
directory as the working directory:

```sh
DEVTOOLS_CHECKOUT=/path/to/devtools
PYTHONPATH="$DEVTOOLS_CHECKOUT${PYTHONPATH:+:$PYTHONPATH}" python3 -m codex_devtools --help
```

Inspection, static validation, hashing, and offline packaging run without Live or
Max. Native Accessibility and CoreMIDI commands require macOS and the prerequisites
in their runbooks. Commands that write artifacts, control apps, send MIDI, or merge
pull requests have dedicated arguments and validation described below.

## Command reference

| Area | Entry points and documentation |
| --- | --- |
| Max files | `max inspect`, `max extract`, `max validate`, `max preview`, `max package-m4l-candidate`. See [archive and graph behavior](#archive-and-graph-behavior), [inline inputs](docs/MAX-INLINE-INPUTS.md), [static preview](docs/MAX-PREVIEW.md), and [offline candidate packaging](docs/M4L-PACKAGING.md). |
| Ableton Sets | `ableton inspect-set-device-refs` and `ableton copy-with-device-ref` inspect or explicitly retarget selected references in an owned sibling copy. See [Set references and copy modes](docs/ABLETON-SET-DEVICE-REFS.md). |
| Live on macOS | `ax` covers [snapshots](docs/AX-SNAPSHOT.md), [settings navigation](docs/LIVE-SETTINGS-TAB.md), [MIDI inputs](docs/LIVE-MIDI-INPUTS-CONTROL.md), [MIDI Output Sync](docs/LIVE-MIDI-SYNC-CONTROL.md), [capture switch verification](docs/STUDIO-CAPTURE-AX.md), [transport and focus](docs/LIVE-TRANSPORT-CONTROL.md), [Set open](docs/LIVE-SET-OPEN.md), [Save As](docs/LIVE-SET-SAVE-AS.md), and [Set lifecycle](docs/LIVE-SET-LIFECYCLE.md). Native CUA sessions can also use the [standalone Save As helper](docs/CUA-LIVE-SAVE-AS.md). |
| MIDI and harnesses | `midi` covers [capture, loopback, export, and assertions](docs/MIDI-CAPTURE.md). `harness` controls [opt-in test-harness mailboxes](docs/HARNESS-CONTROL.md) that expose an existing test driver. |
| Source and deployment | `hash manifest` records sorted relative paths, byte counts, and SHA-256 hashes. It excludes Git metadata, symlinks, virtual environments named `.venv` or `venv`, common caches, `.DS_Store`, `outputs`, `work`, and its own destination. `deploy` provides [per-file plan, apply, verify, and rollback](docs/DEPLOYMENT.md). |
| Runtime and contribution | `runtime node` [finds an existing Node.js runtime](docs/RUNTIMES.md). See [Python quality and test setup](docs/QUALITY.md), [worktree ownership](#worktree-ownership), and [PR readiness and guarded merge](docs/PR-READINESS.md). |
| Workstation access | The [standalone RustDesk scripts](docs/RUSTDESK.md) cover macOS client setup and optional self-hosting. Their setup and service commands change local configuration when invoked. |

### Archive and graph behavior

`max inspect` reports file and entry hashes, wrapper status, and graph counts
where the root payload is Max JSON. The reader recognizes the observed
`mx@c` archives and `ampf` wrappers with `aaaa`, `iiii`, or `mmmm` tags.
It rejects unknown wrapper variants, encrypted payloads, and invalid archive
bounds. `max extract` preserves exact entry bytes and refuses unsafe,
duplicate, or existing output paths. `max validate` checks JSON graph
structure and port references. Native object behavior, loading, and MIDI
require separate evidence.

### Worktree ownership

`worktree start --repository /path/to/repo` creates and registers a named worktree
with an explicit owner chat, story, and plan. See `worktree start --help` for
required arguments. `register` records an existing branch worktree, `update`
refreshes its head and plan or PR, and `audit` reads the registry and flags
missing or changed worktrees. The default registry is
`${XDG_STATE_HOME:-~/.local/state}/codex-devtools/worktrees.json`.
`CODEX_DEVTOOLS_WORKTREE_REGISTRY` overrides it for testing.

`register` and `update` accept a positive PR number or a same-repository HTTPS
GitHub pull URL for `origin`. The owner runs `release` when work is complete.
`cleanup` requires explicit release, unchanged clean state including ignored
files, and a GitHub-verified merge at the exact recorded PR head. Issue and PR
records carry execution status.

## Development checks

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m pytest -q
node tests/live_set_save_as.test.mjs
bash -n scripts/rustdesk-all-in-one-macos.sh
bash -n scripts/rustdesk-client-macos.sh
```

Pytest includes offline regressions, subprocess and mailbox tests, and several
macOS Swift compile checks. The Node Save As tests use a fake app. For hook
setup and CI coverage, see [Python quality tooling](docs/QUALITY.md).

Project-specific device mappings, runtime fixtures and musical acceptance
records live with their projects. The [promotion review](docs/PROMOTION-REVIEW.md)
records reusable mechanics, and [guidance snapshots](guidance/README.md) document
their intended development use. Keep user Sets, commercial assets, credentials,
installation paths, and local receipts outside the source tree.
