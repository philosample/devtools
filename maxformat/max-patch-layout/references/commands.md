# Commands and review

## Existing patches

Score the input, then select the smallest mode that addresses the request. Use a new output path.

```sh
maxformat input.maxpat --check
maxformat input.maxpat out.maxpat --route-only
maxformat input.maxpat out.maxpat --keep-layout --no-triggers
```

`--route-only` keeps all object positions. `--keep-layout` tidies the existing arrangement. A full reflow can insert typed trigger objects and replace long cords with named send/receive pairs. Use `--no-triggers` to refuse a change to fan-out firing order without inserting a trigger. Review any connectivity and namespace changes before adopting a reflow. Use `--presentation` or `--theme` only when the requested change includes that view or styling.

## New patches

Describe the objects, cord endpoints, and modules in a JSON spec. Give each object a stable `id`. Use `text` for object boxes, `message` for message boxes, `ui` for controls, and `label` for short comments. Give modules descriptive `title` and `members`. Cords use zero-based port numbers such as `"pitch:0 -> oscillator:0"`. For unknown externals or argument-dependent objects, declare `inlets`, `outlets`, and `outlettype` explicitly.

```sh
maxformat spec.json out.maxpat --png
maxformat --object 'cycle~'
```

Review the score, advice and optional preview. Fix spec structure and regenerate when the result needs different grouping or semantic wiring. PNG previews use macOS Quick Look and can fall back to SVG. Compare actual parameter declarations, cords and fan-out order for changed paths. Verify native load and interaction when required by the project.
