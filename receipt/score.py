"""Verifier v0.1 — verdict + trust score.

Implements docs/trust_score.md v0.1 EXACTLY. Server-side and client-side
(viewer/index.html) MUST produce identical numbers from the same input.

Public API:
    compute_score(events, report) -> dict matching docs/trust_score.md §3
"""
from __future__ import annotations

from typing import Iterable

from receipt.chain import ChainStatus, VerifyReport, verify_chain

PERMANENT_ANCHOR_KINDS = frozenset({"ipfs", "arweave", "ethereum", "btc_op_return"})
HEARTBEAT_BUCKET_MS = 5 * 60 * 1000  # 5 min


def _coverage(events: list[dict]) -> tuple[float, str, dict]:
    claims = {e["seq"] for e in events if e["kind"] == "claim"}
    if not claims:
        return 100.0, "no claims in window", {}
    terminated: set[int] = set()
    for e in events:
        cs = (e.get("data") or {}).get("claim_seq")
        if e["kind"] in ("verified", "error") and cs is not None:
            terminated.add(cs)
        elif e["kind"] == "signal_rejected":
            terminated.add(e["seq"])
    covered = len(claims & (terminated | {e["seq"] for e in events if e["kind"] == "signal_rejected"}))
    coverage = covered / len(claims)
    score = round(coverage * 100, 1)
    detail = f"{covered}/{len(claims)} claims have receipts"
    return score, detail, {"raw": coverage, "covered": covered, "total": len(claims)}


def _accuracy(events: list[dict]) -> tuple[float, str, dict]:
    verifs = [e for e in events if e["kind"] == "verified"]
    if not verifs:
        return 100.0, "no verified events to grade", {}
    receipts = len(verifs)
    verified_ok = sum(1 for e in verifs if (e["data"].get("match") or {}).get("id_match"))
    mismatches = receipts - verified_ok
    accuracy_raw = verified_ok / receipts
    mismatch_rate = mismatches / receipts
    score = max(0.0, (accuracy_raw - mismatch_rate * 2) * 100)  # 2× penalty per SPEC
    score = round(min(score, 100), 1)
    detail = f"{verified_ok}/{receipts} verified, {mismatches} mismatch{'es' if mismatches != 1 else ''}"
    return score, detail, {
        "verified_ok": verified_ok, "receipts": receipts,
        "mismatches": mismatches, "mismatch_rate": mismatch_rate,
    }


def _consistency(events: list[dict]) -> tuple[float, str, dict]:
    if len(events) < 2:
        return 100.0, "single event window", {}
    first_ts = events[0]["ts"]
    last_ts = events[-1]["ts"]
    span_ms = max(last_ts - first_ts, HEARTBEAT_BUCKET_MS)
    total_buckets = max(1, span_ms // HEARTBEAT_BUCKET_MS)
    covered = {((e["ts"] - first_ts) // HEARTBEAT_BUCKET_MS) for e in events}
    covered_n = len([b for b in covered if 0 <= b < total_buckets])
    ratio = covered_n / total_buckets
    score = round(min(ratio * 100, 100), 1)
    detail = f"covered {covered_n}/{total_buckets} 5-min buckets in window"
    return score, detail, {"covered": covered_n, "total": total_buckets, "ratio": ratio}


def _transparency(events: list[dict]) -> tuple[float, str, dict]:
    score = 100
    deductions: list[str] = []
    has_error = any(e["kind"] == "error" for e in events)
    has_recon = any(e["kind"] == "reconciliation" for e in events)
    has_tol = all(
        ((e["data"].get("match") or {}).get("tolerance_used")) is not None
        for e in events if e["kind"] == "verified"
    )
    if not has_error:
        score -= 20
        deductions.append("−20 no error events (real systems fail)")
    if not has_recon:
        score -= 30
        deductions.append("−30 no reconciliation event")
    if not has_tol:
        score -= 20
        deductions.append("−20 verified events missing match.tolerance_used")
    score = max(0, score)
    detail = "; ".join(deductions) if deductions else "all transparency criteria met"
    return float(score), detail, {"deductions": deductions}


def _integrity(events: list[dict], report: VerifyReport) -> tuple[float, str, dict]:
    if report.status == ChainStatus.INVALID_CHAIN:
        return 0.0, "hash chain broken", {"chain_status": report.status.value}
    anchors = [e for e in events if e["kind"] == "anchor"]
    if not anchors:
        return 50.0, "no anchor event present", {"chain_status": report.status.value}
    last_anchor = anchors[-1]
    is_permanent = last_anchor["data"].get("anchor_kind") in PERMANENT_ANCHOR_KINDS
    last_event_ts = events[-1]["ts"]
    age_h = (last_event_ts - last_anchor["ts"]) / 3_600_000
    score = 100
    notes: list[str] = []
    if not is_permanent:
        notes.append("social-only anchor (−10)")
        score -= 10
    if age_h > 24:
        score -= 30
        notes.append(f"anchor age {age_h:.1f}h (−30)")
    if age_h > 24 * 7:
        score = min(score, 30)
        notes.append("anchor > 7 days (cap 30)")
    score = max(0, min(score, 100))
    detail = (f"anchor {age_h:.1f}h ago on {last_anchor['data'].get('anchor_kind')}"
              + (f"; {'; '.join(notes)}" if notes else ""))
    return float(score), detail, {"age_hours": age_h, "is_permanent": is_permanent}


def _flags(events: list[dict], dims: dict, accuracy_meta: dict) -> list[str]:
    flags: list[str] = []
    if accuracy_meta.get("mismatches", 0) > 0:
        flags.append(f"{accuracy_meta['mismatches']}_mismatches")
    error_count = sum(1 for e in events if e["kind"] == "error")
    if error_count:
        flags.append(f"{error_count}_error_events")
    if dims["Consistency"]["score"] < 60:
        flags.append("missing_heartbeat_periods")
    anchors = [e for e in events if e["kind"] == "anchor"]
    if not anchors:
        flags.append("no_anchor")
    elif anchors:
        age_h = (events[-1]["ts"] - anchors[-1]["ts"]) / 3_600_000
        if age_h > 24:
            flags.append("anchor_stale")
    return flags


def _examples(events: list[dict]) -> dict:
    """Concrete failure examples (SPEC §6 + Bonny's spec §六.examples)."""
    mismatches = []
    for e in events:
        if e["kind"] == "verified":
            m = e["data"].get("match") or {}
            if not m.get("id_match"):
                mismatches.append({"seq": e["seq"], "reason": "id_not_matched"})
            elif m.get("price_match") is False:
                mismatches.append({"seq": e["seq"], "reason": "price_out_of_tolerance"})
            elif m.get("size") is False:
                mismatches.append({"seq": e["seq"], "reason": "size_mismatch"})
    missing = []
    claim_seqs = {e["seq"] for e in events if e["kind"] == "claim"}
    terminated: set[int] = set()
    for e in events:
        cs = (e.get("data") or {}).get("claim_seq")
        if e["kind"] in ("verified", "error") and cs is not None:
            terminated.add(cs)
        elif e["kind"] == "signal_rejected":
            terminated.add(e["seq"])
    for s in sorted(claim_seqs - terminated)[:5]:
        missing.append({"seq": s})
    return {"mismatch": mismatches[:5], "missing_receipts": missing}


def _trust_tier_majority(events: list[dict]) -> str:
    """Return the predominant trust_tier on verified+reconciliation events."""
    counts: dict[str, int] = {}
    for e in events:
        if e["kind"] in ("verified", "reconciliation"):
            t = (e.get("data") or {}).get("trust_tier")
            if t:
                counts[t] = counts.get(t, 0) + 1
    if not counts:
        return "manual"
    return max(counts, key=counts.get)


def _verdict(coverage: dict, accuracy: dict, has_anchor: bool, has_recon: bool,
             anchor_recent: bool, consistency_score: float, tier_majority: str) -> tuple[str, str]:
    """Rule-based per docs/trust_score.md §1.

    Verdict cap by trust_tier majority — a chain made of backfill_local can
    at best be Mixed; a chain made of manual at best Unverified. This
    prevents 'I made up the receipts but the math passes' from going green.
    """
    cov = coverage.get("raw", 0)
    mismatch_rate = accuracy.get("mismatch_rate", 0)
    accuracy_raw = (accuracy.get("verified_ok", 0) / accuracy["receipts"]) if accuracy.get("receipts") else 1.0

    realtime_tiers = {"exchange_realtime", "exchange_delayed", "api_verified"}
    qualifies_for_verified = (
        cov >= 0.9 and accuracy_raw >= 0.8 and mismatch_rate <= 0.05
        and has_anchor and has_recon and anchor_recent
        and tier_majority in realtime_tiers
    )
    fails_hard = (
        cov < 0.5 or mismatch_rate > 0.20
        or not has_anchor or consistency_score < 30
        or tier_majority == "manual"
    )

    if qualifies_for_verified:
        return "Verified", "green"
    if fails_hard:
        return "Unverified", "red"
    return "Mixed", "yellow"


def _enforce_verdict_score_consistency(verdict: str, score: int) -> int:
    """SPEC §4 — verdict and score must not contradict."""
    if verdict == "Verified" and score < 75:
        return 75
    if verdict == "Unverified" and score >= 40:
        return 39
    if verdict == "Mixed":
        return max(40, min(74, score))
    return score


def compute_score(events: Iterable[dict], report: VerifyReport | None = None) -> dict:
    """Return verdict + trust score per docs/trust_score.md §3 output shape."""
    events = list(events)
    if report is None:
        report = verify_chain(events)
    if not events:
        return {
            "agent": None, "verdict": "Unverified", "verdict_color": "red",
            "score": 0, "dimensions": {}, "summary": {}, "flags": ["empty_chain"],
            "examples": {"mismatch": [], "missing_receipts": []},
            "rule_breaks": ["chain is empty"],
        }

    cov_score, cov_detail, cov_meta = _coverage(events)
    acc_score, acc_detail, acc_meta = _accuracy(events)
    cons_score, cons_detail, cons_meta = _consistency(events)
    trans_score, trans_detail, trans_meta = _transparency(events)
    int_score, int_detail, int_meta = _integrity(events, report)

    raw_score = round((cov_score + acc_score + cons_score + trans_score + int_score) / 5)

    has_anchor = any(e["kind"] == "anchor" for e in events)
    has_recon = any(e["kind"] == "reconciliation" for e in events)
    anchor_recent = False
    if has_anchor:
        anchors = [e for e in events if e["kind"] == "anchor"]
        anchor_recent = (events[-1]["ts"] - anchors[-1]["ts"]) <= 24 * 3_600_000

    tier_majority = _trust_tier_majority(events)
    verdict, color = _verdict(cov_meta, acc_meta, has_anchor, has_recon, anchor_recent,
                              cons_score, tier_majority)
    score = _enforce_verdict_score_consistency(verdict, raw_score)

    dims = {
        "Coverage":     {"score": cov_score,   "detail": cov_detail},
        "Accuracy":     {"score": acc_score,   "detail": acc_detail},
        "Consistency":  {"score": cons_score,  "detail": cons_detail},
        "Transparency": {"score": trans_score, "detail": trans_detail},
        "Integrity":    {"score": int_score,   "detail": int_detail},
    }

    return {
        "agent": events[0].get("agent"),
        "window": {"from_ms": events[0]["ts"], "to_ms": events[-1]["ts"]},
        "verdict": verdict,
        "verdict_color": color,
        "score": score,
        "raw_score_pre_clamp": raw_score,
        "trust_tier_majority": tier_majority,
        "dimensions": dims,
        "summary": {
            "total_claims": cov_meta.get("total", 0),
            "receipts": acc_meta.get("receipts", 0),
            "verified": acc_meta.get("verified_ok", 0),
            "mismatch": acc_meta.get("mismatches", 0),
            "errors": sum(1 for e in events if e["kind"] == "error"),
        },
        "flags": _flags(events, dims, acc_meta),
        "examples": _examples(events),
        "chain_status": report.status.value,
    }
