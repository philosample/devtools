# Native macOS Accessibility snapshots

`ax snapshot` reads a bounded Accessibility tree for one caller-selected PID.
Use it to inspect existing UI state when the target app is already open. It
does not discover or launch apps, issue AppleEvents, request Accessibility
permission, or invoke UI actions.

```sh
python3 -m codex_devtools ax snapshot --pid 84769 --window-title Settings \
  --max-elements 500 --max-depth 10
```

The PID is required and is echoed with the running process name and bundle ID.
The optional window filter is an exact title and must match one window. The
JSON contains the timestamp, every reported window title, and a breadth-first
tree for the selected window(s). Each tree record includes role, subrole, title,
description, identifier, help, scalar value, enabled state, depth and parent
index where available. Attribute-specific AX errors are recorded instead of
silently treating failed reads as a control value. Strings are limited to 512
UTF-8 bytes; windows are capped at 64, element count defaults to 500 (hard
maximum 2048), and tree depth defaults to 10 (hard maximum 32). The
`bounds.windows_truncated` and `bounds.truncated` fields signal when either cap
omitted windows or reachable descendants.

The versioned top-level shape is:

```json
{
  "format": "codex-devtools-ax-snapshot-v1",
  "status": "observed",
  "observed_at_utc": "...",
  "read_only": true,
  "permission": "Accessibility already trusted; no prompt requested",
  "target": {"pid": 1234, "process_name": "...", "bundle_identifier": "..."},
  "bounds": {"max_elements": 500, "max_depth": 10, "max_windows": 64,
              "windows_observed": 1, "window_count": 1,
              "windows_truncated": false, "elements_observed": 1,
              "truncated": false, "window_title_filter": "Settings"},
  "windows": [{"index": 0, "title": "Settings"}],
  "trees": [{"window_index": 0, "elements": [
    {"index": 0, "depth": 0, "role": "window", "title": "Settings",
     "description": "...", "value": false, "enabled": true}
  ]}]
}
```

Optional AX attributes are omitted when unavailable. AX errors appear under
`attribute_errors` or `children_error`; strings omitted from the example can be
truncated and are named in `truncated_attributes`. Element indexes are local to
the one snapshot, with parent references expressed as `parent_index`.
For `AXChildren`, `kAXErrorNoValue` is retained as `children_error` metadata and
treated as a leaf. Other child-read errors leave the tree marked truncated.

The command requires `AXIsProcessTrusted()` to already be true. It never calls
the permission-prompting API. Each out-of-process AX message has a two-second
timeout, and the Swift process has a 30-second bound. It reads attributes only;
there is no AXPress, AXSetValue, keyboard/mouse input, preference write or
AppleEvent path in this tool.

AX values are current observations made during traversal, not a coherent atomic
snapshot. The timestamp marks report assembly and should not be mistaken for a
single instant across all controls. The PID must be checked against the
reported process identity to guard against stale PID reuse. Window titles and
control names can vary by Live version and locale. To verify one setting,
inspect its actual role, exact description/identifier, value and enabled state,
and take a fresh snapshot after any separately authorized change. A previous
JSON file or saved preferences file is historical evidence, not proof of the
running app's current state.

Finding a MIDI control in the tree does not by itself prove MIDI isolation or
wire behavior. The caller must positively identify the applicable port and
Track/Sync/Remote switches in the current snapshot; unknown or missing rows
remain unverified. The command intentionally does not interpret any AX row as
a MIDI pass/fail result.

Focused source checks:

```sh
.venv/bin/python -m pytest -q tests/test_ax_snapshot.py
swiftc -framework ApplicationServices -framework AppKit \
  codex_devtools/ax_snapshot.swift -o /tmp/codex-ax-snapshot
```

The Swift compile check does not query a running app. Native query results must
be collected by the coordinated desktop operator.
