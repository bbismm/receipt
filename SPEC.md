# Receipt Spec v0.1

**Status**: draft, 2026-05-05
**Maintainer**: iBitLabs
**License**: CC-BY-4.0 (this spec) | MIT (reference implementation)
**Schema version**: `1`

> Receipt is an append-only, verifiable event log for agent actions.

## 0. Design goals

- Make claims auditable
- Bind actions to verifiable outcomes
- Enable third-party verification
- Minimize trust assumptions

The first deployed use case is AI trading bots and copy-trading services. The
envelope is domain-neutral: any AI agent that takes external actions can emit
a Receipt chain.

## 1. Event envelope (canonical)

```json
{
  "v": "0.1",
  "schema_version": "1",
  "ts": 1710000000000,
  "seq": 1024,
  "agent": "ibitlabs_agent_v3",
  "kind": "claim",
  "data": {},
  "prev_hash": "sha256:abc...",
  "hash": "sha256:def..."
}
```

**`ts`** is **integer milliseconds since Unix epoch** (UTC). 13 digits.
Sub-second precision matters for trading; ms is the cross-language default
(JavaScript Date, Rust SystemTime, Java Instant). All times in `data.*`
fields are also ms unless explicitly suffixed otherwise.

**`schema_version`** is the spec schema version. v0.1 ships schema_version `1`.

**`hash`** = SHA-256 of canonical JSON of
`{v, schema_version, ts, seq, agent, kind, data, prev_hash}` — see §8.

`prev_hash` of `seq=0` is `"sha256:" + 64 zeros`.

## 2. Event kinds

```
claim               # 声明：agent 要做什么
external_action     # 外部执行：下单 / API call / git push / tx
verified            # 第三方验证：external truth 复核
reconciliation      # 对账：周期性全状态盘点
anchor              # 上链 / 外部锚定
error               # 执行失败：必须有
heartbeat           # 系统活跃：必须有
signal_rejected     # 决策被拒：可选
```

**Hard rule**: every `claim` MUST eventually terminate in one of:
- `verified` (success path)
- `error` (failure path)
- `signal_rejected` (proposed but blocked)
- `reconciliation` event covering its period (catch-up after gap)

Silent drop is non-conforming.

## 3. Claim → verification flow

Success path:
```
claim → external_action → verified → reconciliation → anchor
```

Failure paths:
```
claim → error
claim → signal_rejected
```

## 4. `claim` event

```json
{
  "kind": "claim",
  "data": {
    "action": "open_long",
    "symbol": "BTC-USD",
    "side": "buy",
    "size": 0.1,
    "price_intended": 67000.0,
    "stop_loss": 65000.0,
    "take_profit": 70000.0,
    "ai": { ... see §6 ... }
  }
}
```

Required: `action`. Other fields domain-specific.

## 5. `external_action` event

```json
{
  "kind": "external_action",
  "data": {
    "claim_seq": 1024,
    "venue": "coinbase",
    "request": {
      "endpoint": "/api/v3/brokerage/orders",
      "method": "POST",
      "body_hash": "sha256:..."
    },
    "response": {
      "status": 200,
      "order_id": "abc123",
      "execution_id": "def456"
    }
  }
}
```

`response.order_id` / `response.execution_id` are the **identity anchor**
for later `verified` events.

## 6. AI metadata

```json
{
  "model": "gpt-5",
  "provider": "openai",
  "decision_mode": "auto",
  "prompt_hash": "sha256:xxx",
  "temperature": 0.2,
  "tokens_used": 512,
  "agent_version": "3.2.1"
}
```

**Design principle**: do NOT store the prompt itself (privacy + cost).
Store `prompt_hash` so an auditor with access to the operator's prompt
template can verify it matches.

`agent_version` is REQUIRED for any AI-emitting agent. Without it,
"the model drifted" is unfalsifiable.

For rule-based agents: `decision_mode: "rule_based"`,
`agent_version` required, prompt_hash/temperature/tokens_used MAY be omitted.

## 7. `verified` event + match object

```json
{
  "kind": "verified",
  "data": {
    "claim_seq": 1024,
    "action_seq": 1025,
    "source": "coinbase",
    "source_endpoint": "/api/v3/brokerage/orders/historical/{id}",
    "trust_tier": "exchange_realtime",
    "fill_price": 67012.5,
    "filled_size": 0.1,
    "fill_ts": 1710000003000,
    "match": {
      "symbol": "BTC-USD",
      "side": "buy",
      "size": 0.1,
      "price_match": true,
      "time_match": true,
      "id_match": true,
      "tolerance_used": {
        "price": 0.002,
        "time_ms": 5000
      }
    }
  }
}
```

**Match-completeness rule**:
- `id_match` MUST be `true` — order_id or execution_id matches the
  external_action. **Identity binding takes priority over price.**
- `symbol` MUST be present
- `tolerance_used` MUST be recorded — without it, tolerance can be
  retroactively widened to mask mismatches

Verifiers SHOULD treat `id_match: false` as `SUSPECT_VERIFICATION` regardless
of price match.

## 8. Trust tier (mandatory ENUM)

Every `verified` and `reconciliation` event MUST carry `data.trust_tier`,
ENUM:

| Tier | Source |
|---|---|
| `exchange_realtime` | Re-fetched from authoritative venue API < 5 min ago |
| `exchange_delayed`  | Re-fetched but stale (5 min – 24h) |
| `api_verified`      | Different authoritative API (on-chain explorer, signed broker statement) |
| `backfill_local`    | Reconstructed from operator's local DB / logs |
| `manual`            | Operator-attested, no programmatic source |

No default value. Implementations MUST set it explicitly.

## 9. `reconciliation` event

```json
{
  "kind": "reconciliation",
  "data": {
    "period": "2026-05-01T00:00Z/2026-05-01T06:00Z",
    "trust_tier": "exchange_realtime",
    "matched": 24,
    "unmatched": 1,
    "errors": 2
  }
}
```

**Cadence**:
- Minimum: 1 per 6 hours
- Recommended: near-realtime streaming (≤ 5 min after each external_action)

Gap > 6h between reconciliation events → chain status `STALE_RECONCILIATION`
until the next reconciliation event.

## 10. `anchor` event

```json
{
  "kind": "anchor",
  "data": {
    "merkle_root": "sha256:...",
    "covers_seq_range": [0, 1024],
    "anchor_uri": "ipfs://Qm.../receipt_2026-05-05.jsonl",
    "anchor_kind": "ipfs"
  }
}
```

`anchor_kind` ∈ `{twitter, moltbook, github_commit, ipfs, arweave,
ethereum, btc_op_return}`. Permanent layers (`ipfs`, `arweave`, `ethereum`,
`btc_op_return`) are protocol-grade evidence. Social anchors are
informational.

**Rules**:
- anchor MUST reference the hash / merkle_root
- anchor cannot rewrite history — it only proves "this state existed at
  this time"
- compliant agents SHOULD anchor to at least one permanent layer per 24h

## 11. `error` event

```json
{
  "kind": "error",
  "data": {
    "claim_seq": 1024,
    "phase": "external_action",
    "error_type": "exchange_api_failure",
    "message": "order rejected: insufficient margin",
    "retryable": true,
    "context": {"endpoint": "/orders", "request_id": "..."}
  }
}
```

Without this, silent failure becomes indistinguishable from intentional
omission. Required when an attempted action does not complete.

`phase` ∈ `{external_action, verified, reconciliation, other}`.

## 12. `heartbeat` event

```json
{
  "kind": "heartbeat",
  "data": {
    "status": "alive",
    "latency_ms": 120
  }
}
```

Compliant agents SHOULD emit a heartbeat at least every 5 minutes when no
other event is being written. Heartbeats prove the system was alive and
chose to take no action — distinguishing presence-with-restraint from
selective silence.

## 13. `signal_rejected` event (optional)

```json
{
  "kind": "signal_rejected",
  "data": {
    "would_be_action": "open_long",
    "rejected_by": "regime_gate",
    "reason": "30d regime is up, long signal counter-trend",
    "ai": { ... §6 shape ... }
  }
}
```

Critical for AI accountability: if a model proposed an action and a guard
killed it, both are recorded. Without this, "the AI never made bad calls"
becomes unfalsifiable.

## 14. Hash chain — canonicalization

Canonical key order (top-level, used for hashing):

```
(v, schema_version, ts, seq, agent, kind, data, prev_hash)
```

`hash = "sha256:" + hex(SHA-256(canonical_bytes))`

**Canonical JSON rules** (RFC 8785 / JCS subset for v0.1):

1. All keys (top-level and recursively in `data`) sorted lexicographically
   by Unicode code point
2. No whitespace between tokens — separators `(",", ":")`
3. UTF-8 encoding, non-ASCII NOT escaped (`ensure_ascii=False`)
4. Numbers as JSON spec literals — no leading `+`, no trailing zeros in
   fractional parts unless mandated
5. Strings: standard JSON escaping; senders SHOULD send NFC-normalized
   strings
6. Booleans `true`/`false`; absent fields are `null` (NOT omitted)

Implementations in different languages MUST produce byte-identical
`canonical_bytes` for the same logical event, or chains break across
implementations. v1.0 will adopt full RFC 8785.

## 15. Verifier algorithm (summary)

```
verify(jsonl, adapters) →

1. Walk events. Recompute each hash. Mismatch → INVALID_CHAIN.
2. Check seq monotonic and prev_hash linkage.
3. For each claim:
   - Find matching external_action (by claim_seq) OR error OR signal_rejected.
     Missing all three → UNVERIFIED_CLAIM.
   - If external_action present: find matching verified.
     Missing → UNVERIFIED_CLAIM.
   - Verified must have id_match: true → else SUSPECT_VERIFICATION.
4. For each anchor: confirm anchor_uri dereferenceable + merkle_root matches.
   Failure → BROKEN_ANCHOR.
5. Confirm hard reconciliation gap ≤ 6h.
   Larger → STALE_RECONCILIATION.

Return: VERIFIED | INVALID_CHAIN | UNVERIFIED_CLAIM | SUSPECT_VERIFICATION
      | STALE_RECONCILIATION | BROKEN_ANCHOR | EMPTY
```

## 16. Example flow

### Claim

```json
{
  "v": "0.1", "schema_version": "1",
  "ts": 1710000000000, "seq": 1024,
  "agent": "ibitlabs_agent_v3",
  "kind": "claim",
  "data": {
    "action": "open_long", "symbol": "BTC-USD",
    "side": "buy", "size": 0.1
  },
  "prev_hash": "sha256:...", "hash": "sha256:..."
}
```

### External action

```json
{
  "kind": "external_action",
  "data": {
    "claim_seq": 1024,
    "venue": "coinbase",
    "response": {"order_id": "abc123"}
  }
}
```

### Verified

```json
{
  "kind": "verified",
  "data": {
    "claim_seq": 1024,
    "trust_tier": "exchange_realtime",
    "match": {
      "symbol": "BTC-USD", "side": "buy", "size": 0.1,
      "price_match": true, "time_match": true, "id_match": true,
      "tolerance_used": {"price": 0.002, "time_ms": 5000}
    }
  }
}
```

## 17. Attack model

Receipt v0.1 is designed to make these attacks **detectable** by any third
party with a copy of the chain and access to the venue's public API:

### 17.1 Selective logging attack
*Operator records only successful trades.*
**Mitigation**: heartbeat REQUIRED + reconciliation MANDATORY → gaps in
trades-vs-balance reconciliation surface omitted events.

### 17.2 Price-matching manipulation
*Operator widens price tolerance retroactively to mask mismatches.*
**Mitigation**: `tolerance_used` MUST be recorded per event +
`id_match` priority over price → tolerance widening visible in chain;
identity (order_id) cannot be retroactively widened.

### 17.3 Backfill forgery
*Operator fabricates historical chain after the fact.*
**Mitigation**: anchors on permanent layers (IPFS / Arweave / Ethereum /
btc_op_return) + `trust_tier` flagging makes backfill-only chains visibly
weaker than realtime chains.

### 17.4 AI decision drift
*Model behavior changes silently between checkpoints.*
**Mitigation**: `prompt_hash` + `agent_version` REQUIRED →
verifiers can detect undocumented agent changes.

### 17.5 Out-of-scope (NOT defended in v0.1)

- Initial strategy parameters at genesis claim are operator-attested
- Compromised venue API as ground truth
- Multi-account shell games (operator runs N bots, publishes only the
  winner's chain)

Receipt does not eliminate fraud. It raises the cost of fraud from zero
(post screenshots) to non-zero (must publish a chain that re-derives
correctly from venue API and survives daily anchoring).

## 18. Naming, license, packaging

- Project: **Receipt**
- Spec: **Receipt Spec v0.1**
- Code license: **MIT**
- Spec license: **CC-BY-4.0**
- Reference impl: `pip install receipt-protocol`
- Source: `github.com/AgentBonnybb/receipt`

## 19. Versioning

`v` and `schema_version` on every event. v0.1 → v1.0 may add new `kind`
values and optional fields. Verifiers MUST accept unknown `kind` values
in v0.x as informational (forward-compat) — do not fail. Breaking
changes require a major version bump.
