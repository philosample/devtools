# Static Max geometry preview

Inspect a source patch without launching Max:

```sh
python3 -m codex_devtools max preview path/to/device.maxpat
```

Export a static SVG schematic into a new path:

```sh
python3 -m codex_devtools max preview path/to/device.maxpat --out /tmp/device-layout.svg
```

The command reports source format, byte count and SHA-256. It visits the root patcher and inline nested patchers. Per patcher, it uses `presentation_rect` for boxes marked `presentation: 1` when any such boxes exist; otherwise it uses `patching_rect`. Reports include the selected geometry mode, rectangle bounds, invalid or missing rectangles, and intersections between positive-area bounding rectangles. The SVG embeds the source hash, source-derived labels, and box rectangles. Labels come from stored object text, then a saved parameter long name, then the Max class name.

The SVG is a plain schematic. It does not execute Max, JSUI, abstractions, JavaScript or externals; draw cables; reproduce native widget rendering, text metrics, colors, or runtime state; or claim the patch opens correctly. Overlaps describe stored box rectangles only, not rendered-text collision or semantic wiring. Output paths refuse overwrites. No files are written when `--out` is omitted.

## Reuse rationale

The local `work/collect_surface_previews.py` is a one-off studio collage builder: it binds several domain-specific source files, labels, captions, and value extraction rules, then injects data into a pre-existing HTML page. It is useful as a lead for selecting presentation rectangles and carrying source provenance, but it is not a reusable Max renderer. The inspected TD-3 repository has a device-specific `tools/build_surface.py` preview path, and Pro-3 tracks source-preview assets and their device-specific build work. Those generators remain domain-owned. This command stays generic and source-only: it draws stored box geometry with source-derived labels and does not copy device-specific paint or data extraction.
