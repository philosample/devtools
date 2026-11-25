"""maxformat.py - make Max patches clean and readable.

  maxformat.py input.maxpat output.maxpat     lay out a patch: sources on top,
                                              everything they feed below them
  maxformat.py input.maxpat --check           just score it
  maxformat.py spec.json output.maxpat        build a new patch from a spec

The input file is never changed. Nothing is written if the result would
behave differently from the input.
"""

import argparse
import copy
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import build, layout, lint, present, render, route, style
from .maxpat import ID_RE, Patcher, describe, load, read_json, save, walk_patchers


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="maxformat.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "input", nargs="?", help="a .maxpat to clean up, or a .json spec to build from"
    )
    ap.add_argument("output", nargs="?", help="where to write the result")
    ap.add_argument(
        "--check", action="store_true", help="only score the input; write nothing"
    )
    ap.add_argument(
        "--keep-layout",
        action="store_true",
        help="keep the input's arrangement; just tidy it (grid, spacing, cords)",
    )
    ap.add_argument(
        "--route-only",
        action="store_true",
        help="only straighten cords; never move boxes",
    )
    ap.add_argument(
        "--no-triggers",
        action="store_true",
        help="never add [t] objects; refuse instead if execution order would change",
    )
    ap.add_argument(
        "--presentation", action="store_true", help="also lay out presentation mode"
    )
    ap.add_argument(
        "--theme",
        choices=["pcb", "graphite", "light"],
        help="colour the patch (default: leave colours alone)",
    )
    ap.add_argument(
        "--png",
        action="store_true",
        help="also save a preview image next to the output",
    )
    ap.add_argument(
        "--object",
        metavar="NAME",
        help="describe a Max object and what its inlets/outlets carry (e.g. --object cycle~)",
    )
    a = ap.parse_args(argv)
    if a.object:
        return a
    if not a.input:
        ap.error("give an input file (or --object NAME)")
    if not os.path.exists(a.input):
        ap.error(f"no such file: {a.input}")
    if not a.check and not a.output:
        ap.error("give an output file (or use --check)")
    if not a.check:
        outputs = [a.output]
        if a.png:
            outputs.extend(preview_paths(a.output))
        try:
            render.check_output_paths(a.input, outputs)
        except ValueError as exc:
            ap.error(str(exc))
    return a


def bar(score):
    n = round(score / 5)
    return "█" * n + "░" * (20 - n)


def preview_paths(out):
    stem = os.path.splitext(out)[0]
    return Path(stem + ".svg"), Path(stem + ".png")


def save_png(doc, out):
    svg, png = preview_paths(out)
    patcher = Patcher(doc["patcher"])
    with tempfile.TemporaryDirectory() as directory:
        thumbnail = Path(directory) / "preview.svg"
        thumbnail.write_text(render.render(patcher, square=True), encoding="utf-8")
        if render.to_png(thumbnail, png):
            print(f"  preview {png}")
            return
    svg.write_text(render.render(patcher), encoding="utf-8")
    print(f"  preview {svg}")


def unsupported_input_ids(doc):
    issues = []
    for patcher in walk_patchers(doc["patcher"]):
        counts = Counter(box.id for box in patcher.boxes)
        for box_id, count in counts.items():
            if count > 1 or not ID_RE.fullmatch(str(box_id)):
                issues.append(f"{patcher.path}: {box_id!r}")
    return issues


def dangling_patchlines(doc):
    issues = []
    for patcher in walk_patchers(doc["patcher"]):
        for entry in patcher.d.get("lines", []):
            line = entry["patchline"]
            missing = [
                f"{end} {line[end][0]!r}"
                for end in ("source", "destination")
                if line[end][0] not in patcher.by_id
            ]
            if missing:
                issues.append(f"{patcher.path}: missing {', '.join(missing)}")
    return issues


def route_document(doc):
    incomplete = {}
    for patcher in walk_patchers(doc["patcher"]):
        _, failed = route.route_patcher(patcher)
        if failed:
            incomplete[patcher.path] = failed
    return incomplete


def is_spec(path):
    if not path.lower().endswith(".json"):
        return False
    try:
        d = read_json(path)
    except ValueError:
        return False
    return isinstance(d, dict) and "objects" in d


def build_from_spec(a):
    spec = read_json(a.input)
    if a.theme:
        spec["theme"] = a.theme
    doc, ids = build.build(spec, name=os.path.splitext(os.path.basename(a.output))[0])
    res = lint.lint_doc(doc, presentation=True, min_gap=build.STYLE["min_gap"])
    save(doc, a.output)
    print(f"  built   {bar(res['score'])}  {res['score']:5.1f}   ({len(ids)} objects)")
    for tip in res["advice"]:
        print(f"  advice: {tip}")
    print(f"✓ wrote {a.output}")
    if a.png:
        save_png(doc, a.output)
    return 0


def main(argv=None):
    a = parse_args(argv)
    if a.object:
        print(describe(a.object))
        return 0
    if is_spec(a.input):
        if a.check:
            sys.exit("--check takes a .maxpat, not a spec")
        return build_from_spec(a)

    try:
        original = load(a.input)
    except (ValueError, UnicodeDecodeError):
        original = None
    if not isinstance(original, dict) or not isinstance(original.get("patcher"), dict):
        sys.exit(
            f"{a.input} is not a .maxpat (Max for Live .amxd files must be saved as .maxpat first)"
        )
    missing_endpoints = dangling_patchlines(original)
    if missing_endpoints:
        print("invalid patch: patchlines reference missing boxes:", file=sys.stderr)
        for issue in missing_endpoints[:6]:
            print(f"  {issue}", file=sys.stderr)
        return 1
    input_id_issues = unsupported_input_ids(original)
    if input_id_issues:
        action = "check only" if a.check else "not saved"
        print(
            f"{action}: input already contains box IDs outside the unique "
            "obj-<number> format required for rewriting:",
            file=sys.stderr,
        )
        for issue in input_id_issues[:6]:
            print(f"  {issue}", file=sys.stderr)
        if not a.check:
            return 1

    before = lint.lint_doc(original)
    if a.check:
        lint.print_report(before)
        return 0

    doc = copy.deepcopy(original)
    added = 0
    if not a.route_only:
        mode = "tidy" if a.keep_layout else "flow"
        problems, added = layout.layout_doc(
            doc,
            mode,
            fanout="keep" if a.no_triggers else "trigger",
            hgap=build.STYLE["side_gap"],
            vgap=build.STYLE["row_gap"],
        )
        if problems:
            print(
                "✗ not saved: this would change the order cords fire in:",
                file=sys.stderr,
            )
            for p in problems:
                print("   " + p, file=sys.stderr)
            print(
                "  run without --no-triggers to make the order explicit with [t] objects",
                file=sys.stderr,
            )
            return 1
    incomplete_routes = route_document(doc)
    sends = 0
    if not a.route_only and not a.keep_layout:
        # cords bigger than a screen (or crossing 3+) become a named [s]/[r] pair
        for P in walk_patchers(doc["patcher"]):
            sends += layout.insert_sends(P, [ln for ln, _ in lint.long_cords(P)])
        if sends:
            problems, more = layout.layout_doc(
                doc,
                "flow",
                fanout="keep" if a.no_triggers else "trigger",
                hgap=build.STYLE["side_gap"],
                vgap=build.STYLE["row_gap"],
            )
            if problems:
                print(
                    "✗ not saved: this would change the order cords fire in:",
                    file=sys.stderr,
                )
                for p in problems:
                    print("   " + p, file=sys.stderr)
                return 1
            added += more
            incomplete_routes = route_document(doc)
    if a.theme:
        style.apply(doc, a.theme)
    if a.presentation:
        present.present(Patcher(doc["patcher"]), auto=True)

    after = lint.lint_doc(doc, before=original)
    changed = [s for v in after["behaviour"].values() for s in v]
    if changed:
        print("✗ not saved: the result would behave differently:", file=sys.stderr)
        for s in changed[:6]:
            print("   " + s, file=sys.stderr)
        return 1

    save(doc, a.output)
    print(f"  before  {bar(before['score'])}  {before['score']:5.1f}")
    print(f"  after   {bar(after['score'])}  {after['score']:5.1f}")
    for tip in after["advice"]:
        print(f"  advice: {tip}")
    if added:
        print(f"  added {added} [t] object(s) to keep execution order")
    if sends:
        print(f"  replaced {sends} long cord(s) with a named [s]/[r] pair")
    for patcher_path, failed in incomplete_routes.items():
        print(
            f"  incomplete routing: {patcher_path}: {failed} "
            f"{'cord' if failed == 1 else 'cords'} could not be routed",
            file=sys.stderr,
        )
    print(f"✓ wrote {a.output}")
    if a.png:
        save_png(doc, a.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
