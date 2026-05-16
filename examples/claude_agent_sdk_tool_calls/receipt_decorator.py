"""Drop-in Receipt decorator for AI-agent tool calls.

The pain this addresses: an AI agent (Claude Agent SDK, OpenAI Assistants,
LangChain, bare anthropic/openai SDK) calls a tool. Later, a user / auditor
asks "did the agent actually call that tool, with those args, and get
that result?" Without a receipt chain, the answer is "trust me bro." With
this decorator, the answer is a hash-chained JSONL file + Merkle anchor.

Usage (Claude Agent SDK or any function-as-tool framework):

    from receipt import Receipt
    from receipt_decorator import receipt_tool

    r = Receipt(agent="my-agent/v1", out_path="my_agent.receipt.jsonl")

    @receipt_tool(r)
    def fetch_url(url: str) -> str:
        return urllib.request.urlopen(url).read().decode()

    @receipt_tool(r, hash_inputs=True)   # privacy: log only input HASH, not args
    def search_user_data(query: str) -> list[dict]:
        return db.search(query)

Each call writes 3 events to the chain:
    1. claim          — "about to call <tool> with <args or arg_hash>"
    2. external_action — actually performed the call (start ts, duration_ms)
    3. verified | error — output (or failure) recorded against the claim

Failure mode: receipt I/O failure NEVER blocks the underlying tool call.
Same discipline as the sniper bot integration (SPEC §"observability, not
control"). A broken chain shows up at verification time; it does not
break the agent.

This decorator is framework-agnostic. It works with any callable. The name
"claude_agent_sdk" in the directory is the canonical target audience —
this is the receipt-instrumentation pattern we recommend for tool calls
in the Claude Agent SDK — but the code is plain stdlib + the `receipt`
package and works with anthropic SDK, OpenAI SDK, LangChain, CrewAI, or
hand-rolled agent loops.
"""
from __future__ import annotations

import functools
import hashlib
import json
import time
import traceback
from typing import Any, Callable

from receipt import Receipt


def _sha256(payload: Any) -> str:
    """Canonical hash of any JSON-serializable payload. Used to fingerprint
    inputs / outputs without recording them verbatim (privacy, log-size)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_dump(obj: Any, *, max_len: int = 2048) -> Any:
    """Best-effort serialization to something json.dumps will accept.
    Truncates long strings to keep the chain readable. NEVER raises."""
    try:
        json.dumps(obj, default=str)
        if isinstance(obj, str) and len(obj) > max_len:
            return obj[:max_len] + f"...<truncated, {len(obj)} bytes>"
        return obj
    except Exception:
        return f"<unserializable: {type(obj).__name__}>"


def receipt_tool(
    receipt: Receipt,
    *,
    name: str | None = None,
    hash_inputs: bool = False,
    hash_outputs: bool = False,
    max_payload_bytes: int = 2048,
) -> Callable[[Callable], Callable]:
    """Wrap a function so each call emits a 3-event Receipt trace.

    Args:
        receipt: The Receipt instance to write to.
        name: Tool name in the chain (defaults to fn.__name__).
        hash_inputs: If True, record only sha256 of args, not args themselves.
                     Use for PII, credentials, large payloads.
        hash_outputs: Same, for return value.
        max_payload_bytes: Truncate long string payloads in the chain.

    Returns:
        A decorator. Wrapped function behaves identically to the original
        (same return value, same exceptions raised), with the side effect
        of writing 3 receipt events per call. If receipt writes fail, the
        tool call still completes and returns normally.
    """
    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            # Phase 1: claim. Declare intent BEFORE the call.
            request_payload = {"args": [_safe_dump(a, max_len=max_payload_bytes) for a in args],
                               "kwargs": {k: _safe_dump(v, max_len=max_payload_bytes) for k, v in kwargs.items()}}
            request_fingerprint = _sha256(request_payload)
            claim_data: dict = {
                "action": "tool_call",
                "tool": tool_name,
                "request_hash": request_fingerprint,
            }
            if not hash_inputs:
                claim_data["request"] = request_payload

            claim_seq: int | None = None
            try:
                claim_seq = receipt.claim(**claim_data)
            except Exception:
                # Receipt I/O must never block the tool call.
                pass

            # Phase 2: external_action. Record that the call is in flight.
            t0 = time.perf_counter()
            try:
                if claim_seq is not None:
                    receipt.external_action(
                        claim_seq,
                        venue="tool",
                        request={"operation": "invoke", "tool": tool_name,
                                 "request_hash": request_fingerprint},
                        response={"status": "started"},
                    )
            except Exception:
                pass

            # The actual tool call. This is the only line whose semantics
            # matter for correctness; everything else is observation.
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                # Phase 3a: error. Capture and re-raise; receipt is the side
                # observer, not the control flow.
                try:
                    if claim_seq is not None:
                        receipt.error(
                            claim_seq=claim_seq,
                            phase="external_action",
                            error_type=type(exc).__name__,
                            message=str(exc)[:max_payload_bytes],
                            retryable=False,
                            duration_ms=duration_ms,
                            traceback_tail=traceback.format_exc()[-max_payload_bytes:],
                        )
                except Exception:
                    pass
                raise

            duration_ms = int((time.perf_counter() - t0) * 1000)

            # Phase 3b: verified. Record the result against the claim.
            # Note: receipt SPEC v0.1's `verified` was designed for trading
            # match fields (symbol/side/size/...). We re-map for tool calls:
            #   symbol → tool name
            #   side   → "tool_call"
            #   *_match → request/response fingerprint comparison
            # This mapping is documented as a known-ugly in
            # examples/code_writing_agent.py. Spec v0.2 plans kind-aware
            # match fields.
            response_payload = _safe_dump(result, max_len=max_payload_bytes)
            response_fingerprint = _sha256(response_payload)
            verified_data: dict = {
                "tool": tool_name,
                "duration_ms": duration_ms,
                "response_hash": response_fingerprint,
            }
            if not hash_outputs:
                verified_data["response"] = response_payload

            try:
                if claim_seq is not None:
                    receipt.verified(
                        claim_seq,
                        trust_tier="api_verified",
                        source="local_subprocess",
                        source_endpoint=f"tool:{tool_name}",
                        match={
                            "symbol": tool_name,
                            "side": "tool_call",
                            "size": True,
                            "price_match": True,
                            "time_match": True,
                            "id_match": True,
                            "tolerance_used": "exact_hash",
                            "request_fingerprint": request_fingerprint,
                            "response_fingerprint": response_fingerprint,
                        },
                        **verified_data,
                    )
            except Exception:
                pass

            return result

        # Expose the underlying function for testing / wrapping disable.
        wrapped.__receipt_inner__ = fn  # type: ignore[attr-defined]
        wrapped.__receipt_tool_name__ = tool_name  # type: ignore[attr-defined]
        return wrapped

    return decorator
