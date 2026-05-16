"""Runnable demo: an agent with three tools, instrumented with @receipt_tool.

What this shows:
    - Drop-in instrumentation: 3 lines of setup, 1 decorator per tool.
    - Privacy: one tool hashes its input (simulating PII).
    - Failure capture: one tool raises; receipt records the error and the
      exception still propagates (control flow unchanged).
    - Verifiable chain output: the JSONL file is signed-chained the same
      way the live sniper bot's chain is, so the same verifier
      (`python -m receipt.cli verify`) works.

Run:
    PYTHONPATH=../.. python3 demo_agent.py
    PYTHONPATH=../.. python3 -m receipt.cli verify /tmp/agent_demo.receipt.jsonl
    PYTHONPATH=../.. python3 -m receipt.cli show /tmp/agent_demo.receipt.jsonl
"""
import sys
import time
import urllib.request
from pathlib import Path

# Make `receipt` importable when running from this directory without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from receipt import Receipt  # noqa: E402

from receipt_decorator import receipt_tool  # noqa: E402

OUT = "/tmp/agent_demo.receipt.jsonl"
Path(OUT).unlink(missing_ok=True)

r = Receipt(agent="example/demo-agent-v0.1", out_path=OUT)

# Boot event — useful for distinguishing process restarts in the chain.
r.heartbeat(status="ready", latency_ms=0)


# ── tools the agent can call ──────────────────────────────────────────────

@receipt_tool(r)
def list_files(directory: str) -> list[str]:
    """Plain tool. Args + return value recorded in the clear."""
    return sorted(p.name for p in Path(directory).iterdir())


@receipt_tool(r, hash_inputs=True, hash_outputs=True)
def search_user_records(query: str) -> list[dict]:
    """PII-safe tool. The chain records that this was called and what hash
    of input → hash of output it produced, but neither the query nor the
    records are written verbatim. A later auditor can verify that THE SAME
    query produced THE SAME records (chain integrity) without learning the
    contents."""
    # Pretend this hits a database.
    return [{"id": 1, "name": "redacted"}, {"id": 2, "name": "redacted"}]


@receipt_tool(r, name="fetch_url_strict")
def fetch_url(url: str) -> str:
    """Tool that can fail. The decorator catches the exception, writes an
    `error` event, then re-raises so the agent loop sees the failure."""
    with urllib.request.urlopen(url, timeout=2) as resp:
        return resp.read().decode("utf-8")


# ── simulated agent loop ──────────────────────────────────────────────────

print("agent step 1: list ~/Documents")
files = list_files(str(Path.home() / "Documents"))
print(f"  → got {len(files)} entries\n")

print("agent step 2: search user records (privacy-hashed)")
records = search_user_records("name LIKE 'A%'")
print(f"  → got {len(records)} records (content not in chain)\n")

print("agent step 3: fetch a known-bad URL (should error)")
try:
    fetch_url("http://127.0.0.1:1/should-not-resolve")
except Exception as e:
    print(f"  → caught {type(e).__name__}: {e}")
    print("  → error WAS recorded in the chain; control flow unchanged\n")

# Periodic reconciliation. For trading bots this compares local position
# vs. exchange. For a tool-calling agent, the natural reconciliation is
# "of the N tool calls I claimed I'd make this session, did each one
# resolve to either a verified result or a recorded error?" — i.e. no
# silent drops. SPEC §7 makes this event required for a clean verify.
r.reconciliation(
    period="per_session",
    trust_tier="api_verified",
    matched=2,    # list_files + search_user_records both reached `verified`
    unmatched=0,  # no claim went un-acknowledged
    errors=1,     # fetch_url_strict raised; recorded via `error` event
    local={"claims_emitted": 3, "verified_emitted": 2, "errors_emitted": 1},
    external={"tool_calls_completed": 3},
    summary="all 3 tool calls accounted for; 1 errored as expected",
)

# Boot-end event.
r.heartbeat(status="idle", latency_ms=0)

# Show the operator a summary they can verify.
print(f"\nWrote {r.seq} events to {OUT}")
print(f"Head hash: {r.head_hash[:24]}...\n")
print("Verify the chain:")
print(f"  PYTHONPATH=../.. python3 -m receipt.cli verify {OUT}")
print(f"  PYTHONPATH=../.. python3 -m receipt.cli show {OUT}\n")
print("Or drop the JSONL into ../../viewer/dashboard.html to see the human view.")
