# Shadow Surface — Hypothesis (NOT a feature)

**Status**: archived hypothesis. Not in v0.1.1. Not in v0.2 unless trigger fires.
**Created**: 2026-05-06
**Decision frame**: "拒绝一个太早正确的想法"

---

## 1. Hypothesis

There exists a class of risks in iBitLabs (and any agentic system) that the
current Receipt chain does NOT capture: **automation that runs without ever
producing a receipt**. Examples include implicit cron jobs, side-effecting
CLI tools, AI agents with no external attestation, permission scope drift.

If true, this represents a "Shadow Surface" — actions that exist in reality
but are absent from the audit chain by construction.

## 2. Observable signals (using v0.1.1 receipt machinery, no new system)

The hypothesis is testable WITHOUT building anything new. Watch for these
patterns in the realtime chain over the 30-day dogfood window:

| Pattern | What it means |
|---|---|
| `UNVERIFIED_CLAIM` chain status | a claim was emitted but no terminal event followed |
| `heartbeat_silence` suspicion flag (>24h gap with no heartbeat) | bot was alive but didn't emit |
| `backfill_dominant_pretending_realtime` flag | retroactive log filling — shadow becomes visible |
| Reconciliation `match: false` on local vs exchange position | local state and external truth diverged silently |
| Bot or cron writes to disk / spawns subprocess but no receipt event around it | automation without attestation (manual audit) |

> **Reframed slogan (replaces "We track what it forgot to decide"):**
>
> **"If the system forgets, it shows up as a broken chain."**

This is stronger because it requires no new product line — Receipt's own
verifier already detects most of what Shadow Surface aimed to catch.

## 3. Trigger condition

```
IF
    ≥3 distinct real shadow incidents observed in 30 days of dogfood
    AND
    incidents are NOT already covered by Receipt v0.1.1 suspicion_flags
THEN
    Shadow Surface promotes to v0.2 core (new event kind or extended score dimension)
ELSE
    discard, OR merge any genuinely-new signal into v0.1.x suspicion_flags
```

## 4. Anti-trigger (when to discard outright)

- 30 days pass with 0 incidents → concept doesn't apply to trading domain
- All observed incidents are already covered by existing suspicion_flags
  (no_error_events / sudden_activity_gap / heartbeat_silence / etc.)
- iBitLabs's own chain reveals that "shadow" is a definition problem,
  not a tooling problem (e.g., shadow exists only in operator's mental
  model, not in any verifiable reality)

## 5. What's NOT in scope

- Secret exposure scanning (commodity — gitleaks / trufflehog)
- Implicit file detection (commodity — `git status -uall`, lint)
- Boundary drift (real but ~6 months of policy-engine work; not a v0.x candidate)

## 6. Cross-refs

- Receipt Spec v0.1.1: `~/Documents/receipt/SPEC.md`
- Trust score suspicion flags: `~/Documents/receipt/docs/trust_score.md` §2.6
- Decision pattern: `feedback_real_data_before_features.md`
  (operator memory rule established 2026-05-06)
