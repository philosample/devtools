"""Read-only verification of saved Live MIDI output Sync AX observations."""

import argparse
import hashlib
import json
import re
from pathlib import Path

ROW = re.compile(
    r"^[ \t]*[+~]?[ \t]*\d+ checkbox Description: (.{1,256}): "
    r"MIDI Output: Sync, Value: ([01]), Help: MIDI Output: Sync$"
)


def parse_sync_rows(text: str, expected_count: int) -> dict[str, int]:
    """Require an explicit caller inventory count; never infer completeness."""
    if type(expected_count) is not int or expected_count < 1:
        raise ValueError("expected output count must be positive")
    rows = {}
    for line in text.splitlines():
        if "MIDI Output: Sync" not in line or (
            "Description:" not in line and not re.match(r"^[ \t]*[+~]?[ \t]*\d+ ", line)
        ):
            continue
        match = ROW.fullmatch(line)
        if not match:
            raise ValueError("unsupported output Sync AX row")
        name, value = match.groups()
        if name in rows:
            raise ValueError("duplicate output name; identity is ambiguous")
        rows[name] = int(value)
    if len(rows) != expected_count:
        raise ValueError("output count differs from the caller's explicit inventory")
    return rows


def compare_sync_observations(
    before: str, isolated: str, restored: str, expected_count: int
) -> dict:
    snapshots = [before, isolated, restored]
    original, disabled, final = [
        parse_sync_rows(text, expected_count) for text in snapshots
    ]
    if original.keys() != disabled.keys() or original.keys() != final.keys():
        raise ValueError("output identities changed across observations")
    all_off = not any(disabled.values())
    exact_restore = final == original
    return {
        "format": "devtools-live-sync-ax-evidence/v1",
        "valid": all_off and exact_restore,
        "evidence_level": "saved AX Sync values only",
        "limit": (
            "Does not discover inventory, inspect current Live, change settings, "
            "authorize playback or prove MIDI silence/delivery. Caller must verify "
            "the complete applicable output inventory and actual transport state."
        ),
        "expected_output_count": expected_count,
        "originally_enabled": sorted(k for k, v in original.items() if v),
        "isolated_all_off": all_off,
        "restored_exactly": exact_restore,
        "snapshot_sha256": [
            hashlib.sha256(text.encode("utf-8")).hexdigest() for text in snapshots
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("isolated", type=Path)
    parser.add_argument("restored", type=Path)
    parser.add_argument("--expected-output-count", type=int, required=True)
    args = parser.parse_args()
    try:
        report = compare_sync_observations(
            args.before.read_bytes().decode("utf-8"),
            args.isolated.read_bytes().decode("utf-8"),
            args.restored.read_bytes().decode("utf-8"),
            args.expected_output_count,
        )
    except (ValueError, OSError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
