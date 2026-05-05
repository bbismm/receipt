"""receipt CLI — verify and inspect receipt JSONL files.

Usage:
    receipt verify <path.jsonl>      # static chain integrity check
    receipt show <path.jsonl>        # human-readable summary
    receipt --version
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from receipt import __version__
from receipt.chain import ChainStatus, verify_chain


def cmd_verify(path: str) -> int:
    p = Path(path).expanduser()
    if not p.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    events = []
    with p.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"error: line {i} not valid JSON: {e}", file=sys.stderr)
                return 2

    report = verify_chain(events)
    badge = "✅ VERIFIED" if report.status == ChainStatus.VERIFIED else f"❌ {report.status.value}"

    print(f"{badge}  {p}")
    print(f"  events:    {report.n_events}")
    print(f"  claims:    {report.n_claims}")
    print(f"  verified:  {report.n_verified}")
    if report.issues:
        print("  issues:")
        for issue in report.issues:
            print(f"    - {issue}")
    return 0 if report.status == ChainStatus.VERIFIED else 1


def cmd_show(path: str) -> int:
    p = Path(path).expanduser()
    if not p.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    counts: dict[str, int] = {}
    agents: set[str] = set()
    first_ts = last_ts = None
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            counts[ev["kind"]] = counts.get(ev["kind"], 0) + 1
            agents.add(ev["agent"])
            first_ts = first_ts or ev["ts"]
            last_ts = ev["ts"]
    print(f"path:    {p}")
    print(f"agents:  {sorted(agents)}")
    print(f"window:  {first_ts}  →  {last_ts}")
    print(f"events by kind:")
    for k in sorted(counts):
        print(f"  {k:<20} {counts[k]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--version":
        print(f"receipt {__version__}")
        return 0
    if argv[0] == "verify" and len(argv) == 2:
        return cmd_verify(argv[1])
    if argv[0] == "show" and len(argv) == 2:
        return cmd_show(argv[1])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
