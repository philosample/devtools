"""Command line entry point for codex-devtools."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .maxfiles import decode_file, extract_archive, graph_report, inventory, sha256


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
    parser = argparse.ArgumentParser(prog="python3 -m codex_devtools", description="Read-only Max archive and source-tree utilities")
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
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
