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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
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
        if args.group == "hash" and args.action == "manifest":
            result = _manifest(args.path, args.out)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"wrote {len(result['files'])} file hashes to {args.out}")
            return 0
        if args.group == "deploy" and args.action == "plan":
            result = make_plan(args.manifest, args.source_root, args.destination_root)
            output = args.out.resolve(strict=False)
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
