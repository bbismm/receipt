#!/usr/bin/env python3
"""reconcile_now.py — emit a real-tier reconciliation event into a Receipt chain.

Pulls live truth from Coinbase via the receipt.adapters.coinbase_intx adapter,
compares against the bot's local state files, and appends one
`reconciliation` event with `trust_tier=exchange_realtime`.

Designed to be run from launchd cron every 6h (SPEC §5 hard cadence).

Usage (one-shot):
    PYTHONPATH=~/Documents/receipt python3 scripts/reconcile_now.py

Output chain:
    ~/ibitlabs/audit_export/sniper-v5.1.realtime.receipt.jsonl

This is a SEPARATE chain from the backfill (sniper-v5.1.receipt.jsonl).
The realtime chain accumulates from genesis as the bot runs; phase D of
P0.1 will fold the bot's own claim/external_action/verified events into
this same file.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys
from datetime import datetime, timezone
from pathlib import Path

# Receipt lib path
sys.path.insert(0, os.path.expanduser("~/Documents/receipt"))

from receipt import Receipt
from receipt.adapters.coinbase_intx import CoinbaseAdapter

PLIST = Path("~/Library/LaunchAgents/com.ibitlabs.sniper.plist").expanduser()
STATE_FILE = Path("~/ibitlabs/sol_sniper_state.json").expanduser()
RECON_STATE = Path("~/ibitlabs/state/reconciliation_status.json").expanduser()
CHAIN = Path("~/ibitlabs/audit_export/sniper-v5.1.realtime.receipt.jsonl").expanduser()


def _load_credentials() -> tuple[str, str]:
    """Pull CB_API_KEY/CB_API_SECRET from the live bot's launchd plist."""
    if "CB_API_KEY" in os.environ and "CB_API_SECRET" in os.environ:
        return os.environ["CB_API_KEY"], os.environ["CB_API_SECRET"]
    with PLIST.open("rb") as f:
        env = plistlib.load(f).get("EnvironmentVariables", {})
    api_key = env.get("CB_API_KEY") or env.get("COINBASE_API_KEY")
    api_secret = env.get("CB_API_SECRET") or env.get("COINBASE_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("CB credentials not found in env or plist")
    return api_key, api_secret


def _load_local_position() -> dict:
    """Read sol_sniper_state.json. The 'position' dict has no `active` key —
    presence + non-zero entry_price indicates an open position. Empty dict or
    missing entry_price = flat."""
    if not STATE_FILE.exists():
        return {"side": "flat", "size": 0, "symbol": None}
    state = json.loads(STATE_FILE.read_text())
    pos = state.get("position") or {}
    entry = pos.get("entry_price") or 0
    if not entry or not pos.get("symbol"):
        return {"side": "flat", "size": 0, "symbol": None}
    return {
        "side": pos.get("direction", "unknown"),
        "size": float(pos.get("size") or pos.get("quantity") or pos.get("contracts") or 0),
        "symbol": pos.get("symbol"),
        "entry_price": float(entry),
        "order_id": pos.get("order_id"),  # real Coinbase order_id, available for future verified events
    }


def _load_local_balance() -> dict:
    """Best-effort read from latest reconciliation_status.json."""
    if not RECON_STATE.exists():
        return {}
    s = json.loads(RECON_STATE.read_text())
    return {
        "clean": s.get("clean"),
        "last_run_iso": s.get("last_run_iso"),
        "db_rows": s.get("db_rows"),
        "exchange_fills": s.get("exchange_fills"),
        "unmatched": s.get("unmatched_post_cleanup", 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="SLP-20DEC30-CDE",
                    help="Coinbase INTX perp symbol")
    ap.add_argument("--out", default=str(CHAIN))
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch + compare, do NOT append to chain")
    args = ap.parse_args()

    print(f"reconcile_now @ {datetime.now(tz=timezone.utc).isoformat()}")
    api_key, api_secret = _load_credentials()
    adapter = CoinbaseAdapter(api_key=api_key, api_secret=api_secret)

    # external truth
    print("  fetching exchange position...")
    try:
        ext_pos = adapter.fetch_position(args.symbol)
    except Exception as e:
        print(f"  ✗ fetch_position failed: {e}", file=sys.stderr)
        ext_pos = {"side": "unknown", "size": 0, "error": str(e)}

    print("  fetching exchange balance...")
    try:
        ext_bal = adapter.fetch_balance()
    except Exception as e:
        print(f"  ✗ fetch_balance failed: {e}", file=sys.stderr)
        ext_bal = {"balance_usd": 0, "error": str(e)}

    # local state
    local_pos = _load_local_position()
    local_bal_meta = _load_local_balance()

    # compare
    pos_match = (
        local_pos.get("side") == ext_pos.get("side")
        and abs((local_pos.get("size") or 0) - (ext_pos.get("size") or 0)) < 0.001
    )

    matched = 1 if pos_match else 0
    unmatched = 0 if pos_match else 1
    errors = 1 if (ext_pos.get("error") or ext_bal.get("error")) else 0

    print(f"  local position:    {local_pos}")
    print(f"  exchange position: {ext_pos}")
    print(f"  exchange balance:  {ext_bal}")
    print(f"  match: {pos_match}")

    if args.dry_run:
        print("  --dry-run: not writing to chain")
        return 0 if pos_match and not errors else 1

    # emit reconciliation event
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    r = Receipt(agent="iBitLabs/sniper-v5.1", out_path=out)
    seq = r.reconciliation(
        period=f"reconcile_{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%MZ')}",
        trust_tier="exchange_realtime",
        matched=matched, unmatched=unmatched, errors=errors,
        local={"position": local_pos, "recon_meta": local_bal_meta},
        external={"position": ext_pos, "balance": ext_bal},
        match=pos_match,
        venue="coinbase_intx",
    )
    print(f"  appended reconciliation event seq={seq} → {out}")
    print(f"  chain head: {r.head_hash[:32]}...")
    return 0 if pos_match and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
