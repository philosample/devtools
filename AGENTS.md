# DevTools repository guidance

- This directory is a reusable, standard-library Python CLI package. Invoke it with `python3 -m codex_devtools` from `devtools/`; do not add installation steps for its basic use.
- For JS checks when Node is missing from PATH, use `python3 -m codex_devtools runtime node --json` and the verified quoted executable path; read docs/RUNTIMES.md. An absent PATH entry is not proof no runtime is installed. This discovery does not launch Live/Max or install dependencies.
- Preserve archive payload bytes. Never execute Max/Live, rewrite sets/presets, or perform host side effects from inspection commands. Artifact deployment is explicit opt-in only: require a tracked relative-file manifest, a dry-run plan, an apply against unchanged source/destination hashes, and a separate backup receipt for rollback. Never bulk-sync folders or overwrite sources.
- Keep path validation, duplicate detection, and overwrite refusal in archive extraction. Add regression coverage when changing archive bounds or output behavior.
- `max validate` covers JSON graph structure and port references, not runtime semantics or host compatibility.
- Keep the hash manifest stable across runs and exclude its own destination, VCS metadata, generated caches, and transient output/work directories.
- Do not copy personal config, secrets, application binaries, user audio, or unrelated project fixtures into this directory.
- Worktree ownership records are machine-local state outside Git. `worktree audit` must stay read-only; cleanup requires explicit owner release, an unchanged clean worktree, and a GitHub merged PR whose head matches the recorded head. Never infer ownership from a branch name or bulk-prune old worktrees.
- MIDI inspection/export/report commands are offline. Only the explicit `midi capture` command may drive MIDI Monitor; preserve user windows and keep captures, endpoint IDs and routing templates local. Never click a mixed source-category checkbox to disconnect inputs: it can enable unrelated inputs. Expand categories and disconnect selected sources, then verify saved source settings. IAC tests require verified Live input isolation before sending probes.
- Deployment manifests contain logical relative paths only. Keep machine-specific source/destination roots, plans, receipts, and backups outside Git; never use actual user install paths as fixtures.

- Python style, Ruff and Lefthook versions follow the shared global agent instructions; project-specific checks above remain separate.

- The canonical development test runner is pytest (`.venv/bin/python -m pytest -q`); existing `unittest.TestCase` tests remain supported. Runtime CLI code must stay stdlib-only. Do not add pytest as a runtime dependency.
- Lefthook pre-commit runs pinned Ruff checks on staged Python files, and pre-push runs pytest. Before installing hook shims, inspect `core.hooksPath` and existing hooks; never overwrite custom hooks without review.
