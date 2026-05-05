"""Hash chain primitives — Receipt Spec v0.1 §2.

The chain is append-only: each event's `hash` is sha256 of the canonical JSON
of `{v, ts, seq, agent, kind, data, prev_hash}`, in that exact key order, with
no whitespace. Tampering with any field anywhere in the chain breaks
downstream hashes.

GENESIS prev_hash = "sha256:" + 64 zeros.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

GENESIS_PREV_HASH = "sha256:" + "0" * 64
HASH_PREFIX = "sha256:"

# canonical key order — must match SPEC.md §2 exactly
CANONICAL_KEYS = ("v", "ts", "seq", "agent", "kind", "data", "prev_hash")


def _canonical(event: dict) -> str:
    """Serialize the hashable subset of an event in canonical form.

    Only the keys in CANONICAL_KEYS are hashed. Any extra fields (incl. `hash`
    itself) are excluded. `data` is recursively sorted so nested dicts
    serialize deterministically.
    """
    body = {k: event[k] for k in CANONICAL_KEYS if k in event}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(event: dict) -> str:
    """Return the canonical hash for an event (without mutating it)."""
    digest = hashlib.sha256(_canonical(event).encode("utf-8")).hexdigest()
    return HASH_PREFIX + digest


class ChainStatus(str, Enum):
    VERIFIED = "VERIFIED"
    INVALID_CHAIN = "INVALID_CHAIN"
    UNVERIFIED_CLAIM = "UNVERIFIED_CLAIM"
    STALE_RECONCILIATION = "STALE_RECONCILIATION"
    BROKEN_ANCHOR = "BROKEN_ANCHOR"
    EMPTY = "EMPTY"


@dataclass
class VerifyReport:
    status: ChainStatus
    n_events: int
    n_claims: int
    n_verified: int
    issues: list[str]


def verify_chain(events: Iterable[dict]) -> VerifyReport:
    """Walk the chain, recompute hashes, check linkage and required pairings.

    Per SPEC.md §4, this is the static portion (no live exchange re-fetch).
    Live reconciliation is handled by adapters in receipt.adapters.*
    """
    events = list(events)
    issues: list[str] = []

    if not events:
        return VerifyReport(ChainStatus.EMPTY, 0, 0, 0, [])

    expected_prev = GENESIS_PREV_HASH
    expected_seq = 0
    claim_seqs: set[int] = set()
    verified_for: set[int] = set()
    last_reconciliation_ts: str | None = None

    for ev in events:
        # seq must be monotonic
        if ev.get("seq") != expected_seq:
            issues.append(f"seq gap at expected={expected_seq}, got={ev.get('seq')}")
            return VerifyReport(ChainStatus.INVALID_CHAIN, len(events), 0, 0, issues)

        # prev_hash must link
        if ev.get("prev_hash") != expected_prev:
            issues.append(
                f"prev_hash mismatch at seq={expected_seq}: "
                f"expected {expected_prev[:24]}..., got {str(ev.get('prev_hash'))[:24]}..."
            )
            return VerifyReport(ChainStatus.INVALID_CHAIN, len(events), 0, 0, issues)

        # hash must recompute
        recomputed = compute_hash(ev)
        if ev.get("hash") != recomputed:
            issues.append(f"hash mismatch at seq={expected_seq}")
            return VerifyReport(ChainStatus.INVALID_CHAIN, len(events), 0, 0, issues)

        # bookkeeping for pairing checks
        kind = ev.get("kind")
        if kind == "claim":
            claim_seqs.add(ev["seq"])
        elif kind == "verified":
            cs = (ev.get("data") or {}).get("claim_seq")
            if cs is not None:
                verified_for.add(cs)
        elif kind == "reconciliation":
            last_reconciliation_ts = ev["ts"]

        expected_prev = ev["hash"]
        expected_seq += 1

    unmatched = claim_seqs - verified_for
    n_verified = len(claim_seqs & verified_for)

    if unmatched:
        issues.append(
            f"{len(unmatched)} claim(s) without matching verified event: "
            f"{sorted(unmatched)[:5]}{'...' if len(unmatched) > 5 else ''}"
        )
        return VerifyReport(
            ChainStatus.UNVERIFIED_CLAIM, len(events), len(claim_seqs), n_verified, issues
        )

    if claim_seqs and last_reconciliation_ts is None:
        issues.append("no reconciliation event in chain")
        return VerifyReport(
            ChainStatus.STALE_RECONCILIATION, len(events), len(claim_seqs), n_verified, issues
        )

    return VerifyReport(
        ChainStatus.VERIFIED, len(events), len(claim_seqs), n_verified, []
    )
