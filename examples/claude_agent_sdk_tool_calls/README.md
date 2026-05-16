# Receipt for AI-agent tool calls

A drop-in decorator that gives any tool-calling AI agent a verifiable
audit chain — three lines of setup, one decorator per tool.

## The problem

Your agent (Claude Agent SDK, OpenAI Assistants, LangChain, hand-rolled
loop) calls a tool. A user, employer, regulator, or future-you asks:

> Did the agent actually call that tool, with those arguments, and get
> that result back? Or is it making it up?

Without a chain, the answer is "trust me." With this decorator, the
answer is a JSONL file where every call is hash-chained and the file
itself can be anchored to IPFS / a public git push / a Bitcoin OP_RETURN.

## What the decorator does

Each wrapped call emits three events to the receipt chain:

1. **claim** — "about to call `<tool>` with `<args or arg_hash>`"
2. **external_action** — call is in flight; start timestamp
3. **verified** — output recorded against the claim; OR
   **error** — exception recorded against the claim; exception re-raised

The chain is append-only and signed (each event includes a sha256 hash of
the prior event). Tampering shows up at verification time.

## Usage

```python
from receipt import Receipt
from receipt_decorator import receipt_tool

r = Receipt(agent="my-agent/v1", out_path="my_agent.receipt.jsonl")

@receipt_tool(r)
def fetch_url(url: str) -> str:
    return urllib.request.urlopen(url).read().decode()

@receipt_tool(r, hash_inputs=True)   # PII-safe: only hash of arg in chain
def search_user_records(query: str) -> list[dict]:
    return db.search(query)
```

That's it. No changes to how the agent calls the tool. No changes to
the tool's signature. No changes to error handling.

## Try it

```bash
cd examples/claude_agent_sdk_tool_calls
PYTHONPATH=../.. python3 demo_agent.py
PYTHONPATH=../.. python3 -m receipt.cli verify /tmp/agent_demo.receipt.jsonl
PYTHONPATH=../.. python3 -m receipt.cli show /tmp/agent_demo.receipt.jsonl
```

Open `../../viewer/dashboard.html` and drop the JSONL in to see a
human-readable view.

## Failure-mode discipline

The decorator inherits the same rule as the iBitLabs sniper bot
integration: **receipt I/O failure NEVER blocks the underlying tool
call.** Every receipt write is wrapped in `try/except: pass`. If the
chain becomes unwritable (disk full, permission revoked), the agent
continues; the gap shows up at chain verification time, not as a
production outage.

## Privacy options

| Option | Effect |
|---|---|
| `hash_inputs=True` | Args are sha256'd; raw args never written. |
| `hash_outputs=True` | Return value is sha256'd; raw value never written. |
| `max_payload_bytes=N` | Strings > N bytes are truncated with size marker. |

The hash is canonical (sorted keys, no whitespace), so the same input
always produces the same hash — a later auditor can verify "the query
that produced X" without learning the query.

## Framework notes

The directory is named for Claude Agent SDK because that is the canonical
target audience — but the code is pure stdlib + the `receipt` package and
works with:

- Anthropic SDK (`anthropic.Anthropic.messages.create` with `tools=`)
- OpenAI Assistants API
- LangChain / LangGraph tool nodes
- CrewAI agents
- AutoGen
- Any function-as-tool framework

If you want a framework-specific adapter (e.g. auto-wrapping every tool
on an Assistant or Agent object), file an issue at
[github.com/bbismm/receipt](https://github.com/bbismm/receipt).

## Spec mapping (known-ugly)

Receipt SPEC v0.1's `verified` event was designed for trading match
fields: `symbol`, `side`, `size`, `price_match`, `time_match`, `id_match`,
`tolerance_used`. For tool calls we re-map:

| Trading field | Tool-call meaning |
|---|---|
| symbol | tool name |
| side | `"tool_call"` |
| price_match / id_match / etc. | always `True` (verified by hash match) |
| tolerance_used | `"exact_hash"` |

The trading-flavored field names show up in the chain. SPEC v0.2 plans
kind-aware match fields so `verified` for a tool call records
`request_fingerprint`, `response_fingerprint`, `duration_ms` natively
rather than re-purposing trade fields. Until then, the workaround is
documented in `examples/code_writing_agent.py` and here.

## License

MIT (code) + CC-BY-4.0 (spec & docs). Same as the rest of the receipt
repo.
