"""Command line entry point for codex-devtools."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .maxfiles import decode_file, extract_archive, graph_report, inventory, sha256
from .deploy import apply_plan, make_plan, rollback


EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "outputs", "work"}


def _json_out(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _manifest(root: Path, destination: Path) -> dict:
    if not root.is_dir():
        raise ValueError(f"manifest root is not a directory: {root}")
    root_abs = root.resolve()
    output_abs = destination.resolve()
    files = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not (Path(current) / d).is_symlink())
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            if path.resolve() == output_abs:
                continue
            relative = path.relative_to(root).as_posix()
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes())})
    files.sort(key=lambda item: item["path"])
    return {"format": "codex-devtools-hash-manifest-v1", "root": ".", "files": files}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m codex_devtools", description="Max archive, source-tree, and explicit artifact deployment utilities")
    commands = parser.add_subparsers(dest="group", required=True)
    max_cmd = commands.add_parser("max", help="inspect, extract, or validate Max files")
    max_sub = max_cmd.add_subparsers(dest="action", required=True)
    inspect = max_sub.add_parser("inspect", help="inventory an MXF, AMXD, or Max JSON file")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    extract = max_sub.add_parser("extract", help="extract embedded archive entries byte-for-byte")
    extract.add_argument("path", type=Path)
    extract.add_argument("--out", required=True, type=Path)
    validate = max_sub.add_parser("validate", help="check JSON syntax and patch graph ports")
    validate.add_argument("path", type=Path)
    validate.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    package = max_sub.add_parser("package-m4l-candidate", help="build an unfrozen M4L MIDI-effect candidate with declared sidecars")
    package.add_argument("--source-root", required=True, type=Path, help="root constraining the source maxpat and dependency files")
    package.add_argument("--source", required=True, type=Path, help="M4L MIDI-effect source .maxpat (relative to source root or inside it)")
    package.add_argument("--template", required=True, type=Path, help="unfrozen raw-patcher MIDI-effect .amxd template")
    package.add_argument("--dependency-allowlist", required=True, type=Path, help="JSON array of {name, source} dependency entries")
    package.add_argument("--out", required=True, type=Path, help="new output directory; must not exist")
    preview = max_sub.add_parser("preview", help="inspect source geometry and optionally export a static SVG schematic")
    preview.add_argument("path", type=Path, help="Max patcher source (no Max/JSUI code is executed)")
    preview.add_argument("--out", type=Path, help="new SVG output path; refuses overwrite")
    hash_cmd = commands.add_parser("hash", help="hash a source tree")
    hash_sub = hash_cmd.add_subparsers(dest="action", required=True)
    manifest = hash_sub.add_parser("manifest", help="write a deterministic source-file hash manifest")
    manifest.add_argument("path", type=Path)
    manifest.add_argument("--out", required=True, type=Path)
    deploy = commands.add_parser("deploy", help="plan, apply, or roll back listed artifact deployment")
    deploy_sub = deploy.add_subparsers(dest="action", required=True)
    plan = deploy_sub.add_parser("plan", help="hash listed sources and current destinations without writing to destinations")
    plan.add_argument("manifest", type=Path)
    plan.add_argument("--source-root", required=True, type=Path)
    plan.add_argument("--destination-root", required=True, type=Path)
    plan.add_argument("--out", required=True, type=Path)
    apply = deploy_sub.add_parser("apply", help="apply an unchanged plan and preserve overwritten files")
    apply.add_argument("plan", type=Path)
    apply.add_argument("--backup-root", required=True, type=Path)
    undo = deploy_sub.add_parser("rollback", help="restore files from an apply receipt")
    undo.add_argument("receipt", type=Path)
    midi = commands.add_parser("midi", help="capture MIDI Monitor input or inspect/run isolated transport checks")
    midi_sub = midi.add_subparsers(dest="action", required=True)
    midi_sub.add_parser("endpoints", help="read-only CoreMIDI inventory (macOS + Swift)")
    template = midi_sub.add_parser("template", help="create a local single-source capture template")
    template.add_argument("--source", required=True, help="exact source name or displayName")
    template.add_argument("--out", required=True, type=Path)
    report = midi_sub.add_parser("report", help="summarize a saved capture and optionally assert event counts")
    report.add_argument("path", type=Path)
    report.add_argument("--expect", type=Path)
    report.add_argument("--receipt", type=Path, help="verify the matching capture receipt and exact source identity")
    export = midi_sub.add_parser("export", help="decode a saved capture without opening any app")
    export.add_argument("path", type=Path)
    export.add_argument("--out", required=True, type=Path)
    export.add_argument("--format", choices=["json", "csv"], default="json")
    capture = midi_sub.add_parser("capture", help="explicit macOS GUI capture in an isolated document")
    capture.add_argument("--template", required=True, type=Path)
    capture.add_argument("--out", required=True, type=Path, help="new run directory; refuses existing paths")
    capture.add_argument("--seconds", required=True, type=float)
    capture.add_argument("--max-events", type=int, default=100000)
    loopback = midi_sub.add_parser("loopback", help="send an isolated CoreMIDI fixture to a private virtual destination")
    loopback.add_argument("--out", required=True, type=Path, help="new local JSON evidence file; refuses overwrite")
    loopback.add_argument("--timeout", type=float, default=3.0, help="callback wait bound in seconds (0.05–30)")
    runtime = commands.add_parser("runtime", help="discover existing local runtimes")
    runtime_sub = runtime.add_subparsers(dest="action", required=True)
    node = runtime_sub.add_parser("node", help="locate and verify an existing Node.js executable")
    node.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    node.add_argument("--app", type=Path, help="Ableton Live.app or Max.app bundle to check after PATH")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.group == "runtime" and args.action == "node":
            from .runtime import discover_node
            result = discover_node(args.app)
            if args.json:
                _json_out(result)
            elif result["available"]:
                print(f"Node {result['version']}: {result['path']} ({result['provenance']})")
            else:
                print(result["error"], file=sys.stderr)
            return 0 if result["available"] else 2
        if args.group == "midi":
            from .midi_monitor import parse_document, write_json, write_csv
            if args.action in ("endpoints", "template"):
                from .midi_capture import endpoints, make_template
                _json_out(endpoints() if args.action == "endpoints" else make_template(args.out, args.source))
                return 0
            if args.action == "report":
                from .midi_assertions import analyze, check_expectations, verify_capture_receipt
                document = parse_document(args.path)
                result = {
                    "provenance": {
                        "document": str(args.path),
                        "sha256": document["document_sha256"],
                        "parser_sha256": sha256(Path(__file__).with_name("midi_monitor.py").read_bytes()),
                        "midi_monitor_document_version": document["version"],
                        "event_count": len(document["events"]),
                    },
                    "analysis": analyze(document["events"]),
                }
                if args.receipt:
                    result["provenance"]["receipt"] = verify_capture_receipt(args.path, document, args.receipt)
                if args.expect:
                    result["assertions"] = check_expectations(document["events"], json.loads(args.expect.read_text()))
                if result["provenance"].get("receipt", {}).get("status") == "inconclusive":
                    result["provenance"]["capture_gate"] = "inconclusive: event buffer reached its configured limit"
                    result.setdefault("assertions", {"passed": False, "checks": []})
                    result["assertions"]["passed"] = False
                _json_out(result)
                return 0 if result.get("assertions", {}).get("passed", True) else 1
            if args.action == "export":
                if args.out.exists():
                    raise ValueError("export output already exists")
                document = parse_document(args.path)
                (write_json if args.format == "json" else write_csv)(document, args.out)
                print(f"exported {len(document['events'])} events: {args.out}")
                return 0
            if args.action == "capture":
                from .midi_capture import capture
                result = capture(args.template, args.out, args.seconds, args.max_events)
                _json_out(result)
                return 1 if result["status"] != "captured" else 0
            if args.action == "loopback":
                from .midi_loopback import loopback
                result = loopback(args.out, args.timeout)
                _json_out(result)
                return 0 if result["status"] == "passed" else 1
        if args.group == "max" and args.action == "inspect":
            report = inventory(args.path)
            if args.json:
                _json_out(report)
            else:
                print(f"{report['format']} {args.path}: {report['bytes']} bytes, sha256 {report['sha256']}")
                print(f"archive entries: {report['entry_count']}")
                if report["graph"] is not None:
                    counts = report["graph"]["counts"]
                    print(f"patchers: {counts['patchers']}, boxes: {counts['boxes']}, lines: {counts['lines']}")
                    for error in report["graph"]["errors"]:
                        print(f"graph error: {error}")
            return 1 if report["graph"] and report["graph"]["errors"] else 0
        if args.group == "max" and args.action == "extract":
            result = extract_archive(args.path, args.out)
            _json_out({"out": str(args.out), "entries": result})
            return 0
        if args.group == "max" and args.action == "validate":
            raw = args.path.read_bytes()
            fmt, entries, patch = decode_file(raw)
            if patch is None:
                raise ValueError("archive root payload is not a Max JSON patch")
            graph = graph_report(patch)
            result = {"path": str(args.path), "format": fmt, "entry_count": len(entries), **graph}
            if args.json:
                _json_out(result)
            else:
                counts = graph["counts"]
                print(f"{fmt}: {counts['patchers']} patchers, {counts['boxes']} boxes, {counts['lines']} lines")
                for error in graph["errors"]:
                    print(f"error: {error}")
                if not graph["errors"]:
                    print("OK")
            return 1 if graph["errors"] else 0
        if args.group == "max" and args.action == "package-m4l-candidate":
            from .m4l_package import package_m4l_candidate
            manifest = package_m4l_candidate(args.source_root, args.source, args.template,
                                             args.dependency_allowlist, args.out)
            _json_out({"out": str(args.out.expanduser().absolute()), "manifest": manifest})
            return 0
        if args.group == "max" and args.action == "preview":
            from .max_preview import preview as max_preview
            result = max_preview(args.path, args.out)
            _json_out(result)
            return 0
        if args.group == "hash" and args.action == "manifest":
            result = _manifest(args.path, args.out)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"wrote {len(result['files'])} file hashes to {args.out}")
            return 0
        if args.group == "deploy" and args.action == "plan":
            output = args.out.resolve(strict=False)
            if output == args.manifest.resolve(strict=False):
                raise ValueError("plan output must not overwrite the deployment manifest")
            result = make_plan(args.manifest, args.source_root, args.destination_root)
            for root_arg in (args.source_root.resolve(), args.destination_root.resolve(strict=False)):
                try:
                    output.relative_to(root_arg)
                except ValueError:
                    continue
                raise ValueError("plan output must be outside source and destination roots")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"planned {len(result['files'])} files: {args.out}")
            return 0
        if args.group == "deploy" and args.action == "apply":
            receipt = apply_plan(args.plan, args.backup_root)
            print(f"applied {args.plan}; receipt: {receipt}")
            return 0
        if args.group == "deploy" and args.action == "rollback":
            rollback(args.receipt)
            print(f"rolled back deployment: {args.receipt}")
            return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
