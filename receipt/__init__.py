"""Receipt — open standard for publicly-verifiable AI agent activity logs.

Spec: https://github.com/bbismm/receipt/blob/main/SPEC.md

Quick start:

    from receipt import Receipt

    r = Receipt(agent="my-bot/v1", out_path="my_bot.receipt.jsonl")
    r.claim(action="open_short", symbol="SOL", size=0.1, price_intended=108)
    r.external_action(claim_seq=0, venue="coinbase_intx",
                      request={"endpoint": "/api/v3/brokerage/orders", "method": "POST"},
                      response={"status": 200, "order_id": "abc"})
    r.verified(claim_seq=0, action_seq=1, source="coinbase_intx",
               fill_price=108.01, filled_size=0.1,
               match={"size": True, "side": True})
    r.reconciliation(local={"balance_usd": 1000}, external={"balance_usd": 1000}, match=True)
"""
from receipt.core import Receipt
from receipt.chain import compute_hash, verify_chain, ChainStatus
from receipt.score import compute_score

__version__ = "0.1.1"
__all__ = ["Receipt", "compute_hash", "verify_chain", "ChainStatus", "compute_score"]
