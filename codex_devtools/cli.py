"""Command line entry point for codex-devtools."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .deploy import apply_plan, make_plan, rollback, verify_plan
from .maxfiles import decode_file, extract_archive, graph_report, inventory, sha256

EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "outputs",
    "work",
}


def _json_out(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _manifest(root: Path, destination: Path) -> dict:
    if not root.is_dir():
        raise ValueError(f"manifest root is not a directory: {root}")
    root.resolve()
    output_abs = destination.resolve()
    files = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in EXCLUDED_DIRS and not (Path(current) / d).is_symlink()
        )
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            if path.resolve() == output_abs:
                continue
            relative = path.relative_to(root).as_posix()
            files.append(
                {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path.read_bytes()),
                }
            )
    files.sort(key=lambda item: item["path"])
    return {"format": "codex-devtools-hash-manifest-v1", "root": ".", "files": files}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m codex_devtools",
        description="Max archive, source-tree, and explicit artifact deployment utilities",
    )
    commands = parser.add_subparsers(dest="group", required=True)
    max_cmd = commands.add_parser("max", help="inspect, extract, or validate Max files")
    max_sub = max_cmd.add_subparsers(dest="action", required=True)
    inspect = max_sub.add_parser(
        "inspect", help="inventory an MXF, AMXD, or Max JSON file"
    )
    inspect.add_argument("path", type=Path)
    inspect.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    extract = max_sub.add_parser(
        "extract", help="extract embedded archive entries byte-for-byte"
    )
    extract.add_argument("path", type=Path)
    extract.add_argument("--out", required=True, type=Path)
    validate = max_sub.add_parser(
        "validate", help="check JSON syntax and patch graph ports"
    )
    validate.add_argument("path", type=Path)
    validate.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    validate.add_argument(
        "--native-inline-inputs",
        action="store_true",
        help="preflight pcontrol cords to actual dedicated inlets in inline p subpatches",
    )
    package = max_sub.add_parser(
        "package-m4l-candidate",
        help="build an unfrozen M4L MIDI-effect candidate with declared sidecars",
    )
    package.add_argument(
        "--source-root",
        required=True,
        type=Path,
        help="root constraining the source maxpat and dependency files",
    )
    package.add_argument(
        "--source",
        required=True,
        type=Path,
        help="M4L MIDI-effect source .maxpat (relative to source root or inside it)",
    )
    package.add_argument(
        "--template",
        required=True,
        type=Path,
        help="unfrozen raw-patcher MIDI-effect .amxd template",
    )
    package.add_argument(
        "--dependency-allowlist",
        required=True,
        type=Path,
        help="JSON array of {name, source} dependency entries",
    )
    package.add_argument(
        "--out", required=True, type=Path, help="new output directory; must not exist"
    )
    preview = max_sub.add_parser(
        "preview",
        help="inspect source geometry and optionally export a static SVG schematic",
    )
    preview.add_argument(
        "path", type=Path, help="Max patcher source (no Max/JSUI code is executed)"
    )
    preview.add_argument(
        "--out", type=Path, help="new SVG output path; refuses overwrite"
    )
    hash_cmd = commands.add_parser("hash", help="hash a source tree")
    hash_sub = hash_cmd.add_subparsers(dest="action", required=True)
    manifest = hash_sub.add_parser(
        "manifest", help="write a deterministic source-file hash manifest"
    )
    manifest.add_argument("path", type=Path)
    manifest.add_argument("--out", required=True, type=Path)
    deploy = commands.add_parser(
        "deploy", help="plan, apply, verify, or roll back listed artifact deployment"
    )
    deploy_sub = deploy.add_subparsers(dest="action", required=True)
    plan = deploy_sub.add_parser(
        "plan",
        help="hash listed sources and current destinations without writing to destinations",
    )
    plan.add_argument("manifest", type=Path)
    plan.add_argument("--source-root", required=True, type=Path)
    plan.add_argument("--destination-root", required=True, type=Path)
    plan.add_argument("--out", required=True, type=Path)
    apply = deploy_sub.add_parser(
        "apply", help="apply an unchanged plan and preserve overwritten files"
    )
    apply.add_argument("plan", type=Path)
    apply.add_argument("--backup-root", required=True, type=Path)
    verify = deploy_sub.add_parser(
        "verify",
        help="check planned source and deployed destination hashes without writing",
    )
    verify.add_argument("plan", type=Path)
    undo = deploy_sub.add_parser("rollback", help="restore files from an apply receipt")
    undo.add_argument("receipt", type=Path)
    midi = commands.add_parser(
        "midi",
        help="capture MIDI Monitor input or inspect/run isolated transport checks",
    )
    midi_sub = midi.add_subparsers(dest="action", required=True)
    midi_sub.add_parser(
        "endpoints", help="read-only CoreMIDI inventory (macOS + Swift)"
    )
    template = midi_sub.add_parser(
        "template", help="create a local single-source capture template"
    )
    template.add_argument(
        "--source", required=True, help="exact source name or displayName"
    )
    template.add_argument("--out", required=True, type=Path)
    report = midi_sub.add_parser(
        "report", help="summarize a saved capture and optionally assert event counts"
    )
    report.add_argument("path", type=Path)
    report.add_argument("--expect", type=Path)
    report.add_argument(
        "--receipt",
        type=Path,
        help="verify the matching capture receipt and exact source identity",
    )
    export = midi_sub.add_parser(
        "export", help="decode a saved capture without opening any app"
    )
    export.add_argument("path", type=Path)
    export.add_argument("--out", required=True, type=Path)
    export.add_argument("--format", choices=["json", "csv"], default="json")
    capture = midi_sub.add_parser(
        "capture", help="explicit macOS GUI capture in an isolated document"
    )
    capture.add_argument("--template", required=True, type=Path)
    capture.add_argument(
        "--out",
        required=True,
        type=Path,
        help="new run directory; refuses existing paths",
    )
    capture.add_argument("--seconds", required=True, type=float)
    capture.add_argument("--max-events", type=int, default=100000)
    loopback = midi_sub.add_parser(
        "loopback",
        help="send an isolated CoreMIDI fixture to a private virtual destination",
    )
    loopback.add_argument(
        "--out",
        required=True,
        type=Path,
        help="new local JSON evidence file; refuses overwrite",
    )
    loopback.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="callback wait bound in seconds (0.05–30)",
    )
    ax = commands.add_parser(
        "ax", help="inspect macOS Accessibility and perform explicit bounded controls"
    )
    ax_sub = ax.add_subparsers(dest="action", required=True)
    ax_snapshot = ax_sub.add_parser(
        "snapshot",
        help="snapshot one exact PID without UI actions or permission prompts",
    )
    ax_snapshot.add_argument("--pid", type=int, required=True)
    ax_snapshot.add_argument(
        "--window-title", help="descend into exactly one window with this exact title"
    )
    ax_snapshot.add_argument("--max-elements", type=int, default=500)
    ax_snapshot.add_argument("--max-depth", type=int, default=10)
    ax_verify = ax_sub.add_parser(
        "verify-studio-capture",
        help="assert exact Studio Capture switch values from three saved AX snapshots",
    )
    ax_verify.add_argument("before", type=Path)
    ax_verify.add_argument("isolated", type=Path)
    ax_verify.add_argument("restored", type=Path)
    ax_verify.add_argument("--expected-bundle-id", required=True)
    ax_verify.add_argument(
        "--max-age-seconds",
        type=float,
        help="require all snapshots to be no older than this at verification time",
    )
    lifecycle = ax_sub.add_parser(
        "live-set-lifecycle",
        help="inspect or explicitly save, quit and reopen one exact Live Set",
    )
    lifecycle.add_argument("--pid", required=True, type=int)
    lifecycle.add_argument("--expected-bundle-id", required=True)
    lifecycle.add_argument("--source-set", required=True, type=Path)
    lifecycle.add_argument("--destination-set", type=Path)
    lifecycle.add_argument(
        "--app", type=Path, help="exact Live.app bundle URL for reopen"
    )
    lifecycle.add_argument(
        "--execute",
        action="store_true",
        help="perform guarded Save As, Quit and LaunchServices reopen; default only inspects",
    )
    set_open = ax_sub.add_parser(
        "live-set-open",
        help="inspect or explicitly open a pinned existing Set in the same Live process",
    )
    set_open.add_argument("--pid", required=True, type=int)
    set_open.add_argument("--expected-bundle-id", required=True)
    set_open.add_argument("--source-set", required=True, type=Path)
    set_open.add_argument("--destination-set", required=True, type=Path)
    set_open.add_argument("--source-sha256", required=True)
    set_open.add_argument("--destination-sha256", required=True)
    set_open.add_argument("--app", required=True, type=Path)
    set_open.add_argument("--save-owned-source", action="store_true")
    set_open.add_argument("--backup-root", type=Path)
    set_open.add_argument("--execute", action="store_true")
    save_as = ax_sub.add_parser(
        "live-set-save-as",
        help="preserve an owned Set through one same-process Save As without quit/reopen",
    )
    save_as.add_argument("--pid", required=True, type=int)
    save_as.add_argument("--expected-bundle-id", required=True)
    save_as.add_argument("--source-set", required=True, type=Path)
    save_as.add_argument("--source-sha256", required=True)
    save_as.add_argument("--destination-directory", required=True, type=Path)
    save_as.add_argument("--destination-filename", required=True)
    save_as.add_argument("--expected-final-set", required=True, type=Path)
    save_as.add_argument("--app", required=True, type=Path)
    save_as.add_argument("--backup-root", required=True, type=Path)
    save_as.add_argument("--project-required-dialog", action="store_true")
    save_as.add_argument("--execute", action="store_true")
    midi_sync = ax_sub.add_parser(
        "live-midi-sync",
        help="inspect or explicitly isolate and restore exact MIDI Output Sync controls",
    )
    midi_sync.add_argument("--pid", type=int, required=True)
    midi_sync.add_argument("--expected-bundle-id", required=True)
    midi_sync.add_argument("--expected-count", type=int, required=True)
    midi_sync.add_argument("--baseline", type=Path)
    midi_sync.add_argument(
        "--out", type=Path, help="new baseline JSON path in inspect mode"
    )
    midi_sync.add_argument(
        "--receipt", type=Path, help="new structured receipt path in execute mode"
    )
    midi_sync.add_argument("--action", dest="operation", choices=("isolate", "restore"))
    midi_sync.add_argument(
        "--execute",
        action="store_true",
        help="allow the selected isolate/restore action",
    )
    midi_sync.add_argument("--max-baseline-age-seconds", type=float, default=300.0)
    midi_inputs = ax_sub.add_parser(
        "live-midi-inputs",
        help="inspect or explicitly isolate and restore MIDI input Track, Sync and Remote Control flags",
    )
    midi_inputs.add_argument("--pid", type=int, required=True)
    midi_inputs.add_argument("--expected-bundle-id", required=True)
    midi_inputs.add_argument("--expected-count", type=int, required=True)
    midi_inputs.add_argument("--baseline", type=Path)
    midi_inputs.add_argument(
        "--out", type=Path, help="new baseline JSON path in inspect mode"
    )
    midi_inputs.add_argument(
        "--receipt", type=Path, help="new structured receipt path in execute mode"
    )
    midi_inputs.add_argument(
        "--action", dest="operation", choices=("isolate", "restore")
    )
    midi_inputs.add_argument(
        "--execute",
        action="store_true",
        help="allow the selected isolate/restore action",
    )
    midi_inputs.add_argument("--max-baseline-age-seconds", type=float, default=300.0)
    live_transport = ax_sub.add_parser(
        "live-transport",
        help="inspect or explicitly play briefly or stop one exact Live Set",
    )
    live_transport.add_argument("--pid", required=True, type=int)
    live_transport.add_argument("--expected-bundle-id", required=True)
    live_transport.add_argument("--set", dest="set_path", required=True, type=Path)
    live_transport.add_argument("--action", dest="operation", choices=("play", "stop"))
    live_transport.add_argument("--play-for-seconds", type=int)
    live_transport.add_argument("--expected-output-count", type=int)
    live_transport.add_argument("--receipt", type=Path)
    live_transport.add_argument(
        "--execute",
        action="store_true",
        help="allow the selected bounded Play or Stop action",
    )
    runtime = commands.add_parser("runtime", help="discover existing local runtimes")
    runtime_sub = runtime.add_subparsers(dest="action", required=True)
    node = runtime_sub.add_parser(
        "node", help="locate and verify an existing Node.js executable"
    )
    node.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    node.add_argument(
        "--app",
        type=Path,
        help="Ableton Live.app or Max.app bundle to check after PATH",
    )
    ableton = commands.add_parser(
        "ableton", help="inspect one explicitly selected Ableton Live Set"
    )
    ableton_sub = ableton.add_subparsers(dest="action", required=True)
    set_refs = ableton_sub.add_parser(
        "inspect-set-device-refs",
        help="report saved FileRef paths and local AMXD hashes without changing the Set",
    )
    set_refs.add_argument(
        "path",
        type=Path,
        help="one selected gzip-compressed .als file; no directory scan",
    )
    set_refs.add_argument(
        "--expect-amxd-path",
        type=Path,
        help="require the Set's sole distinct AMXD FileRef to resolve to this exact file",
    )
    set_refs.add_argument(
        "--expect-amxd-sha256",
        help="require the expected and referenced AMXD bytes to match this SHA-256",
    )
    worktree = commands.add_parser(
        "worktree", help="track local ownership and guarded post-merge cleanup"
    )
    worktree_sub = worktree.add_subparsers(dest="action", required=True)
    wt_start = worktree_sub.add_parser(
        "start", help="create and register a named Git worktree"
    )
    wt_start.add_argument("--repository", required=True, type=Path)
    wt_start.add_argument("--worktree", required=True, type=Path)
    wt_start.add_argument("--branch", required=True)
    wt_start.add_argument("--base", required=True)
    wt_register = worktree_sub.add_parser(
        "register", help="register an existing named Git worktree"
    )
    wt_register.add_argument("--repository", required=True, type=Path)
    wt_register.add_argument("--worktree", required=True, type=Path)
    for command in (wt_start, wt_register):
        command.add_argument("--owner-chat", required=True)
        command.add_argument("--story", required=True)
        command.add_argument("--plan", required=True)
    wt_register.add_argument("--pr", default="")
    for action in ("update", "release", "cleanup"):
        command = worktree_sub.add_parser(action)
        command.add_argument("--worktree", required=True, type=Path)
        command.add_argument("--owner-chat", required=True)
        if action == "update":
            command.add_argument("--pr")
            command.add_argument("--plan")
    worktree_sub.add_parser("audit", help="read-only inventory of registered worktrees")
    from .harness_control import register_cli

    register_cli(commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.group == "worktree":
            from . import worktrees

            if args.action == "start":
                result = worktrees.start(
                    args.repository,
                    args.worktree,
                    args.branch,
                    args.base,
                    args.owner_chat,
                    args.story,
                    args.plan,
                )
            elif args.action == "register":
                result = worktrees.register(
                    args.repository,
                    args.worktree,
                    args.owner_chat,
                    args.story,
                    args.plan,
                    args.pr,
                )
            elif args.action == "update":
                result = worktrees.update(
                    args.worktree, args.owner_chat, args.pr, args.plan
                )
            elif args.action == "release":
                result = worktrees.release(args.worktree, args.owner_chat)
            elif args.action == "cleanup":
                result = worktrees.cleanup(args.worktree, args.owner_chat)
            else:
                result = worktrees.audit()
            _json_out(result)
            return 0
        if args.group == "harness":
            from .harness_control import run_cli

            result, code = run_cli(args)
            _json_out(result)
            return code
        if args.group == "runtime" and args.action == "node":
            from .runtime import discover_node

            result = discover_node(args.app)
            if args.json:
                _json_out(result)
            elif result["available"]:
                print(
                    f"Node {result['version']}: {result['path']} ({result['provenance']})"
                )
            else:
                print(result["error"], file=sys.stderr)
            return 0 if result["available"] else 2
        if args.group == "ableton" and args.action == "inspect-set-device-refs":
            from .ableton_set import inspect_set_device_refs

            result = inspect_set_device_refs(
                args.path,
                expected_amxd_path=args.expect_amxd_path,
                expected_amxd_sha256=args.expect_amxd_sha256,
            )
            _json_out(result)
            return 0
        if args.group == "ax" and args.action == "snapshot":
            from .ax_snapshot import snapshot

            _json_out(
                snapshot(
                    args.pid,
                    window_title=args.window_title,
                    max_elements=args.max_elements,
                    max_depth=args.max_depth,
                )
            )
            return 0
        if args.group == "ax" and args.action == "verify-studio-capture":
            from .live_midi_ax import verify_studio_capture_isolation

            result = verify_studio_capture_isolation(
                args.before,
                args.isolated,
                args.restored,
                expected_bundle_id=args.expected_bundle_id,
                max_age_seconds=args.max_age_seconds,
            )
            _json_out(result)
            return 0 if result["valid"] else 1
        if args.group == "ax" and args.action == "live-set-lifecycle":
            from .live_set_lifecycle import run_lifecycle

            result = run_lifecycle(
                args.pid,
                expected_bundle_id=args.expected_bundle_id,
                source_set=args.source_set,
                destination_set=args.destination_set,
                app_bundle=args.app,
                execute=args.execute,
            )
            _json_out(result)
            return 1 if result["status"] == "stopped" else 0
        if args.group == "ax" and args.action == "live-set-open":
            from .live_set_open import run_set_open

            result = run_set_open(
                args.pid,
                expected_bundle_id=args.expected_bundle_id,
                source_set=args.source_set,
                destination_set=args.destination_set,
                source_sha256=args.source_sha256,
                destination_sha256=args.destination_sha256,
                app_bundle=args.app,
                save_owned_source=args.save_owned_source,
                backup_root=args.backup_root,
                execute=args.execute,
            )
            _json_out(result)
            return 1 if result["status"] == "stopped" else 0
        if args.group == "ax" and args.action == "live-set-save-as":
            from .live_set_save_as import run_set_save_as

            result = run_set_save_as(
                args.pid,
                expected_bundle_id=args.expected_bundle_id,
                source_set=args.source_set,
                source_sha256=args.source_sha256,
                destination_directory=args.destination_directory,
                destination_filename=args.destination_filename,
                expected_final_set=args.expected_final_set,
                app_bundle=args.app,
                backup_root=args.backup_root,
                project_required_dialog=args.project_required_dialog,
                execute=args.execute,
            )
            _json_out(result)
            return 1 if result["status"] == "stopped" else 0
        if args.group == "ax" and args.action == "live-midi-sync":
            from .live_midi_sync_control import run_live_midi_sync

            if args.execute and args.out is not None:
                raise ValueError(
                    "--out is for inspect mode; use --receipt with --execute"
                )
            if not args.execute and (
                args.operation is not None
                or args.baseline is not None
                or args.receipt is not None
            ):
                raise ValueError("--action, --baseline and --receipt require --execute")
            result = run_live_midi_sync(
                args.pid,
                expected_bundle_id=args.expected_bundle_id,
                expected_count=args.expected_count,
                action=args.operation,
                baseline_path=args.baseline,
                output_path=args.receipt if args.execute else args.out,
                execute=args.execute,
                max_baseline_age_seconds=args.max_baseline_age_seconds,
            )
            _json_out(result)
            return 1 if result["status"] == "stopped" else 0
        if args.group == "ax" and args.action == "live-midi-inputs":
            from .live_midi_inputs_control import run_live_midi_inputs

            if args.execute and args.out is not None:
                raise ValueError(
                    "--out is for inspect mode; use --receipt with --execute"
                )
            if not args.execute and (
                args.operation is not None
                or args.baseline is not None
                or args.receipt is not None
            ):
                raise ValueError("--action, --baseline and --receipt require --execute")
            result = run_live_midi_inputs(
                args.pid,
                expected_bundle_id=args.expected_bundle_id,
                expected_count=args.expected_count,
                action=args.operation,
                baseline_path=args.baseline,
                output_path=args.receipt if args.execute else args.out,
                execute=args.execute,
                max_baseline_age_seconds=args.max_baseline_age_seconds,
            )
            _json_out(result)
            return 1 if result["status"] == "stopped" else 0
        if args.group == "ax" and args.action == "live-transport":
            from .live_transport_control import run_live_transport

            if not args.execute and args.operation is not None:
                raise ValueError("--action requires --execute")
            result = run_live_transport(
                args.pid,
                expected_bundle_id=args.expected_bundle_id,
                set_path=args.set_path,
                action=args.operation if args.execute else None,
                play_for_seconds=args.play_for_seconds,
                expected_count=args.expected_output_count,
                receipt_path=args.receipt,
                execute=args.execute,
            )
            _json_out(result)
            return 1 if result["status"] == "stopped" else 0
        if args.group == "midi":
            from .midi_monitor import parse_document, write_csv, write_json

            if args.action in ("endpoints", "template"):
                from .midi_capture import endpoints, make_template

                _json_out(
                    endpoints()
                    if args.action == "endpoints"
                    else make_template(args.out, args.source)
                )
                return 0
            if args.action == "report":
                from .midi_assertions import (
                    analyze,
                    check_expectations,
                    verify_capture_receipt,
                )

                document = parse_document(args.path)
                result = {
                    "provenance": {
                        "document": str(args.path),
                        "sha256": document["document_sha256"],
                        "parser_sha256": sha256(
                            Path(__file__).with_name("midi_monitor.py").read_bytes()
                        ),
                        "midi_monitor_document_version": document["version"],
                        "event_count": len(document["events"]),
                    },
                    "analysis": analyze(document["events"]),
                }
                if args.receipt:
                    result["provenance"]["receipt"] = verify_capture_receipt(
                        args.path, document, args.receipt
                    )
                if args.expect:
                    result["assertions"] = check_expectations(
                        document["events"], json.loads(args.expect.read_text())
                    )
                if (
                    result["provenance"].get("receipt", {}).get("status")
                    == "inconclusive"
                ):
                    result["provenance"]["capture_gate"] = (
                        "inconclusive: event buffer reached its configured limit"
                    )
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
                print(
                    f"{report['format']} {args.path}: {report['bytes']} bytes, sha256 {report['sha256']}"
                )
                print(f"archive entries: {report['entry_count']}")
                if report["graph"] is not None:
                    counts = report["graph"]["counts"]
                    print(
                        f"patchers: {counts['patchers']}, boxes: {counts['boxes']}, lines: {counts['lines']}"
                    )
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
            result = {
                "path": str(args.path),
                "format": fmt,
                "entry_count": len(entries),
                **graph,
            }
            if args.native_inline_inputs:
                from .max_inline_inputs import inline_input_report

                preflight = inline_input_report(patch)
                result["native_inline_inputs"] = preflight
                result["errors"] = [*graph["errors"], *preflight["errors"]]
            if args.json:
                _json_out(result)
            else:
                counts = graph["counts"]
                print(
                    f"{fmt}: {counts['patchers']} patchers, {counts['boxes']} boxes, {counts['lines']} lines"
                )
                if args.native_inline_inputs:
                    print(
                        "native inline inputs: "
                        f"{len(preflight['checked'])} checked cords, "
                        f"{len(preflight['excluded'])} excluded cords"
                    )
                for error in result["errors"]:
                    print(f"error: {error}")
                if not result["errors"]:
                    print("OK")
            return 1 if result["errors"] else 0
        if args.group == "max" and args.action == "package-m4l-candidate":
            from .m4l_package import package_m4l_candidate

            manifest = package_m4l_candidate(
                args.source_root,
                args.source,
                args.template,
                args.dependency_allowlist,
                args.out,
            )
            _json_out(
                {"out": str(args.out.expanduser().absolute()), "manifest": manifest}
            )
            return 0
        if args.group == "max" and args.action == "preview":
            from .max_preview import preview as max_preview

            result = max_preview(args.path, args.out)
            _json_out(result)
            return 0
        if args.group == "hash" and args.action == "manifest":
            result = _manifest(args.path, args.out)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"wrote {len(result['files'])} file hashes to {args.out}")
            return 0
        if args.group == "deploy" and args.action == "plan":
            output = args.out.resolve(strict=False)
            if output == args.manifest.resolve(strict=False):
                raise ValueError(
                    "plan output must not overwrite the deployment manifest"
                )
            result = make_plan(args.manifest, args.source_root, args.destination_root)
            for root_arg in (
                args.source_root.resolve(),
                args.destination_root.resolve(strict=False),
            ):
                try:
                    output.relative_to(root_arg)
                except ValueError:
                    continue
                raise ValueError(
                    "plan output must be outside source and destination roots"
                )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"planned {len(result['files'])} files: {args.out}")
            return 0
        if args.group == "deploy" and args.action == "apply":
            receipt = apply_plan(args.plan, args.backup_root)
            print(f"applied {args.plan}; receipt: {receipt}")
            return 0
        if args.group == "deploy" and args.action == "verify":
            result = verify_plan(args.plan)
            _json_out(result)
            return 0 if result["passed"] else 1
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
