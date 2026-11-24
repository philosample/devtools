# Static inline input preflight

```sh
python3 -m codex_devtools max validate selected.amxd --native-inline-inputs --json
```

This opt-in check addresses a narrow saved-graph defect: a `pcontrol` patchcord
can reference a declared subpatch input even when no actual inlet object exists.
Cycling ’74 documents that [patcher inputs are determined by contained inlet
objects](https://docs.cycling74.com/reference/patcher/), and each
[`inlet`](https://docs.cycling74.com/reference/inlet/) supplies an input on its
parent object. This command reads existing files only; it does not compile or
execute Max/Live, execute JavaScript, open a window, or rewrite an artifact.

## Covered representation and scope

The existing reader decodes JSON, supported AMXD or MXF files. The existing
patcher walker visits root and inline nested patchers. Within each patcher,
the additional check inspects outgoing cords from a `newobj` whose operator is
`pcontrol` to a `newobj` whose operator is `p` or `patcher`. Such a target must
contain a saved inline `patcher` object in the default/`box` namespace.

Only **direct child boxes with `maxclass: "inlet"`** are supported as actual
inputs. Their IDs must be present, unique strings. Saved `outlettype` does not
change their count; message/signal compatibility is not assessed. Child `numinlets`
declarations, comments and nested grandchildren do not create inputs. The number
of dedicated inlet boxes determines the available zero-based ordinal range;
the target's declared `numinlets` must agree. A `pcontrol` cord must use source
outlet 0 and a strict integer target ordinal in that range. JSON booleans are
not ordinals or counts.

This checks ordinal **existence**, not which child inlet has a particular role.
The report's `inlet_box_ids` preserve saved box-array order for inspection; that
list is not an ordinal-to-ID mapping. No ordering is inferred from IDs, array
position, coordinates or saved index hints.

An inline target with a missing/malformed child, unsupported namespace, malformed
child boxes or ambiguous inlet IDs fails explicitly. Alternate saved forms such
as `newobj` text `inlet`, plus operators/classes `inlet~`, `in`, `in~`,
`mc.inlet~`, `mc.in~` and `mcs.in~`, are
reported as unsupported rather than counted or assigned guessed semantics. Their
presence makes `actual_numinlets` unknown and fails this preflight, even if other
dedicated inlet boxes are present. This conservative supported subset
does not assert that those other forms are invalid Max patches.

`bpatcher`, external/abstraction targets, dynamic JS targets and other target
operators are explicitly listed in `excluded`; their inputs are not checked by
this rule. The normal graph validation still applies. The rule does not inspect
JS includes/handlers, infer dynamic script ports, evaluate `thispatcher` mutation,
resolve runtime abstractions, or validate signal compilation.

## Report and exit status

Without `--native-inline-inputs`, default reports, formatting and exit behavior
remain unchanged. With the flag, JSON gains `native_inline_inputs` containing
`checked`, `excluded` and `errors` arrays. Each inspected target cord records its
patcher path, line index, source/target IDs, target ordinal, declared count and
actual dedicated count (or `null` when unsupported). Preflight errors also join
the normal top-level `errors` list. Text mode reports inspected/excluded cord
counts and errors.

Exit 0 means no reported graph/preflight error, 1 means validation errors, and 2
means an input/decode error. Zero inspected cords is **no coverage of this rule**,
not successful native port validation. Review exclusions and counts before
relying on a result. Default validation may accept a declared-only input; the
opt-in check must reject it.

Passing this source preflight does not establish native compilation, actual port
counts after runtime initialization, click delivery, `pcontrol` receipt, window
visibility, dependencies, M4L compatibility, timing, MIDI or hardware effects.
Retain exact source/artifact hashes and verify those gates separately under the
owning project's native procedure.

Focused regression command:

```sh
.venv/bin/python -m pytest -q tests/test_max_inline_inputs.py
```
