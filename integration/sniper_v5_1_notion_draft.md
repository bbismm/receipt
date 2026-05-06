# Receipt v0.1.1 sniper integration (P0.1) — battle room

**Created**: 2026-05-06
**Owner**: Bonny
**Parent page**: Strategy Optimization (3403c821a4aa81b5ba43dbcdb62e95bc)
**Status**: planning

---

## Why this exists

iBitLabs has shipped Receipt Spec v0.1.1 + reference implementation
(`~/Documents/receipt/`, tag `v0.1.1`, 9 commits, frozen). The first
adopter — iBitLabs's own sniper bot — currently publishes only a backfill
chain (`sniper-v5.1.receipt.jsonl`, 419 events scoring Unverified 39 in
v0.1.1 because all events are `trust_tier=backfill_local` and span 0.1h).

To get to a public-launch-ready state, we need 30 consecutive days of
**realtime** Receipt events emitted by the live bot. This battle room tracks
that integration work.

## Hard constraints

1. **Zero behavior change** in trading logic. Receipt is pure observability.
2. **Default OFF** via `SNIPER_RECEIPT` env. Operator flips manually.
3. **Failure-mode**: Receipt I/O failure NEVER blocks trading.
4. **Live bot is mid-trade**: 129h SHORT, 12th scan past 36h boundary, MAE
   doubling. Phase A patch can apply (env-gated, default off), but phase B
   restart MUST wait until LIVE SHORT closes or operator explicitly approves
   restart-while-open.
5. Backfill chain (sniper-v5.1.backfill.receipt.jsonl) is preserved as
   historical artifact; realtime chain starts fresh genesis.

## Plan

See `~/Documents/receipt/integration/sniper_v5_1_plan.md` for full
implementation plan. Summary:

- ~50 lines added in 4 logical sites (init + signal-fill + close + heartbeat)
- `_SafeReceipt` wrapper guarantees no exception escapes
- 5-phase rollout: 0 (this room) → A (patch) → B (12h observe) → C (shadow on)
  → D (live on) → E (IPFS anchor + recon crons)

## Decision log

| Date | Decision |
|---|---|
| 2026-05-06 | Battle room created. Pending: operator review of plan. |
| ____ | Phase A green-lit (patch applied, env=0) |
| ____ | Phase B clean (12h zero `[RECEIPT]` lines) |
| ____ | Phase C green-lit (SNIPER_RECEIPT=1 on shadow) |
| ____ | Phase D green-lit (SNIPER_RECEIPT=1 on live) |
| ____ | Phase E (anchor + recon crons live) |
| ____ | 30-day milestone: realtime chain VERIFIED (or not) |

## Open questions

1. When does LIVE SHORT close? (Currently 129h, regime sideways, MAE −$14.10)
2. Should phase D start the moment LIVE closes, or wait for next clean entry?
3. Anchor target for phase E: IPFS via Pinata, or w3s, or self-host?
4. After 30 days realtime chain runs clean, do we push `github.com/AgentBonnybb/receipt`
   public + send launch post, or wait longer?

## Cross-refs

- Receipt Spec v0.1.1: `~/Documents/receipt/SPEC.md`
- Trust Score & Verdict v0.1.1: `~/Documents/receipt/docs/trust_score.md`
- Implementation plan: `~/Documents/receipt/integration/sniper_v5_1_plan.md`
- Patch DRAFT: `~/Documents/receipt/integration/sniper_main_patch.py`
- Past adverse incident: claude-mm 2026-05-05 audit module injection (132 lines, hallucinated Coinbase API endpoints, file in `~/Documents/receipt/SPEC.md` `Appendix A` is the corrective)
