# Explicit artifact deployment

Deployment is opt-in and operates only on individually listed files. It does not synchronize folders, remove destination files, open Max or Live, or modify saved Sets and presets. Keep the logical manifest in Git; keep local plans, install roots, receipts, and backups outside Git.

Create a manifest containing logical paths only:

```json
{
  "format": "codex-devtools-deployment-manifest-v1",
  "files": ["Devices/Example.amxd", "Devices/Example.maxpat"]
}
```

Plan records each source SHA-256 and the current destination SHA-256 (or `null` for an absent file). It writes only the requested plan file, which must be outside both roots and must not replace the input manifest:

```sh
python3 -m codex_devtools deploy plan deployment-manifest.json \
  --source-root /path/to/repository/release \
  --destination-root /path/to/selected/device/library \
  --out /tmp/example-plan.json
```

To run the CLI while staying in another project's directory, add the existing devtools checkout to `PYTHONPATH` rather than installing the package:

```sh
DEVTOOLS_CHECKOUT=/path/to/devtools
PYTHONPATH="$DEVTOOLS_CHECKOUT${PYTHONPATH:+:$PYTHONPATH}" python3 -m codex_devtools deploy plan deployment-manifest.json \
  --source-root ./release \
  --destination-root /path/to/selected/device/library \
  --out /tmp/example-plan.json
```

Review the plan before applying. Apply preflights every listed source and destination, rejects changed hashes, unsafe paths, symlinks, duplicate/overlapping names, and roots that overlap. Existing destination bytes are backed up before any destination is changed. Each file replacement is atomic. The backup directory contains `receipt.json` and exact-byte backup files:

```sh
python3 -m codex_devtools deploy apply /tmp/example-plan.json \
  --backup-root /tmp/example-deployment-backup
python3 -m codex_devtools deploy rollback /tmp/example-deployment-backup/receipt.json
```

After applying a plan, verify that every listed source still matches its planned source hash and every destination contains those same bytes:

```sh
python3 -m codex_devtools deploy verify /tmp/example-plan.json
```

Verification is read-only and emits JSON with a `match`, `drift`, or `missing` status for each file plus aggregate counts. It compares the current destination with `source_sha256`; the plan's `destination_sha256` records the pre-deployment destination and is not the expected post-deployment value. Exit status is `0` when all files match and `1` when any file is missing or drifted. Invalid plans, unreadable paths, and symlink paths return an error and a nonzero exit status.

Rollback restores prior bytes or removes files created by that deployment only while the destination still matches the deployed source hash. If any destination has been edited since deployment, rollback refuses that path and records `recovery_required` in the receipt. Apply failures after writing begins trigger best-effort rollback and retain a receipt describing recovery needs; never report such a batch as applied. Re-plan after any source or destination changes.
