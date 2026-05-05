"""End-to-end Receipt v0.1 lifecycle, simulated with mock external truth.

Demonstrates how iBitLabs's sniper bot intends to integrate Receipt.
Run this to produce a sample chain you can verify with `receipt verify`.

    PYTHONPATH=. python3.12 examples/ibitlabs_quickstart.py
    PYTHONPATH=. python3.12 -m receipt.cli verify /tmp/ibitlabs_demo.receipt.jsonl
    PYTHONPATH=. python3.12 -m receipt.cli show /tmp/ibitlabs_demo.receipt.jsonl
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt import Receipt

OUT = "/tmp/ibitlabs_demo.receipt.jsonl"
Path(OUT).unlink(missing_ok=True)

r = Receipt(agent="iBitLabs/sniper-v5.1", out_path=OUT)

# heartbeat — system alive, scanning ──────────────────────────────────────
r.heartbeat(status="alive", latency_ms=120)

# trade 1 — clean lifecycle ───────────────────────────────────────────────
claim_seq = r.claim(
    action="open_short",
    symbol="SOL-USD-PERPETUAL",
    side="sell",
    size=0.092,
    price_intended=108.42,
    stop_loss=113.84,
    take_profit=105.40,
    ai={
        "model": "rule_based",
        "provider": "internal",
        "decision_mode": "rule_based",
        "agent_version": "sniper-v5.1",
    },
    reasoning=["StochRSI K=18.3 oversold-side", "near lower BB", "regime=down"],
)

r.external_action(
    claim_seq,
    venue="coinbase_intx",
    request={"endpoint": "/api/v3/brokerage/orders", "method": "POST",
             "body_hash": "sha256:f3a1b2c3d4e5"},
    response={"status": 200, "order_id": "1a2b3c4d-aaaa-bbbb-cccc-dddddddddddd"},
)

r.verified(
    claim_seq,
    trust_tier="exchange_realtime",
    source="coinbase_intx",
    source_endpoint="/api/v3/brokerage/orders/historical/1a2b3c4d-...",
    fill_price=108.41,
    filled_size=0.092,
    fill_ts=1714912321847,
    match={
        "symbol": "SOL-USD-PERPETUAL",
        "side": True, "size": True,
        "price_match": True, "time_match": True, "id_match": True,
        "tolerance_used": {"price": 0.002, "time_ms": 5000},
    },
)

# trade 2 — rejected by regime gate before reaching exchange ──────────────
r.signal_rejected(
    would_be_action="open_long",
    rejected_by="regime_gate",
    reason="30d regime is down, long counter-trend",
    ai={"model": "rule_based", "provider": "internal",
        "decision_mode": "rule_based", "agent_version": "sniper-v5.1"},
)

# trade 3 — failure path ──────────────────────────────────────────────────
claim_seq2 = r.claim(
    action="open_short",
    symbol="SOL-USD-PERPETUAL",
    side="sell", size=0.092,
    price_intended=108.50,
    ai={"model": "rule_based", "provider": "internal",
        "decision_mode": "rule_based", "agent_version": "sniper-v5.1"},
)
r.error(
    claim_seq=claim_seq2,
    phase="external_action",
    error_type="exchange_5xx",
    message="Coinbase returned 502 after 3 retries",
    retryable=True,
    context={"endpoint": "/api/v3/brokerage/orders", "request_id": "7a3b9c"},
)

# reconciliation — every 6h hard cadence ──────────────────────────────────
r.reconciliation(
    period="2026-05-05T18:00Z/2026-05-06T00:00Z",
    trust_tier="exchange_realtime",
    matched=1,
    unmatched=0,
    errors=1,
)

# anchor — daily public timestamp on permanent layer ──────────────────────
r.anchor(
    merkle_root=r.head_hash,
    anchor_uri="ipfs://Qm_PLACEHOLDER_pin_via_pinata_or_w3s",
    anchor_kind="ipfs",
)

print(f"wrote {r.seq} events to {OUT}")
print(f"chain head: {r.head_hash}")
print()
print("verify with:")
print(f"  PYTHONPATH=. python3.12 -m receipt.cli verify {OUT}")
