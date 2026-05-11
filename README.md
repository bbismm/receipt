# Receipt

**A minimal standard for verifiable actions.**
**If an agent claims it acted, it should produce a receipt.**

The first deployed use case: AI trading bots and copy-traders who want
to be believed without asking anyone to take their word for it.

> **Heads-up for non-trading adopters:** spec v0.1's vocabulary is
> trade-flavored (`trust_tier` enum, `match.symbol/side/size` requirements,
> `reconciliation.matched/unmatched/errors` counts). The four-phase
> mechanic — `claim → external_action → verified → reconciliation` —
> generalizes cleanly to any AI task (writing code, fetching URLs, running
> research loops), but a code-writing or research adopter has to re-map
> trade fields. See [`examples/code_writing_agent.md`](examples/code_writing_agent.md)
> for a worked example and the three concrete spec issues it surfaced —
> these become the shopping list for spec v0.2.

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

- **Spec v0.1.1**: draft — see [SPEC.md](./SPEC.md). Canonicalization rules
  frozen pre-v1.0; cross-implementation parity tests in [`tests/`](./tests/).
  Breaking changes possible before v1.0.
- **Reference adopter (wiring in progress)**: iBitLabs's `$1k → $10k` SOL
  perpetuals experiment. Backfill receipts for live trade history work today
  via [`examples/backfill_ibitlabs.py`](./examples/backfill_ibitlabs.py).
  Realtime emission from the live bot is queued — see
  [`integration/sniper_main_patch.py`](./integration/sniper_main_patch.py).
- **Reconciliation adapters**: Coinbase Advanced Trade (in
  [`receipt/adapters/`](./receipt/adapters/)); Binance Futures + Hyperliquid
  (planned).
- **Anchor**: IPFS daily Merkle anchor cron at
  [`scripts/anchor_daily.py`](./scripts/anchor_daily.py).
- **Verifier**: HTTP service at
  [`scripts/verifier_service.py`](./scripts/verifier_service.py); browser
  viewer at [`viewer/`](./viewer/).

## Origin

Built by [iBitLabs](https://ibitlabs.com) while running our own public AI
trading experiment. We had to solve "how do we prove our bot is real?"
before we could ask anyone else to take the public-AI-trading space
seriously. So we open-sourced the answer.

We don't think this should be our moat. We think it should be the floor.

## License

- Reference implementation (this repo's code): MIT
- Spec ([SPEC.md](./SPEC.md)): CC-BY-4.0
