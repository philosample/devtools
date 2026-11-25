# Commands and review

## Existing patches

Score the input, then select the smallest mode that addresses the request. Use a new output path.

```sh
maxformat input.maxpat --check
maxformat input.maxpat out.maxpat --route-only
maxformat input.maxpat out.maxpat --keep-layout --no-triggers
```

`--route-only` keeps all object positions. `--keep-layout` tidies the existing arrangement. A full reflow can insert typed trigger objects and replace long cords with named send/receive pairs. Use `--no-triggers` to refuse a change to fan-out firing order without inserting a trigger. Review any connectivity and namespace changes before adopting a reflow. Use `--presentation` or `--theme` only when the requested change includes that view or styling.

`--check` can score a graph with existing box IDs outside the formatter's rewrite rules. It refuses a cord whose source or destination box is missing from the same patcher. The formatter requires each patcher's box IDs to be unique `obj-<number>` values before writing a candidate. Fix graph defects or migrate IDs in the project's editable source or generator, keeping stable Live parameter IDs.

Read standard error along with the score. An incomplete-routing diagnostic gives the patcher and count of cords the router could not place. Inspect those paths in the candidate before accepting it. If a dense patch exceeds the time available, keep its source and render the relevant embedded views. Record an unchanged baseline or a declined candidate explicitly.

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
