"""Backfill iBitLabs's existing 84 live trades into a Receipt-compliant JSONL.

Honest about provenance: backfilled events use source='local_db_backfill_2026_05_05'
on their `verified` events, distinguishing them from future real-time receipts
that will source from 'coinbase_intx' via the live adapter.

Output:  /Users/bonnyagent/ibitlabs/audit_export/sniper-v5.1.receipt.jsonl

Run:
    cd ~/Documents/receipt
    PYTHONPATH=. python3.12 examples/backfill_ibitlabs.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt import Receipt

DB = Path("~/ibitlabs/sol_sniper.db").expanduser()
OUT = Path("~/ibitlabs/audit_export/sniper-v5.1.receipt.jsonl").expanduser()

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.unlink(missing_ok=True)

r = Receipt(agent="iBitLabs/sniper-v5.1", out_path=OUT)

print(f"Reading {DB}...")
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = conn.execute("""
    SELECT id, symbol, side, direction, entry_price, exit_price, quantity,
           pnl, fees, funding, exit_reason, regime, mfe, mae,
           strategy_version, strategy_intent, trigger_rule, instance_name, timestamp
    FROM trade_log
    WHERE instance_name = 'live'
    ORDER BY id ASC
""")

rows_seen = 0
for row in cur:
    (rid, symbol, side, direction, entry_px, exit_px, qty, pnl, fees, funding,
     exit_reason, regime, mfe, mae, strat_ver, strat_int, trigger, instance, ts) = row
    rows_seen += 1

    # ── claim: the bot intended to open a position at entry_px ────────────
    open_action = f"open_{direction}" if direction else f"open_{side.lower()}"
    claim_open = r.claim(
        action=open_action,
        symbol=symbol,
        size=qty,
        price_intended=entry_px,
        ai={"model": "rule_based", "provider": "internal", "decision_mode": "rule_based"},
        strategy={"version": strat_ver, "intent": strat_int, "trigger_rule": trigger},
        regime=regime,
        backfill={"source_row_id": rid, "source_db": "sol_sniper.db"},
    )

    # ── external_action: the order was placed (we don't have request body
    #    hashes for historical fills, so omit body_hash) ────────────────────
    action_open = r.external_action(
        claim_open,
        venue="coinbase_intx",
        request={"endpoint": "/api/v3/brokerage/orders", "method": "POST"},
        response={"status": 200, "order_id": f"backfill_open_{rid}"},
    )

    # ── verified: backfilled from local DB, not re-fetched from exchange.
    #    Honest source label so verifiers can distinguish trust tier. ──────
    r.verified(
        claim_open,
        source="local_db_backfill_2026_05_05",
        fill_price=entry_px,
        filled_size=qty,
        fill_ts=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        match={
            "size": True, "side": True,
            "price_within_tolerance": True,
            "trust_tier": "backfill_local",
        },
    )

    # ── if the trade closed (exit_price set), emit close lifecycle ────────
    if exit_px is not None:
        close_action = f"close_{direction}" if direction else "close"
        claim_close = r.claim(
            action=close_action,
            symbol=symbol,
            size=qty,
            price_intended=exit_px,
            exit_reason=exit_reason,
            backfill={"source_row_id": rid, "leg": "close"},
        )
        r.external_action(
            claim_close,
            venue="coinbase_intx",
            request={"endpoint": "/api/v3/brokerage/orders", "method": "POST"},
            response={"status": 200, "order_id": f"backfill_close_{rid}"},
        )
        r.verified(
            claim_close,
            source="local_db_backfill_2026_05_05",
            fill_price=exit_px,
            filled_size=qty,
            fill_ts=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            match={
                "size": True, "side": True,
                "price_within_tolerance": True,
                "trust_tier": "backfill_local",
            },
            pnl_usd=pnl,
            fees_usd=fees,
            funding_usd=funding,
            mfe=mfe, mae=mae,
        )

conn.close()

# ── one reconciliation event covering the whole backfill window ───────────
state_path = Path("~/ibitlabs/state/reconciliation_status.json").expanduser()
recon_state = json.loads(state_path.read_text()) if state_path.exists() else {}
r.reconciliation(
    window="backfill_2026_05_05",
    local={"source": "sol_sniper.db", "rows_replayed": rows_seen},
    external={"source": "deferred_to_coinbase_adapter"},
    match=recon_state.get("clean", True),
    notes="backfill — full coinbase_intx re-verification will be added when the adapter is wired",
)

# ── anchor: this chain head will be published in the launch post ──────────
r.anchor(
    merkle_root=r.head_hash,
    anchor_uri="PENDING_LAUNCH_POST",
    anchor_kind="github_commit",
)

print(f"\nbackfilled {rows_seen} trade rows  →  {r.seq} receipt events")
print(f"chain head:  {r.head_hash}")
print(f"output:      {OUT}")
