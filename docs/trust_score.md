# Receipt Trust Score & Verdict v0.1

**Status**: candidate. License: CC-BY-4.0.

> **Trust score is not performance. It is verifiability.**

This document defines two outputs every Receipt verifier MUST produce:

1. **Verdict** (categorical) — what users see first
2. **Trust Score** (0–100) — for ranking / comparison

Hard rule: **Verdict and Score must never contradict.**
A green ✅ chain must score ≥ 75. A red ❌ chain must score < 40.

## 1. Verdict (categorical, rules-based)

Three states. Pure rules — no weighted math, no opinions.

### ✅ Verified (green)
ALL of the following:
- Coverage ≥ 90% (receipts / total_claims)
- Accuracy ≥ 80% (verified / receipts)
- Mismatch rate ≤ 5%
- Has at least one reconciliation event in window
- Has at least one anchor event in last 24h
- **Trust-tier majority on `verified` events ∈ {exchange_realtime, exchange_delayed, api_verified}**

The trust-tier majority rule prevents a chain made entirely of
`backfill_local` (operator's own logs) from claiming green ✅. Backfill is
material evidence but it's not exchange-confirmed; it caps at Mixed.

### ⚠️ Mixed (yellow)
Verdict is Verified does NOT hold, AND ANY of:
- Coverage in [50%, 90%)
- Mismatch rate in (5%, 20%]
- Some missing receipts
- Anchor unstable (gap > 24h but ≤ 7 days)

### ❌ Unverified (red)
ANY of:
- Coverage < 50%
- Mismatch rate > 20%
- No anchor at all (or last anchor > 7 days)
- Long heartbeat silence suggesting selective logging
- INVALID_CHAIN per SPEC §15
- **Trust-tier majority is `manual`** (operator-attested with no programmatic source = no evidence)

Verdict is the **cognitive anchor**. Users can hold this in their head.
"Verified" = trust. "Mixed" = caution. "Unverified" = walk away.

## 2. Trust Score (0–100, weighted average)

Five equal dimensions, 20 points each:

```
Trust Score = (Coverage + Accuracy + Consistency + Transparency + Integrity) / 5
```

Each dimension is normalized to 0–100, then averaged.

### 2.1 Coverage (20)
What share of the agent's claimed actions actually have receipts.
```
coverage = count(claim with terminating verified|error|signal_rejected) / count(claim)
score = coverage * 100
```

### 2.2 Accuracy (20)
Of the claims with receipts, how many actually verified.
```
verified_count = count(verified events with match.id_match=true)
receipts_count = count(claims that reached verified or error stage)
mismatch_rate  = count(verified with id_match=false) / receipts_count

score = clamp((verified_count / receipts_count) * 100 - mismatch_rate * 200, 0, 100)
```
Mismatch penalty is 2× — id-mismatch is fraud signal, not noise.

### 2.3 Consistency (20)
Time-bucket coverage. Divide window into 5-minute buckets; count buckets
containing at least one event (any kind, including heartbeat).
```
covered = count(5-min buckets with ≥ 1 event)
total   = total 5-min buckets in [first_event_ts, last_event_ts]
score   = (covered / total) * 100
```
Long unexplained silences cost points. Heartbeats are how an honest agent
proves "I was alive and chose to take no action".

### 2.4 Transparency (20)
Starts at 100, deductions for what's NOT being reported:
- No `error` event ever in window: −20 (real systems fail; perfect logs are suspicious)
- No `reconciliation` event ever: −30
- Any `verified` event missing `match.tolerance_used`: −20

### 2.5 Integrity (20)
Chain-level structural soundness:
- INVALID_CHAIN (broken hash / seq): score = 0 (fatal)
- No anchor at all: score capped at 50
- Anchor stale (last permanent-layer anchor > 24h): −30
- Anchor stale (> 7d): score capped at 30
- Otherwise: 100

## 3. Output shape

```json
{
  "agent": "iBitLabs/sniper-v5.1",
  "window": {"from_ms": 1709000000000, "to_ms": 1714912321847},

  "verdict": "Verified",
  "verdict_color": "green",

  "score": 82,
  "dimensions": {
    "Coverage":     {"score": 100, "detail": "139/139 claims have receipts"},
    "Accuracy":     {"score": 100, "detail": "139/139 verified, 0 mismatches"},
    "Consistency":  {"score":  64, "detail": "covered 21 of 33 5-min buckets in window"},
    "Transparency": {"score":  50, "detail": "−30 no reconciliation; −20 missing tolerance"},
    "Integrity":    {"score":  90, "detail": "chain valid; anchor 4h ago on github_commit (−10 social-only)"}
  },

  "rule_breaks": [],
  "computed_at_ms": 1714912321847,
  "recompute_command": "open /viewer/index.html — runs in your browser"
}
```

## 4. Consistency rule (verdict ↔ score)

The verifier MUST enforce:

| Verdict | Score range allowed |
|---|---|
| Verified ✅   | 75 – 100 |
| Mixed ⚠️    | 40 – 74  |
| Unverified ❌ | 0 – 39   |

If the dimensional math produces a score outside the verdict's range, the
verifier downgrades or upgrades the verdict to match. Verdict wins because
verdict is the human truth.

## 5. UX requirements (non-negotiable)

Every viewer MUST display, in this order:

1. **Verdict** as the largest visual element (✅ / ⚠️ / ❌ + color band)
2. **Score** with grade label below the verdict
3. **Per-dimension breakdown** with the `detail` strings — users must be
   able to see *why* each number is what it is, in plain language
4. **"Verified locally in your browser"** affordance — users should know
   the score was recomputed in their session, not handed down from a server

A viewer that shows score without verdict, or hides the dimensional
breakdown, is non-conforming.

## 6. What this is NOT

- **NOT performance**. A bot that loses money but publishes flawless
  receipts scores 100. That's correct: Receipt verifies *honesty*, not
  *skill*. Performance evaluation is downstream.
- **NOT machine learning**. The formula is fully published; anyone can
  predict their own score before publishing. This is the point.
- **NOT a moat for iBitLabs**. The formula is CC-BY-4.0; anyone can run
  their own verifier with the same algorithm and get identical results.
  iBitLabs's edge comes from running the canonical reference deployment,
  not from owning the math.

## 7. Future (v0.2+, NOT in v0.1)

- Per-venue trust weighting (Coinbase Advanced ≠ random Telegram bot)
- User-tunable thresholds (institutional users may want stricter)
- Time-decay (last 30d weighted heavier than older)
- Multi-agent / multi-venue chains
- ML-augmented anomaly detection

v0.1 is deliberately rules-only because **the algorithm is the trust**.
The moment we can't explain to a non-engineer why their score is X, we've
lost.
