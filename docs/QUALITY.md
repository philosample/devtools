# Python quality tooling

The studio repositories use Ruff `0.17.3` for linting and formatting and Lefthook `2.2.2` to run fast staged-file checks. Each repository pins the behavior in `ruff.toml` and `lefthook.yml`. The hook runs `ruff check` and `ruff format --check` on staged `*.py` paths. It never applies fixes, edits files, or stages content. The Lefthook glob skips a commit with no staged Python files.

## Install and verify

From this checkout, install the exact, versioned tools with pipx:

```sh
python3 -m codex_devtools.quality bootstrap --dry-run
python3 -m codex_devtools.quality bootstrap
python3 -m codex_devtools.quality doctor
```

The bootstrap installs `ruff-0.17.3` and `lefthook-2.2.2`. It checks that the pipx bin directory is on `PATH` before writing. If `lefthook` is not already on `PATH`, it creates a `~/.local/bin/lefthook` symlink to the pinned executable, because generated Git hook shims invoke `lefthook` by its normal command name. It refuses to replace a different version. A local receipt at `~/.local/state/codex-devtools/quality-tools.json` records which pieces it created. To reverse only that setup, run:

```sh
python3 -m codex_devtools.quality remove
```

Install the hooks for a repository only after its tooling config has been reviewed and merged. First inspect `git config --show-origin --get core.hooksPath`, `git rev-parse --git-path hooks`, and existing non-sample hook files. `lefthook install` writes into the selected hooks directory; do not replace a custom hook or a shared hook path. `lefthook uninstall` removes Lefthook's installed shims. The hooks are separate from the tracked config and may be installed or removed per local repository.

## Run checks

After staging Python changes, run the same checks manually with `lefthook run pre-commit`. Lefthook filters staged file names and quotes paths with spaces. Its jobs read those paths from the working tree, not from the index blob. A partially staged Python file can therefore fail because of unstaged edits; the hook leaves the index and working tree untouched.

For local checks or CI over an explicit changed-file list, run:

```sh
ruff-0.17.3 check --force-exclude -- path/to/file.py "path with spaces.py"
ruff-0.17.3 format --check --force-exclude -- path/to/file.py "path with spaces.py"
```

CI should pass only the changed Python paths to these commands; when no Python path changed, skip both commands. A full-repository Ruff run currently reports pre-existing lint and format findings in all audited repositories. The hooks adopt checks incrementally as Python files are changed, and no full-repository pass is claimed.

When a check fails, review fixes on specific files:

```sh
ruff-0.17.3 check --fix -- path/to/file.py
ruff-0.17.3 format -- path/to/file.py
git diff -- path/to/file.py
git diff --cached -- path/to/file.py
```

Ruff applies only fixes marked safe by default; `ruff.toml` also keeps `unsafe-fixes` disabled. Review and stage intended hunks yourself, especially when the file was partially staged. Ruff formats Python only; legacy Max JavaScript remains unchanged.

## References

- [Ruff installation and versioned installs](https://docs.astral.sh/ruff/installation/)
- [Ruff configuration settings](https://docs.astral.sh/ruff/settings/)
- [Ruff formatter and check mode](https://docs.astral.sh/ruff/formatter/)
- [Lefthook installation](https://github.com/evilmartians/lefthook/blob/master/docs/install.md)
- [Lefthook staged-file templates and path quoting](https://lefthook.dev/configuration/run/)
- [Lefthook glob filtering and skipped no-match commands](https://lefthook.dev/configuration/glob/)
- [Lefthook releases](https://github.com/evilmartians/lefthook/releases)
