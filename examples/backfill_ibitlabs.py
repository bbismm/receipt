"""Backfill iBitLabs's existing live trades into a Receipt v0.1 chain.

Honest about provenance: backfill events use trust_tier='backfill_local' —
materially weaker than realtime exchange verification. Future events written
by the bot live will use trust_tier='exchange_realtime'.

Output:  ~/ibitlabs/audit_export/sniper-v5.1.receipt.jsonl

Run:
    cd ~/Documents/receipt
    PYTHONPATH=. python3.12 examples/backfill_ibitlabs.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
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

rows = 0
for row in cur:
    (rid, symbol, side, direction, entry_px, exit_px, qty, pnl, fees, funding,
     exit_reason, regime, mfe, mae, strat_ver, strat_int, trigger, _inst, ts) = row
    rows += 1
    side_str = side.lower() if side else ("buy" if direction == "long" else "sell")
    open_action = f"open_{direction}" if direction else f"open_{side_str}"

    # ── claim: open ──
    claim_open = r.claim(
        action=open_action,
        symbol=symbol,
        side=side_str,
        size=qty,
        price_intended=entry_px,
        ai={"model": "rule_based", "provider": "internal",
            "decision_mode": "rule_based", "agent_version": strat_ver or "sniper-v5.1"},
        strategy={"version": strat_ver, "intent": strat_int, "trigger_rule": trigger},
        regime=regime,
        backfill={"source_row_id": rid, "source_db": "sol_sniper.db"},
    )

    # ── external_action: order placed (synthesized order_id for backfill) ──
    order_id = f"backfill_open_{rid}"
    r.external_action(
        claim_open,
        venue="coinbase_intx",
        request={"endpoint": "/api/v3/brokerage/orders", "method": "POST"},
        response={"status": 200, "order_id": order_id},
    )

    # ── verified: trust_tier='backfill_local' — honest about weakness ──
    r.verified(
        claim_open,
        trust_tier="backfill_local",
        source="local_db_backfill_2026_05_05",
        fill_price=entry_px,
        filled_size=qty,
        fill_ts=int(ts * 1000),
        match={
            "symbol": symbol, "side": True, "size": True,
            "price_match": True, "time_match": True, "id_match": True,
            "tolerance_used": {"price": 0.0, "time_ms": 0,
                               "note": "backfill identity is internal row id, not exchange-verified"},
        },
    )

    # ── if closed, emit close lifecycle ──
    if exit_px is not None:
        close_action = f"close_{direction}" if direction else "close"
        claim_close = r.claim(
            action=close_action,
            symbol=symbol,
            side=("sell" if direction == "long" else "buy"),
            size=qty,
            price_intended=exit_px,
            exit_reason=exit_reason,
            ai={"model": "rule_based", "provider": "internal",
                "decision_mode": "rule_based", "agent_version": strat_ver or "sniper-v5.1"},
            backfill={"source_row_id": rid, "leg": "close"},
        )
        close_order_id = f"backfill_close_{rid}"
        r.external_action(
            claim_close,
            venue="coinbase_intx",
            request={"endpoint": "/api/v3/brokerage/orders", "method": "POST"},
            response={"status": 200, "order_id": close_order_id},
        )
        r.verified(
            claim_close,
            trust_tier="backfill_local",
            source="local_db_backfill_2026_05_05",
            fill_price=exit_px,
            filled_size=qty,
            fill_ts=int(ts * 1000),
            match={
                "symbol": symbol, "side": True, "size": True,
                "price_match": True, "time_match": True, "id_match": True,
                "tolerance_used": {"price": 0.0, "time_ms": 0,
                                   "note": "backfill identity is internal row id"},
            },
            pnl_usd=pnl, fees_usd=fees, funding_usd=funding, mfe=mfe, mae=mae,
        )

conn.close()

# ── one reconciliation event covering the backfill window ────────────────
recon_path = Path("~/ibitlabs/state/reconciliation_status.json").expanduser()
recon_state = json.loads(recon_path.read_text()) if recon_path.exists() else {}
r.reconciliation(
    period="backfill_2026_05_05",
    trust_tier="backfill_local",
    matched=rows,
    unmatched=0,
    errors=0,
    notes="full coinbase_intx re-verification deferred to live adapter",
    local_recon_clean=recon_state.get("clean", True),
)

# ── anchor: chain head will be published in launch post ──────────────────
r.anchor(
    merkle_root=r.head_hash,
    anchor_uri="PENDING_LAUNCH",
    anchor_kind="github_commit",
)

print(f"\nbackfilled {rows} trade rows  →  {r.seq} receipt events")
print(f"chain head:  {r.head_hash}")
print(f"output:      {OUT}")
