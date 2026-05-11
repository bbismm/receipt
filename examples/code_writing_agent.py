"""Receipt v0.1 worked example — a code-writing AI auditing itself.

The pain this addresses: a coding agent (Cursor, Claude Code, an AutoGPT-style
loop) finishes and says "done — I added handleX() to Y.ts". The human has no
way to know:
  - Did it actually do what it claimed?
  - Are there other files it touched silently?
  - Did the build / tests pass after the edit?
  - Could the agent be making any of this up?

This example demonstrates how the same protocol used for trading-bot audit
maps cleanly onto a code-writing AI. The four-phase lifecycle is identical:

    claim ──▶ external_action ──▶ verified ──▶ reconciliation

What changes is the *content* of the events, not the structure:

  trading              code-writing
  ─────────            ──────────────────────
  symbol               file path
  size, price          pre-edit file hash, post-edit file hash
  Coinbase API         git diff / tsc / pytest
  exchange position    git status, file system state
  USD balance          number of files modified (vs. claim)

Run it:

    PYTHONPATH=. python3 examples/code_writing_agent.py
    PYTHONPATH=. python3 -m receipt.cli verify /tmp/code_writing_demo.receipt.jsonl
    PYTHONPATH=. python3 -m receipt.cli show /tmp/code_writing_demo.receipt.jsonl

Then open the JSONL in the dashboard at viewer/dashboard.html (or upload via
the file picker) to see the "human-readable" rendering.
"""
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt import Receipt

OUT = "/tmp/code_writing_demo.receipt.jsonl"
Path(OUT).unlink(missing_ok=True)

# Treat the AI as an agent with its own name. The "version" is whatever you
# bump when the agent's behavior changes — not the underlying LLM version.
r = Receipt(agent="example/code-writing-agent-v0.1", out_path=OUT)


def sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


# ── boot ──────────────────────────────────────────────────────────────────
r.heartbeat(status="ready", latency_ms=42)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — happy path: agent says "add a score() function", does it cleanly.
# ═══════════════════════════════════════════════════════════════════════════

# Phase 1: claim. The agent declares intent BEFORE touching anything.
# Note the user-prompt hash: this anchors what the human originally asked
# for, so the agent can't quietly drift later.
task_1 = r.claim(
    action="edit_file",
    repo="example/scoring-lib",
    target_file="src/scoring.py",
    user_prompt_hash=sha256("Please add a score() function that takes a list of scores and returns the average."),
    intent="add function `score(values: list[float]) -> float` that returns sum(values)/len(values)",
    expected_change={
        "files_modified": ["src/scoring.py"],
        "lines_added_approx": 5,
        "lines_removed": 0,
    },
    ai={
        "model": "claude-opus-4-7",
        "provider": "anthropic",
        "decision_mode": "llm",
        "agent_version": "code-writing-agent-v0.1",
    },
    reasoning=[
        "user prompt is unambiguous: function signature and behavior are both specified",
        "no existing score() in scoring.py per pre-edit grep",
        "naive arithmetic mean meets the literal request",
    ],
)

# Phase 2: external_action — the agent records what it actually did to the
# outside world. The file-system *is* the outside world for a code-writing
# AI. We record pre-edit hash so we have a fingerprint to reconcile against.
pre_edit_text = "def already_exists():\n    return 1\n"
post_edit_text = (
    "def already_exists():\n"
    "    return 1\n"
    "\n"
    "def score(values: list[float]) -> float:\n"
    "    return sum(values) / len(values)\n"
)

r.external_action(
    task_1,
    venue="filesystem",
    request={
        "operation": "write_file",
        "path": "src/scoring.py",
        "pre_edit_hash": sha256(pre_edit_text),
        "diff_hash": sha256("+def score(values: list[float]) -> float:\n+    return sum(values) / len(values)\n"),
    },
    response={
        "status": "written",
        "post_edit_hash": sha256(post_edit_text),
        "bytes_written": len(post_edit_text),
    },
)

# A second external_action: agent runs a syntax check. This is the
# code-writing equivalent of "did the order actually fill on the exchange".
r.external_action(
    task_1,
    venue="subprocess",
    request={
        "operation": "syntax_check",
        "command": "python3 -m py_compile src/scoring.py",
    },
    response={
        "status": 0,
        "stdout": "",
        "stderr": "",
    },
)

# Phase 3: verified — the agent compares "what I claimed" to "what really
# exists on disk now". Match fields are how downstream verifiers know what
# the agent considered to be a successful match.
#
# NOTE: Receipt Spec v0.1 currently requires 7 trade-centric fields inside
# `match`: symbol, side, size, price_match, time_match, id_match,
# tolerance_used. For a code-writing AI we re-map them:
#   symbol      → file path under audit
#   side        → direction-of-change ("edit_file")
#   size        → "did the byte size change as expected"
#   price_match → "did the diff hash match the planned diff"
#   id_match    → "did the post-edit file hash match what we recorded"
# This works but is ugly. The finding goes in the README: spec v0.2 should
# make `match` kind-aware (trade-match vs file-edit-match vs url-fetch-match),
# not require trade fields universally.
r.verified(
    task_1,
    trust_tier="api_verified",  # subprocess (py_compile) IS a deterministic external check
    source="local_filesystem",
    source_endpoint="stat:src/scoring.py",
    match={
        "symbol": "src/scoring.py",
        "side": "edit_file",
        "size": True,
        "price_match": True,
        "time_match": True,
        "id_match": True,
        "tolerance_used": "exact_hash",
        # extra fields (informative, not spec-required):
        "lines_added": 3,
        "expected_files": ["src/scoring.py"],
        "actual_files": ["src/scoring.py"],
    },
    summary="score() added; file hash matches; syntax check clean",
)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — failure path: agent says "extend score() with median support",
# but the edit causes a syntax error. The receipt records the failure
# *explicitly* — a fake "AI says done but it didn't work" is impossible
# under this discipline.
# ═══════════════════════════════════════════════════════════════════════════

task_2 = r.claim(
    action="edit_file",
    repo="example/scoring-lib",
    target_file="src/scoring.py",
    user_prompt_hash=sha256("Now extend score() so it can also return the median if passed median=True."),
    intent="add `median` kwarg to score(); when True, use statistics.median",
    expected_change={
        "files_modified": ["src/scoring.py"],
        "lines_added_approx": 4,
        "lines_removed": 1,
    },
    ai={
        "model": "claude-opus-4-7",
        "provider": "anthropic",
        "decision_mode": "llm",
        "agent_version": "code-writing-agent-v0.1",
    },
    reasoning=[
        "user wants opt-in median; default behavior must remain mean",
        "statistics.median in stdlib — no new dependency",
    ],
)

# The agent wrote the file, but forgot to import statistics. Syntax check
# passes (it's a runtime error, not a syntax error) but pytest catches it.
r.external_action(
    task_2,
    venue="filesystem",
    request={
        "operation": "write_file",
        "path": "src/scoring.py",
        "pre_edit_hash": sha256(post_edit_text),  # carry forward from task 1
    },
    response={"status": "written", "post_edit_hash": "sha256:abc...newhash"},
)

r.external_action(
    task_2,
    venue="subprocess",
    request={
        "operation": "test_run",
        "command": "pytest tests/test_scoring.py -q",
    },
    response={
        "status": 1,
        "stderr": "NameError: name 'statistics' is not defined",
        "stdout_tail": "FAILED tests/test_scoring.py::test_score_median",
    },
)

# Phase = "verify" because that's where the failure surfaced — we got the
# file written, but couldn't verify the claim was actually met (the function
# crashes at runtime). The agent records the failure on-chain rather than
# silently returning "done".
r.error(
    claim_seq=task_2,
    phase="verified",  # spec phase enum: {external_action, verified, reconciliation, other}
    error_type="behavior_mismatch",
    message="post-edit file imports incomplete: NameError at runtime in test suite",
    retryable=True,
    proposed_remedy="re-edit to add `import statistics` at top of file",
)


# ═══════════════════════════════════════════════════════════════════════════
# Reconciliation — every N minutes (or every N tasks), the agent compares
# its local belief to ground truth. For trading, that's "exchange balance
# = my book". For a code-writing AI, it's "git status matches the union of
# files I claimed to touch — nothing more, nothing less".
# ═══════════════════════════════════════════════════════════════════════════

# Reconciliation requires period, trust_tier, matched, unmatched, errors per
# spec §7. For code-AI: matched = tasks where claim files == git status files,
# unmatched = tasks where surprise files showed up, errors = tasks where
# verification flagged drift.
r.reconciliation(
    period="per_task",
    trust_tier="api_verified",
    matched=1,    # task_1: claimed files == git status output
    unmatched=0,  # no surprise file edits
    errors=1,     # task_2 failed pytest (already logged via r.error above)
    local={
        "files_claimed_modified": ["src/scoring.py"],
        "tasks_completed": 1,
        "tasks_failed": 1,
    },
    external={
        # `git status --porcelain` output: only the one file is dirty
        "files_actually_modified": ["src/scoring.py"],
        # `git diff --stat` line count
        "lines_added_actual": 5,
        "lines_removed_actual": 0,
    },
    summary="no surprise file edits — git status matches claim union",
)


# ═══════════════════════════════════════════════════════════════════════════
# Anchor (optional but recommended). For trading, anchor goes to IPFS /
# bitcoin OP_RETURN every 24h. For a code-writing AI, the natural anchor
# is `git push` — once your receipt chain is pushed to a public branch,
# you can't rewrite it without leaving a trace. This is a dry-run anchor
# (anchor_kind="local" tells verifiers not to trust this for external
# integrity); a real adopter would push to ipfs / a git remote.
# ═══════════════════════════════════════════════════════════════════════════

# Hash the chain so far — in production this would be Merkle root, here we
# just use a placeholder.
# For a code-writing AI, the most natural anchor is `github_commit` — push
# the receipt JSONL to a public branch, and the commit hash becomes the
# anchor (rewriting history without leaving a trace is hard, especially if
# the branch is also mirrored to IPFS). This demo uses a fake commit hash.
r.anchor(
    merkle_root="sha256:" + "f" * 64,
    anchor_uri="https://github.com/bbismm/receipt/commit/abc123def456789012345678901234567890abcd",
    anchor_kind="github_commit",
    note="demo anchor with fake commit; real adopters push to a public receipts branch",
)

r.heartbeat(status="idle", latency_ms=8)

print(f"\n✓ wrote {r.seq} events to {OUT}")
print(f"  agent: {r.agent}")
print(f"  head hash: {r.head_hash[:16]}...")
print(f"\nVerify:")
print(f"  PYTHONPATH=. python3 -m receipt.cli verify {OUT}")
print(f"  PYTHONPATH=. python3 -m receipt.cli show {OUT}")
print(f"\nOr drop the JSONL into viewer/dashboard.html to see the human view.")
