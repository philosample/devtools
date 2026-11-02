# Studio Capture AX switch verification

Use this offline verifier for three JSON receipts produced by
[`ax snapshot`](AX-SNAPSHOT.md): before changing input Track, with input Track
isolated, and after restoring the original value.

```sh
python3 -m codex_devtools ax verify-studio-capture \
  /absolute/local/before.json /absolute/local/isolated.json \
  /absolute/local/restored.json \
  --expected-bundle-id com.ableton.live --max-age-seconds 300
```

The verifier requires all six exact AX checkbox descriptions for `IAC Driver
(Studio Capture)`: MIDI Input Track, Sync and Remote Control; MIDI Output
Track, Sync and Remote Control. It rejects duplicate or missing rows, wrong
roles, disabled controls, values other than 0/1, truncated trees, non-Settings
observations, mismatched PID/process/bundle identity and timestamps that are
missing, out of sequence or non-UTC. The native v1 bounds schema is required
in full, including integer `window_count`, `windows_observed`,
`elements_observed`, and their matching `windows`/`trees` array lengths and
truncation flags. Older snapshots that omit fields added to native v1 are
rejected; do not backfill these fields into an old receipt. The expected bundle
identifier is explicitly supplied by the caller. `--max-age-seconds`
optionally requires all three observation timestamps to fall within that age
at verification time.

A passing report requires MIDI Input Track off in the isolated observation;
MIDI Input Sync and Remote off; output Track on; output Sync and Remote off;
only MIDI Input Track changing during isolation; and exact restoration of all
six observed values. If input Track was already off, a no-change sequence can
pass. The report records all row values, timestamps and snapshot SHA-256 hashes.

Without `--max-age-seconds`, the result validates a historical snapshot
sequence only. Even with the age gate, it establishes the AX values only at the
recorded observation times. It does not read Live, alter preferences, prove
that settings remain unchanged afterward, or establish emitted MIDI bytes,
silence, or delivery. Run it immediately after the operator's fresh native
snapshots when a current-state gate is required, and keep the source files
local because they contain application UI values.

Exit status is 0 for a passing sequence, 1 for a failed assertion and 2 for
invalid or incomplete receipts.
