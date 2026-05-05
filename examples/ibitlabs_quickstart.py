"""End-to-end Receipt lifecycle, simulated with mock external truth.

Demonstrates how iBitLabs's sniper bot intends to integrate Receipt v0.1.
Run this to produce a sample chain you can verify with `receipt verify`.

    python3 examples/ibitlabs_quickstart.py

Then:

    receipt verify /tmp/ibitlabs_demo.receipt.jsonl
    receipt show   /tmp/ibitlabs_demo.receipt.jsonl
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt import Receipt

OUT = "/tmp/ibitlabs_demo.receipt.jsonl"

# Wipe for a clean demo run
Path(OUT).unlink(missing_ok=True)

r = Receipt(agent="iBitLabs/sniper-v5.1", out_path=OUT)

# ── Trade 1: clean lifecycle ──────────────────────────────────────────────

claim_seq = r.claim(
    action="open_short",
    symbol="SOL-USD-PERPETUAL",
    size=0.092,
    price_intended=108.42,
    stop_loss=113.84,
    take_profit=105.40,
    reasoning=["StochRSI K=18.3 oversold-side", "near lower BB", "regime=down"],
    ai={"model": "rule_based", "provider": "internal", "decision_mode": "rule_based"},
)

r.external_action(
    claim_seq,
    venue="coinbase_intx",
    request={
        "endpoint": "/api/v3/brokerage/orders",
        "method": "POST",
        "body_hash": "sha256:f3a1b2c3d4e5...",  # truncated
    },
    response={"status": 200, "order_id": "1a2b3c4d-aaaa-bbbb-cccc-dddddddddddd"},
)

r.verified(
    claim_seq,
    source="coinbase_intx",
    source_endpoint="/api/v3/brokerage/orders/historical/1a2b3c4d-...",
    fill_price=108.41,
    filled_size=0.092,
    fill_ts="2026-05-05T14:32:01.847Z",
    match={
        "size": True,
        "side": True,
        "price_within_tolerance": True,  # |108.41 - 108.42| < 0.01
        "timing_within_tolerance": True,
    },
)

# ── Periodic reconciliation (≥ once per 24h required by spec) ─────────────

r.reconciliation(
    window="since_last",
    local={
        "balance_usd": 974.21,
        "positions": [{"symbol": "SOL-USD-PERPETUAL", "side": "short", "size": 0.092}],
    },
    external={
        "balance_usd": 974.21,
        "positions": [{"symbol": "SOL-USD-PERPETUAL", "side": "short", "size": 0.092}],
    },
    match=True,
    drift={"balance_usd": 0.00},
)

# ── Daily public anchor (Twitter / Moltbook / GitHub commit) ──────────────

r.anchor(
    merkle_root=r.head_hash,  # for v0.1 demo, single-stream chain head doubles as root
    anchor_uri="https://twitter.com/bonnybb/status/PLACEHOLDER",
    anchor_kind="twitter",
)

print(f"wrote {r.seq} events to {OUT}")
print(f"chain head: {r.head_hash}")
print()
print("verify with:")
print(f"  receipt verify {OUT}")
