# Commands and review

## Existing patches

Score the input, then select the smallest mode that addresses the request. Use a new output path.

```sh
maxformat input.maxpat --check
maxformat input.maxpat out.maxpat --route-only
maxformat input.maxpat out.maxpat --keep-layout --no-triggers
```

`--route-only` keeps all object positions. `--keep-layout` tidies the existing arrangement. A full reflow can insert typed trigger objects and replace long cords with named send/receive pairs. Use `--no-triggers` to refuse a change to fan-out firing order without inserting a trigger. Review any connectivity and namespace changes before adopting a reflow. Use `--presentation` or `--theme` only when the requested change includes that view or styling.

`--check` can score an existing patch whose box IDs fall outside the formatter's rewrite rules. Read its diagnostics before routing a dense graph. The formatter requires each patcher's box IDs to be unique `obj-<number>` values before it writes a candidate. Preserve named IDs owned by an existing device or generator.

## Before and after images

From the MaxFormat release directory, render an existing source or candidate without changing it:

```sh
python3 -m maxlayout.render input.maxpat -o before.svg --png
python3 -m maxlayout.render out.maxpat -o after.svg --png
```

Use the same patcher and view for both images. Add `--presentation` for the stored presentation geometry or `--subpatcher obj-12` for an embedded view. If the ID is reused in different patchers, use the full path printed in the error. For a dense or unusually tall view, retain the scalable SVG and export the relevant embedded views separately. Quick Look PNGs are bounded thumbnails and the SVG remains available if conversion fails. For device screenshots, use the owning project's native Max or Live procedure and pin the loaded artifact.

## New patches

Describe the objects, cord endpoints, and modules in a JSON spec. Give each object a stable `id`. Use `text` for object boxes, `message` for message boxes, `ui` for controls, and `label` for short comments. Give modules descriptive `title` and `members`. Cords use zero-based port numbers such as `"pitch:0 -> oscillator:0"`. For unknown externals or argument-dependent objects, declare `inlets`, `outlets`, and `outlettype` explicitly.

```sh
maxformat spec.json out.maxpat --png
maxformat --object 'cycle~'
```

Review the score, advice and optional preview. Fix spec structure and regenerate when the result needs different grouping or semantic wiring. PNG previews use macOS Quick Look and can fall back to SVG. Compare actual parameter declarations, cords and fan-out order for changed paths. Verify native load and interaction when required by the project.
