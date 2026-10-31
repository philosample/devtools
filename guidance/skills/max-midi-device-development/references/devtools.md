# Devtools entrypoints

Private source: https://github.com/philosample/devtools . Locate an existing checkout through the project/workspace registry; do not install packages or clone another copy just to run a command when it is already available. From the checkout:

```sh
python3 -m codex_devtools max inspect /absolute/path/to/file.mxf --json
python3 -m codex_devtools max extract /absolute/path/to/file.mxf --out /explicit/new/output
python3 -m codex_devtools max validate /absolute/path/to/device.amxd --json
python3 -m codex_devtools hash manifest /absolute/source/root --out /explicit/manifest.json
python3 -m unittest discover -s tests -v
```

Inspection and graph validation are static evidence only. Extraction preserves archived bytes and refuses existing destination files. Graph checks do not prove object semantics or runtime behavior. Keep the installed app/user library read-only unless the task specifically authorizes deployment. Read the repo README for the actual current command interface.
