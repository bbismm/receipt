# P0.1 — sniper-v5.1 Receipt integration plan

**Status**: DRAFT, awaiting operator review.
**Target file**: `~/ibitlabs/sol_sniper_main.py`
**Goal**: emit Receipt-v0.1.1-compliant chain in real time as the LIVE bot trades.
**Anti-goal**: change the bot's trading behavior in any observable way.

## Live-trade context (must respect)

As of Scan #34 (2026-05-06 00:15 UTC):
- LIVE SHORT open since 04-30, hold = 129h, 12th consecutive scan past 36h boundary
- MAE doubled in 12h (−$7 → −$14.10)
- Regime sideways for 3 consecutive scans (drift confirmed, not noise)
- Operator stance per CLAUDE.md: **observe, do not force-exit**

**Implication for this work**: zero changes to entry/exit/risk logic. Receipt is
pure observability, like `close_verify` and `entry_confidence_map` from 2026-05-01.

## Design

### Surface area

~50 lines added to sol_sniper_main.py, in 4 logical sites:

```
1. import + lazy init (top of file, ~10 lines)
2. claim + external_action + verified at signal/fill (line ~770 region, ~15 lines)
3. claim + external_action + verified at close (line ~600 region, ~15 lines)
4. heartbeat every 5min in main loop idle path (line ~530 region, ~5 lines)
```

`error` events triggered in the existing exception-handler `except` blocks at the same call sites.
`reconciliation` runs as a separate periodic call (every 6h via launchd cron, NOT in bot main loop).
`anchor` runs as a separate daily launchd cron (NOT in bot main loop).

### Failure-mode contract (non-negotiable)

```python
class _SafeReceipt:
    """Every Receipt call wrapped in try/except. A failure to write a receipt
    NEVER blocks trading. NEVER raises. Logs a warning and returns None."""
    def __init__(self, agent, out_path):
        try:
            from receipt import Receipt
            self._r = Receipt(agent=agent, out_path=out_path)
        except Exception as e:
            logger.warning(f"[RECEIPT] init failed: {e}")
            self._r = None
    def __getattr__(self, name):
        if self._r is None: return lambda *a, **kw: None
        method = getattr(self._r, name, None)
        if method is None: return lambda *a, **kw: None
        def safe(*args, **kwargs):
            try: return method(*args, **kwargs)
            except Exception as e:
                logger.warning(f"[RECEIPT] {name} failed: {e}")
                return None
        return safe
```

### Default OFF

```python
RECEIPT_ENABLED = os.environ.get("SNIPER_RECEIPT", "0") == "1"
```

The plist EnvironmentVariables is unchanged for the first 24 hours.
Operator flips `SNIPER_RECEIPT=1` only after smoke-testing on shadow.

### Output path

```
~/ibitlabs/audit_export/sniper-v5.1.receipt.jsonl
```

Same path as the current backfill output. Backfill is renamed to
`sniper-v5.1.backfill.receipt.jsonl` when realtime starts. The two NEVER share
a chain — realtime starts a fresh genesis (the chains are independent).

### What does NOT touch sol_sniper_main.py

- IPFS anchoring → separate launchd job `com.ibitlabs.receipt-anchor-daily`
- Reconciliation against Coinbase API → separate launchd job `com.ibitlabs.receipt-recon-6h`
- Score/verdict computation → only ever runs in viewer or verifier service, not in bot

## Rollout phases

| Phase | Action | Gate |
|---|---|---|
| **0** | Operator creates Notion battle room (this doc + risk discussion) | manual review |
| **A** | Apply patch to sol_sniper_main.py with `SNIPER_RECEIPT=0` default | py_compile passes; live bot restart NOT required (env-gated) |
| **B** | Bot restart with `SNIPER_RECEIPT=0` (still off). Confirm no behavior change for 12h | live PnL unchanged; ghost_watchdog clean |
| **C** | Flip `SNIPER_RECEIPT=1` on **shadow only** for 24h | shadow chain validates with `receipt verify` ✅ |
| **D** | Flip `SNIPER_RECEIPT=1` on live | live chain accumulates real-time events |
| **E** | Wire IPFS anchoring + 6h reconciliation crons | first daily anchor lands on IPFS |

Rollback at any phase: set `SNIPER_RECEIPT=0`, kill bot, bot restarts ignoring receipt code path entirely.

## Acceptance criteria

- 11/11 receipt tests still pass after patch
- `python3 -m py_compile sol_sniper_main.py` clean
- Phase B: 12h live with `SNIPER_RECEIPT=0` and zero `[RECEIPT]` log lines
- Phase C: shadow chain has ≥1 claim → external_action → verified → reconciliation lifecycle, `receipt verify` ✅
- Phase D: 30 days of continuous events, daily anchor success rate ≥ 95%
- Phase D end: backfill chain DEPRECATED in favor of realtime chain; viewer link points to realtime

## Risks

| Risk | Mitigation |
|---|---|
| Receipt I/O blocks trading hot path | All calls wrapped via `_SafeReceipt` (returns None on any failure, logs warning) |
| Disk fill from JSONL growth | Estimated ~150 events/day → ~30KB/day → trivial. `audit_export/` already in cleanup ignore list. |
| Coinbase Advanced Trade SDK reverification breaks | Reconciliation job is SEPARATE process; failure doesn't affect bot |
| Bot crashes mid-write → corrupted last line | Receipt uses fsync after each line; on restart `_tail_state` reads to last valid line |
| Replay between bot restarts | Receipt's `_tail_state` reopens chain at last seq + last hash; chain continues |
| Identity binding fails (no real exchange order_id available locally) | Use `executor.position.get('order_id')` → if None, skip verified, emit error |

## What I need from you

1. Create Notion battle room under "Strategy Optimization"
   (`3403c821a4aa81b5ba43dbcdb62e95bc`) titled
   **"Receipt v0.1.1 sniper integration (P0.1)"** with the content from
   `integration/sniper_v5_1_notion_draft.md` (sibling file)
2. Confirm the LIVE SHORT trade either closes or stabilizes before phase A
3. Confirm phase B → C → D gating: I do NOT auto-flip `SNIPER_RECEIPT=1`,
   you do it
4. Confirm rollout schedule:
   - phase A apply patch: when you say
   - phase B observe 12h: automatic after A
   - phase C shadow on: when you say
   - phase D live on: only after 24h shadow validation passes
