# Receipt Spec v0.1

**Status**: draft, 2026-05-05
**Maintainer**: iBitLabs
**License**: CC-BY-4.0 (this spec) | MIT (reference implementation)

Receipt is an open standard for **publicly-verifiable AI agent activity logs**,
designed first for AI trading bots and copy-trading services where users need
to verify that the bot did what it claimed.

> If your AI bot tells the public it made a trade, Receipt is the artifact
> that lets anyone confirm the trade actually happened — without trusting you.

---

## 1. Design principles

1. **Append-only, hash-chained**. Once written, an event cannot be edited
   without breaking the chain. Daily Merkle roots get publicly anchored
   (Twitter / Moltbook / GitHub commit) for third-party timestamping.
2. **External truth is the arbiter, not the agent's self-report**. Every
   `claim` event must eventually be paired with a `verified` event sourced
   from an external API (exchange, blockchain, GitHub, etc.).
3. **Schema is JSONL — one event per line, UTF-8, append-only**. Trivial to
   tail, grep, ship, mirror.
4. **Read-only by default**. Compliant tooling never mutates exchange state.
   The spec describes verification, not execution.
5. **Adversarial-robust**. Assumes the bot operator may try to fake or omit
   events. The chain + external reconciliation makes silent omission
   detectable (gap in sequence, balance drift, missing fills).

## 2. Event types (v0.1)

Every event has a fixed envelope:

```json
{
  "v": "0.1",
  "ts": "2026-05-05T14:32:00.123Z",
  "seq": 4231,
  "agent": "iBitLabs/sniper-v5.1",
  "kind": "<one of: claim | external_action | verified | reconciliation | anchor>",
  "data": { ... },
  "prev_hash": "sha256:abc...",
  "hash": "sha256:def..."
}
```

`hash` = SHA-256 of canonical JSON of `{v, ts, seq, agent, kind, data, prev_hash}`,
in that key order, no whitespace. `prev_hash` of event 0 is `sha256:` + 64 zeros.

### 2.1 `claim` — what the agent says it's about to do

```json
{
  "kind": "claim",
  "data": {
    "action": "open_short",
    "symbol": "SOL-USD-PERPETUAL",
    "size": 0.092,
    "price_intended": 108.42,
    "stop_loss": 113.84,
    "take_profit": 105.40,
    "reasoning": ["stoch_rsi K=18.3", "near lower BB"],
    "reasoning_uri": "ipfs://Qm...",
    "ai": {
      "model": "claude-opus-4-7",
      "provider": "anthropic",
      "decision_mode": "rule_based"
    }
  }
}
```

Required: `action`. Everything else conditional on action type.

### 2.2 `external_action` — the external call that supposedly executes the claim

```json
{
  "kind": "external_action",
  "data": {
    "claim_seq": 4230,
    "venue": "coinbase_intx",
    "request": {
      "endpoint": "/api/v3/brokerage/orders",
      "method": "POST",
      "body_hash": "sha256:..."
    },
    "response": {
      "status": 200,
      "order_id": "1a2b3c4d-..."
    }
  }
}
```

`claim_seq` links back to the originating claim. `body_hash` lets verifiers
confirm the claim translated to the actual order without leaking secrets.

### 2.3 `verified` — external truth says this is what happened

```json
{
  "kind": "verified",
  "data": {
    "claim_seq": 4230,
    "action_seq": 4231,
    "source": "coinbase_intx",
    "source_endpoint": "/api/v3/brokerage/orders/historical/{id}",
    "fill_price": 108.41,
    "filled_size": 0.092,
    "fill_ts": "2026-05-05T14:32:01.847Z",
    "match": {
      "size": true,
      "side": true,
      "price_within_tolerance": true,
      "timing_within_tolerance": true
    }
  }
}
```

A receipt is **verified** when every `claim` has a corresponding `verified`
event whose `match` object is all-true.

### 2.4 `reconciliation` — periodic full-state check

```json
{
  "kind": "reconciliation",
  "data": {
    "window": "since_last",
    "local": {"balance_usd": 974.21, "positions": [...]},
    "external": {"balance_usd": 974.21, "positions": [...]},
    "match": true,
    "drift": {"balance_usd": 0.00}
  }
}
```

Compliant agents MUST emit at least one `reconciliation` per 24h. Gap longer
than 24h = chain considered un-reconciled until next event.

### 2.5 `anchor` — public timestamp anchor

```json
{
  "kind": "anchor",
  "data": {
    "merkle_root": "sha256:...",
    "covers_seq_range": [0, 4231],
    "anchor_uri": "https://twitter.com/bonnybb/status/1234567890",
    "anchor_kind": "twitter | moltbook | github_commit | btc_op_return"
  }
}
```

Daily anchor is the keystone of public verifiability — proves the chain at
seq 4231 existed at this timestamp, no retroactive editing possible.

## 3. Reconciliation adapters (per venue)

Each venue gets a small Python module conforming to:

```python
class ReconciliationAdapter:
    def fetch_order(self, order_id: str) -> dict: ...
    def fetch_position(self, symbol: str) -> dict: ...
    def fetch_balance(self) -> dict: ...
    def normalize(self, raw: dict) -> dict: ...
```

Reference adapters in v0.1:

- `receipt.adapters.coinbase_intx` (Coinbase Advanced Trade with Ed25519 / JWT auth)
- `receipt.adapters.binance_futures`
- `receipt.adapters.hyperliquid`

Future: `receipt.adapters.github` (for code agents), `receipt.adapters.x` (for content agents).

## 4. Verifier algorithm

```
verify(jsonl_path, adapters) →

1. Walk events in order. Recompute each hash. Compare to stored hash.
   Any mismatch → INVALID_CHAIN.
2. For each claim event:
   - Find matching external_action by claim_seq. Missing → UNVERIFIED_CLAIM.
   - Find matching verified by claim_seq. Missing → UNVERIFIED_CLAIM.
   - For each verified event: re-fetch from adapter, compare against stored
     `data`. Mismatch → STALE_RECONCILIATION.
3. For each anchor event: confirm anchor_uri actually exists publicly and
   contains the merkle_root. Missing/altered → BROKEN_ANCHOR.
4. Confirm at least one reconciliation per 24h since the first event.
   Gap > 24h → STALE_RECONCILIATION.

Return: chain_status ∈ {VERIFIED, INVALID_CHAIN, UNVERIFIED_CLAIM,
                       STALE_RECONCILIATION, BROKEN_ANCHOR}
```

Only `VERIFIED` chains earn the public badge.

## 5. Public viewer contract

A compliant viewer (the iBitLabs reference viewer at `receipt.ibitlabs.com/<handle>`)
SHOULD render:

- Chain status (VERIFIED / one of the failure modes)
- Last anchor timestamp + link
- Per-claim cards: claim → action → verified, with reconciliation status
- Aggregate stats: claim count, verified rate, win rate (if PnL present)
- Raw JSONL download

## 6. Versioning

`v` field on every event. v0.1 → v1.0 may add new `kind` values. Removal or
breaking field changes require new major version. Verifiers should accept
unknown `kind` values in v0.x as informational (do not fail).

## 7. What this spec is NOT

- NOT a real-time trading API (read-only)
- NOT a trust system for the agent itself — Receipt makes claims **externally
  verifiable**, but a "verified" receipt for a losing strategy is still a
  losing strategy
- NOT a replacement for exchange-side audit logs (it complements them)
- NOT cryptocurrency / a token. The hash chain is for tamper detection only.

## Appendix A: Reference implementation

`pip install receipt-trade` — minimal Python lib + CLI verifier.
Source: `github.com/AgentBonnybb/receipt`.

## Appendix B: First reference adopter

iBitLabs's `$1k → $10k` SOL perpetuals experiment publishes Receipt-compliant
data at `receipt.ibitlabs.com/sniper-v5.1`, anchored daily to GitHub +
Moltbook. Live since 2026-MM-DD.
