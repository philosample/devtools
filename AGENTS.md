# DevTools repository guidance

- This directory is a reusable, standard-library Python CLI package. Invoke it with `python3 -m codex_devtools` from `devtools/`; do not add installation steps for its basic use.
- Preserve archive payload bytes. Never execute Max/Live, edit installed devices, rewrite sets/presets, or perform other host side effects from these commands.
- Keep path validation, duplicate detection, and overwrite refusal in archive extraction. Add regression coverage when changing archive bounds or output behavior.
- `max validate` covers JSON graph structure and port references, not runtime semantics or host compatibility.
- Keep the hash manifest stable across runs and exclude its own destination, VCS metadata, generated caches, and transient output/work directories.
- Do not copy personal config, secrets, application binaries, user audio, or unrelated project fixtures into this directory.
