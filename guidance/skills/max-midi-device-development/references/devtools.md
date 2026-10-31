# Devtools entrypoints

Private source: https://github.com/philosample/devtools . Locate an existing checkout through the project/workspace registry; do not install packages or clone another copy just to run a command when it is already available. From the checkout:

```sh
python3 -m codex_devtools max inspect /absolute/path/to/file.mxf --json
python3 -m codex_devtools max extract /absolute/path/to/file.mxf --out /explicit/new/output
python3 -m codex_devtools max validate /absolute/path/to/device.amxd --json
python3 -m codex_devtools hash manifest /absolute/source/root --out /explicit/manifest.json
python3 -m unittest discover -s tests -v
python3 -m codex_devtools runtime node --json
```

Inspection and graph validation are static evidence only. Extraction preserves archived bytes and refuses existing destination files. Graph checks do not prove object semantics or runtime behavior. Keep the installed app/user library read-only unless the task specifically authorizes deployment. Read the repo README for the actual current command interface.

If Node is missing from PATH, use `runtime node --json` before installing or repeatedly searching: it checks existing PATH and Live's bundled Max Node, verifies its version, and reports the selected executable/provenance. See devtools `docs/RUNTIMES.md` for an alternate app bundle. Execute the quoted returned path for focused JS suites; Node execution does not prove Max/Live behavior.

For `midi endpoints/template/capture/export/report`, read devtools `docs/MIDI-CAPTURE.md` and use the `midi-capture-testing` skill. These commands may initially be in a reviewed feature worktree; check the selected revision before invoking. Capture is explicit UI automation; export/report are offline. Never infer Live-to-IAC isolation from an online endpoint or hardware state from a Thru echo.
