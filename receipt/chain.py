"""Hash chain primitives — Receipt Spec v0.1 §14.

Canonical key order: (v, schema_version, ts, seq, agent, kind, data, prev_hash).
Canonicalization: RFC 8785 / JCS subset — sort_keys, no whitespace, UTF-8,
ensure_ascii=False.

Tampering with any byte anywhere in the chain breaks downstream hashes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

GENESIS_PREV_HASH = "sha256:" + "0" * 64
HASH_PREFIX = "sha256:"

# canonical key order — must match SPEC §14 exactly
CANONICAL_KEYS = ("v", "schema_version", "ts", "seq", "agent", "kind", "data", "prev_hash")


def _canonical(event: dict) -> str:
    """Serialize the hashable subset of an event in canonical form per SPEC §14."""
    body = {k: event[k] for k in CANONICAL_KEYS if k in event}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(event: dict) -> str:
    digest = hashlib.sha256(_canonical(event).encode("utf-8")).hexdigest()
    return HASH_PREFIX + digest


class ChainStatus(str, Enum):
    VERIFIED = "VERIFIED"
    INVALID_CHAIN = "INVALID_CHAIN"
    UNVERIFIED_CLAIM = "UNVERIFIED_CLAIM"
    SUSPECT_VERIFICATION = "SUSPECT_VERIFICATION"
    STALE_RECONCILIATION = "STALE_RECONCILIATION"
    BROKEN_ANCHOR = "BROKEN_ANCHOR"
    EMPTY = "EMPTY"


@dataclass
class VerifyReport:
    status: ChainStatus
    n_events: int
    n_claims: int
    n_verified: int
    n_errors: int
    n_rejected: int
    issues: list[str] = field(default_factory=list)


def verify_chain(events: Iterable[dict], hard_reconciliation_gap_ms: int = 6 * 3600 * 1000) -> VerifyReport:
    """Static portion of SPEC §15. Live exchange re-fetch handled by adapters."""
    events = list(events)
    if not events:
        return VerifyReport(ChainStatus.EMPTY, 0, 0, 0, 0, 0, [])

    expected_prev = GENESIS_PREV_HASH
    expected_seq = 0

    claim_seqs: set[int] = set()
    verified_for: set[int] = set()
    error_for: set[int] = set()
    rejected_for: set[int] = set()
    suspect_verified: set[int] = set()

    last_recon_ts: int | None = None
    first_claim_ts: int | None = None
    last_event_ts: int | None = None
    issues: list[str] = []

    for ev in events:
        # seq monotonic
        if ev.get("seq") != expected_seq:
            issues.append(f"seq gap at expected={expected_seq}, got={ev.get('seq')}")
            return VerifyReport(ChainStatus.INVALID_CHAIN, len(events), 0, 0, 0, 0, issues)
        # prev_hash linkage
        if ev.get("prev_hash") != expected_prev:
            issues.append(
                f"prev_hash mismatch at seq={expected_seq}: "
                f"expected {expected_prev[:24]}..., got {str(ev.get('prev_hash'))[:24]}..."
            )
            return VerifyReport(ChainStatus.INVALID_CHAIN, len(events), 0, 0, 0, 0, issues)
        # hash recompute
        recomputed = compute_hash(ev)
        if ev.get("hash") != recomputed:
            issues.append(f"hash mismatch at seq={expected_seq}")
            return VerifyReport(ChainStatus.INVALID_CHAIN, len(events), 0, 0, 0, 0, issues)

        kind = ev.get("kind")
        data = ev.get("data") or {}
        last_event_ts = ev["ts"]

        if kind == "claim":
            claim_seqs.add(ev["seq"])
            if first_claim_ts is None:
                first_claim_ts = ev["ts"]
        elif kind == "verified":
            cs = data.get("claim_seq")
            if cs is not None:
                verified_for.add(cs)
                # match-completeness check (§7)
                m = data.get("match") or {}
                if not m.get("id_match", False):
                    suspect_verified.add(cs)
        elif kind == "error":
            cs = data.get("claim_seq")
            if cs is not None:
                error_for.add(cs)
        elif kind == "signal_rejected":
            rejected_for.add(ev["seq"])
        elif kind == "reconciliation":
            last_recon_ts = ev["ts"]

        expected_prev = ev["hash"]
        expected_seq += 1

    n_claims = len(claim_seqs)
    n_verified = len(claim_seqs & verified_for)
    n_errors = len(error_for)
    n_rejected = len(rejected_for)

    # SUSPECT_VERIFICATION takes precedence over UNVERIFIED_CLAIM if any verified lacks id_match
    if suspect_verified:
        issues.append(
            f"{len(suspect_verified)} verified event(s) with id_match=false: "
            f"{sorted(suspect_verified)[:5]}"
        )
        return VerifyReport(
            ChainStatus.SUSPECT_VERIFICATION, len(events), n_claims, n_verified,
            n_errors, n_rejected, issues
        )

    # every claim must terminate (verified | error | signal_rejected) — §2 hard rule
    terminated = verified_for | error_for
    unmatched = claim_seqs - terminated
    if unmatched:
        issues.append(
            f"{len(unmatched)} claim(s) without verified/error termination: "
            f"{sorted(unmatched)[:5]}"
        )
        return VerifyReport(
            ChainStatus.UNVERIFIED_CLAIM, len(events), n_claims, n_verified,
            n_errors, n_rejected, issues
        )

    # reconciliation cadence — §9
    if claim_seqs:
        if last_recon_ts is None:
            issues.append("no reconciliation event in chain")
            return VerifyReport(
                ChainStatus.STALE_RECONCILIATION, len(events), n_claims, n_verified,
                n_errors, n_rejected, issues
            )
        if last_event_ts is not None and (last_event_ts - last_recon_ts) > hard_reconciliation_gap_ms:
            issues.append(
                f"reconciliation gap {(last_event_ts - last_recon_ts)/1000/3600:.1f}h "
                f"exceeds 6h limit"
            )
            return VerifyReport(
                ChainStatus.STALE_RECONCILIATION, len(events), n_claims, n_verified,
                n_errors, n_rejected, issues
            )

    return VerifyReport(
        ChainStatus.VERIFIED, len(events), n_claims, n_verified, n_errors, n_rejected, []
    )
