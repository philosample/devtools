# Local runtime discovery

Discover and verify an existing Node.js executable without installing packages or changing configuration:

```sh
python3 -m codex_devtools runtime node
python3 -m codex_devtools runtime node --json
python3 -m codex_devtools runtime node --app "/Applications/Ableton Live 12 Suite.app" --json
```

The command checks `node` on `PATH` first. If that executable is missing or does not return a version, it checks the selected app bundle. By default the app is `/Applications/Ableton Live 12 Suite.app`; `--app` can point to an Ableton Live app bundle or directly to a `Max.app` bundle. The bundled executable path is derived from that bundle and is not searched for elsewhere.

Each candidate must be an executable file and complete `node --version` within five seconds. Output includes availability, selected path, version, provenance, and attempted candidates. Exit status is 0 when a runtime is verified and 2 when none is usable; `--json` emits the same fields for either result. This is runtime discovery only: it does not install dependencies or prove a particular package or Max for Node project works.

The command performs no network, UI, permission, application, package, or routing changes.
