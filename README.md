# Receipt

**An open standard for publicly-verifiable AI agent activity logs.**

If your AI bot tells the public it made a trade, Receipt is the artifact that
lets anyone confirm the trade actually happened — without trusting you.

```bash
pip install receipt-trade
```

```python
from receipt import Receipt

r = Receipt(agent="my-bot/v1", out_path="my_bot.receipt.jsonl")

claim_seq = r.claim(action="open_short", symbol="SOL-USD-PERPETUAL", size=0.1)
r.external_action(claim_seq, venue="coinbase_intx",
                  request={"endpoint": "/api/v3/brokerage/orders", "method": "POST"},
                  response={"status": 200, "order_id": "abc"})
r.verified(claim_seq, source="coinbase_intx", fill_price=108.01,
           match={"size": True, "side": True, "price_within_tolerance": True})
```

Anyone can verify the chain:

```bash
receipt verify my_bot.receipt.jsonl
# ✅ VERIFIED   my_bot.receipt.jsonl
#   events:    4
#   claims:    1
#   verified:  1
```

## Why

Public AI trading bots, copy-trade services, and "AI fund" claims today rely
on screenshots and trust. Receipt makes the activity log:

- **Append-only and hash-chained** — silent edits break the chain
- **Externally reconciled** — every claim is paired with exchange-API truth
- **Publicly anchored** — daily Merkle roots posted to Twitter / Moltbook /
  GitHub create third-party timestamps no one can rewrite
- **Open standard** — JSONL spec, MIT reference impl, no lock-in

If a public-trading project refuses to publish a Receipt-compliant log,
that's the signal.

## Status

- **Spec v0.1**: draft, see [SPEC.md](./SPEC.md)
- **Reference adopter**: iBitLabs's `$1k → $10k` SOL perpetuals experiment
  publishes Receipt-compliant data at `receipt.ibitlabs.com/sniper-v5.1`
  (live since 2026-MM-DD).
- **Reconciliation adapters**: Coinbase Advanced Trade (in progress),
  Binance Futures, Hyperliquid (planned)

## Origin

Built by [iBitLabs](https://ibitlabs.com) while running our own public AI
trading experiment. We had to solve "how do we prove our bot is real?"
before we could ask anyone else to take the public-AI-trading space
seriously. So we open-sourced the answer.

We don't think this should be our moat. We think it should be the floor.

## License

- Reference implementation (this repo's code): MIT
- Spec ([SPEC.md](./SPEC.md)): CC-BY-4.0
