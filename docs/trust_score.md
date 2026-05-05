# Receipt Trust Score v0.1

**Status**: draft. License: CC-BY-4.0.
**Goal**: a single 0–100 number anyone can recompute from a Receipt JSONL,
that captures "how much do these claims survive external scrutiny".

> Trust score is open algorithm. If it's a black box, no one trusts it.

## Output

```json
{
  "agent": "iBitLabs/sniper-v5.1",
  "window": "2026-04-05/2026-05-05",
  "score": 78,
  "grade": "B",
  "components": {
    "id_match_rate":      {"weight": 50, "raw": 0.95, "contribution": 47.5},
    "trust_tier_quality": {"weight": 20, "raw": 0.92, "contribution": 18.4},
    "anchor_freshness":   {"weight": 10, "raw": 1.00, "contribution": 10.0},
    "reconciliation":     {"weight": 10, "raw": 0.83, "contribution":  8.3},
    "error_transparency": {"weight":  5, "raw": 0.40, "contribution":  2.0},
    "liveness":           {"weight":  5, "raw": 0.95, "contribution":  4.75}
  },
  "deductions": [
    {"reason": "1 verified event with id_match=false", "minus": 10}
  ],
  "chain_status": "VERIFIED",
  "computed_at": 1714912321847,
  "recompute_command": "receipt score path/to/chain.jsonl"
}
```

`grade`: A (≥90) | B (≥75) | C (≥60) | D (≥40) | F (<40).

## Components

### 1. `id_match_rate` (weight 50)
```
id_match_rate = count(verified where match.id_match==true) / count(verified)
```
Identity binding is non-negotiable per SPEC §17.2. **Half the score** because
without identity matching, every other check is decorative — anyone can
publish "verified" events with right prices that have nothing to do with
real orders.

### 2. `trust_tier_quality` (weight 20)
Per-event tier weight, then weighted average over all `verified` +
`reconciliation` events:

| trust_tier | weight |
|---|---|
| exchange_realtime | 1.00 |
| exchange_delayed  | 0.70 |
| api_verified      | 0.70 |
| backfill_local    | 0.30 |
| manual            | 0.00 |

A chain entirely backed by `backfill_local` caps at 0.30 here = 6/20 points.

### 3. `anchor_freshness` (weight 10)
```
anchor_freshness = 1.0  if last permanent-layer anchor within 24h
                 = 0.5  if within 7 days
                 = 0.0  otherwise
```
Permanent layers: `ipfs`, `arweave`, `ethereum`, `btc_op_return`. Social
anchors (twitter / moltbook / github_commit) count for half (0.5 ceiling).

### 4. `reconciliation` (weight 10)
```
gap_score = clamp(1 - (max_recon_gap_hours - 6) / 18, 0, 1)
```
- 6h gap → 1.0 (compliant)
- 12h gap → 0.67
- 24h gap → 0.0

### 5. `error_transparency` (weight 5)
```
if total_attempted_actions == 0:    score = 0.5  (not enough data)
elif error_count == 0:               score = 0.4  (suspiciously perfect)
elif error_rate <= 0.1:              score = 1.0
elif error_rate <= 0.25:             score = 0.7
else:                                score = 0.3  (chain mostly errors)
```

**Counter-intuitive but important**: a chain with zero errors is *suspicious*
because real systems fail. Honest chains show ~5–15% error rate.

### 6. `liveness` (weight 5)
Heartbeat density: gap-free 5-minute coverage over the window.
```
liveness = covered_5min_buckets / total_5min_buckets_in_window
```

## Deductions (subtracted after weighted sum)

| Trigger | Deduction |
|---|---|
| ANY `verified` event with `id_match=false` (SPEC §7) | −10 per event, max −30 |
| Chain status ∈ {INVALID_CHAIN, BROKEN_ANCHOR} | score forced to 0 |
| Chain status = SUSPECT_VERIFICATION | cap at 50 |
| Chain status = STALE_RECONCILIATION | cap at 70 |
| Last `error` event has `retryable=false` and no follow-up resolution | −5 |

## Anti-gaming considerations

| Game | Defense |
|---|---|
| Submit 10000 trivial claims with id_match=true to inflate denominator | trust_tier weighting kicks in; backfill_local at scale ≠ exchange_realtime |
| Anchor only social, never permanent | anchor_freshness caps at 0.5 |
| Hide all errors | error_transparency penalizes 0% error rate |
| Skip heartbeats during quiet periods | liveness drops |
| One realtime event + 999 backfill | weighted average exposes the dilution |

## Recompute requirement

Every viewer MUST display the score breakdown. A standalone CLI:
```
receipt score path/to/chain.jsonl
```
prints the JSON above. The score is **deterministic given the same JSONL**.
If `receipt.ibitlabs.com` shows score X, anyone with the JSONL can
recompute and confirm X.
