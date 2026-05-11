# Worked example: a code-writing AI auditing itself

**The pain this addresses, in plain words:**

> AI says it's done. The human doesn't know if it actually finished, what the
> result was, or whether it's lying.

This is the universal experience of running a coding agent (Cursor, Claude
Code, a homebrew AutoGPT loop). The agent returns "done — I added `handleX()`
to `Y.ts`" and you have no way to tell whether:

1. It actually did what it claimed
2. It touched other files silently
3. The build / tests passed after the edit
4. It could be making any of this up

Receipt v0.1 — originally written to audit a trading bot — turns out to
solve this without modification. The four-phase lifecycle is identical
across domains:

```
   claim ──▶ external_action ──▶ verified ──▶ reconciliation
```

What changes between "trading bot" and "code-writing AI" is the *content*
of the events, not the structure:

| trading bot               | code-writing AI                                            |
| ------------------------- | ---------------------------------------------------------- |
| `symbol` (SOL-USD)        | file path (`src/scoring.py`)                               |
| `size`, `price`           | pre-edit hash, post-edit hash, diff hash                   |
| Coinbase API              | git diff / py_compile / pytest                             |
| exchange position         | `git status --porcelain` output                            |
| USD balance               | number of files modified vs. number claimed                |
| `exchange_realtime` tier  | `api_verified` tier (subprocess result IS an external API) |
| IPFS daily anchor         | `git push` to public receipts branch (commit hash anchors) |

## Mapping the 3 pain points to 3 receipt events

| Your pain                                                              | Receipt event           | What it captures                                                                                                                                                                                                |
| ---------------------------------------------------------------------- | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "AI says it's done"                                                    | `claim` (phase 1)       | Agent declares intent **before** doing anything. Includes `user_prompt_hash` so it can't quietly drift from the original ask.                                                                                  |
| "I don't know if it really finished, what the result is"               | `external_action` + `verified` (phases 2–3) | Agent records what it actually did to the file system / git / subprocess, with hashes. Verified step compares "what I claimed" against "what really exists now".                                |
| "Is it lying?"                                                         | hash chain + `reconciliation` (phase 4) | Every event hash-links to the previous — retroactive edits are detectable. Reconciliation forces the agent to compare its local belief against `git status` ground truth, periodically. |

## What lies this catches

| Lie                                                       | How it's caught                                                                                                                  |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| "I did X" — but never did                                 | `claim` has no terminating `verified` or `error`; chain shows orphan claim                                                       |
| "I'm done" — but went silent mid-task                     | Missing heartbeats + claim still open                                                                                            |
| "I did X" — but actually did Y                            | `external_action.request` records the actual subprocess command + response; mismatch with `claim.intent` surfaces in verifier UI |
| Quiet edits to files outside the claim                    | `reconciliation` compares `claim.expected_change.files_modified` to `git status --porcelain` output                              |
| Editing the receipt itself after the fact                 | Hash chain breaks on re-verification                                                                                             |

## What lies this does **not** catch (be honest)

A code-writing AI in real time could:

- Run `cat fake-output.txt` and record its content as if it were a real test
  pass
- Write a passing test that doesn't actually exercise the feature it claimed
  to add

Receipt protects against **retroactive tampering** + **omission lies** + **drift
from declared intent**. It does not, by itself, protect against an agent
actively constructing fake evidence at write time. The mitigation is the same
as for trading: external anchoring (so the lie has to commit before
reconciliation), reconciliation events (where local belief is compared to
external truth and divergence is logged), and trust_tier signals that flag
"this evidence is local-only" vs "externally confirmed".

## Walk-through

The script `code_writing_agent.py` simulates two tasks:

1. **Happy path** (task 1, seq 1–5):
   - claim: "I'll add `score(values)` to `src/scoring.py`"
   - external_action: wrote the file, pre/post hashes recorded
   - external_action: ran `python3 -m py_compile` — exit 0
   - verified: post-edit hash matches expected; syntax clean; file size grew

2. **Failure path** (task 2, seq 6–9):
   - claim: "I'll extend `score()` with a `median` kwarg"
   - external_action: wrote the file
   - external_action: ran `pytest` — exit 1, NameError
   - **error**: explicit "behavior_mismatch" event on chain. The agent does NOT silently
     return "done"; the failure is recorded permanently

3. **Reconciliation** (seq 10): `git status` confirms only the claimed file
   is dirty — no surprise edits anywhere else

4. **Anchor** (seq 11): pretend `git push` to a public receipts branch; the
   commit hash becomes the external anchor

Run it:

```bash
cd ~/Documents/receipt
PYTHONPATH=. python3 examples/code_writing_agent.py
PYTHONPATH=. python3 -m receipt.cli verify /tmp/code_writing_demo.receipt.jsonl
```

To see the human view: open `viewer/dashboard.html` and upload
`/tmp/code_writing_demo.receipt.jsonl` (file picker), or point the URL field
at a server-hosted copy.

## Findings (writing this example surfaced 3 real spec issues)

Writing this example wasn't just an illustration — it surfaced concrete
limitations in spec v0.1 that a non-trading adopter immediately hits:

1. **`trust_tier` enum is trade-centric.** The allowed values are
   `{api_verified, backfill_local, exchange_delayed, exchange_realtime,
   manual}`. For a code-writing AI, the natural tier "filesystem hash
   confirmed locally" doesn't fit cleanly — we mapped it to `api_verified`
   on the grounds that a subprocess return value is a deterministic
   external check, but a v0.2 should add `local_deterministic` or similar.

2. **`match` schema has 7 required trade fields.** `symbol`, `side`,
   `size`, `price_match`, `time_match`, `id_match`, `tolerance_used`. We
   re-mapped `symbol → file path`, `side → edit direction`, `price_match →
   diff hash equality`, etc. — it works, but it's ugly. A v0.2 should make
   `match` polymorphic by `kind` (`trade-match` vs `file-edit-match` vs
   `url-fetch-match`).

3. **`reconciliation` requires `matched / unmatched / errors` counts.** For
   trading these are clean (matched orders vs unmatched orders). For a
   code-writing AI we used "tasks where claim files == git status files".
   The semantics carry over but the field names are misleading.

These are not blockers — the SDK works and a chain verifies — but they're a
shopping list for spec v0.2. The whole point of writing a non-trading
adopter is to surface this.

## What this example is **not**

- A real production code-writing agent. It simulates one task in-memory and
  fakes the subprocess output. A real adopter wires `r.claim()` /
  `r.external_action()` / etc. into actual Cursor / Claude Code / their own
  agent loop.
- A spec proposal. The findings above are observations, not a v0.2 RFC.

## Mission framing

Receipt v0.1's existence reason is "if an agent claims it acted, it should
produce a receipt." iBitLabs's sniper bot is the first adopter and proves
the spec on trade audit. This example proves the spec generalizes to AI
audit broadly — the same protocol can carry "this AI just wrote this
function and here's the hash + git status proof" — and surfaces what needs
to change for that generalization to feel native rather than retrofitted.
