"""Receipt — append-only logger conforming to Receipt Spec v0.1.

Drop-in for any AI agent. Writes hash-chained JSONL to disk. Read-only by
design — never mutates external state, never edits prior events.

Concurrent-writer-safe via fcntl.flock on each append: multiple processes
writing to the same chain file serialize on an exclusive lock and re-read
tail state under the lock, so the seq/prev_hash chain stays consistent
even with concurrent writers (e.g., a live bot + an out-of-band anchor
script writing to the same chain). On platforms without fcntl (Windows),
falls back to single-process semantics (threading.Lock only).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from receipt.chain import GENESIS_PREV_HASH, compute_hash

# fcntl is POSIX-only; on Windows or restricted environments, file-level
# locking degrades to single-process semantics. The chain still serializes
# within one process via threading.Lock.
try:
    import fcntl as _fcntl
    _HAS_FLOCK = True
except ImportError:
    _fcntl = None  # type: ignore
    _HAS_FLOCK = False

SPEC_VERSION = "0.1"
SCHEMA_VERSION = "1"

ALLOWED_KINDS = frozenset({
    "claim", "external_action", "verified",
    "reconciliation", "anchor",
    "error", "heartbeat", "signal_rejected",
})

ALLOWED_TRUST_TIERS = frozenset({
    "exchange_realtime", "exchange_delayed", "api_verified",
    "backfill_local", "manual",
})


def _now_ms() -> int:
    """Integer milliseconds since Unix epoch — SPEC §1."""
    return int(time.time() * 1000)


class Receipt:
    """Append-only Receipt logger. Thread-safe within a single process."""

    def __init__(self, agent: str, out_path: str | Path, autoflush: bool = True):
        if not agent:
            raise ValueError("agent is required (e.g. 'iBitLabs/sniper-v5.1')")
        self.agent = agent
        self.out_path = Path(out_path).expanduser()
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.autoflush = autoflush
        self._lock = threading.Lock()
        self._seq, self._prev_hash = self._tail_state()

    # ── public API ────────────────────────────────────────────────────────

    def claim(self, **data: Any) -> int:
        if "action" not in data:
            raise ValueError("claim requires 'action'")
        return self._append("claim", data)

    def external_action(self, claim_seq: int, **data: Any) -> int:
        data = {"claim_seq": claim_seq, **data}
        if "venue" not in data:
            raise ValueError("external_action requires 'venue'")
        return self._append("external_action", data)

    def verified(self, claim_seq: int, *, trust_tier: str, match: dict, **data: Any) -> int:
        if trust_tier not in ALLOWED_TRUST_TIERS:
            raise ValueError(f"trust_tier must be one of {sorted(ALLOWED_TRUST_TIERS)}")
        for required in ("symbol", "side", "size", "price_match",
                         "time_match", "id_match", "tolerance_used"):
            if required not in match:
                raise ValueError(f"match.{required} is required (SPEC §7)")
        data = {"claim_seq": claim_seq, "trust_tier": trust_tier, "match": match, **data}
        return self._append("verified", data)

    def reconciliation(self, *, period: str, trust_tier: str,
                       matched: int, unmatched: int, errors: int, **data: Any) -> int:
        if trust_tier not in ALLOWED_TRUST_TIERS:
            raise ValueError(f"trust_tier must be one of {sorted(ALLOWED_TRUST_TIERS)}")
        data = {
            "period": period, "trust_tier": trust_tier,
            "matched": matched, "unmatched": unmatched, "errors": errors,
            **data,
        }
        return self._append("reconciliation", data)

    def anchor(self, *, merkle_root: str, anchor_uri: str, anchor_kind: str, **data: Any) -> int:
        if not merkle_root.startswith("sha256:"):
            raise ValueError("merkle_root must be 'sha256:...' format")
        if anchor_kind not in {"twitter", "moltbook", "github_commit",
                                "ipfs", "arweave", "ethereum", "btc_op_return"}:
            raise ValueError(f"unknown anchor_kind: {anchor_kind}")
        return self._append("anchor", {
            "merkle_root": merkle_root,
            "anchor_uri": anchor_uri,
            "anchor_kind": anchor_kind,
            "covers_seq_range": [0, max(self._seq - 1, 0)],
            **data,
        })

    def error(self, *, claim_seq: int | None, phase: str, error_type: str,
              message: str, retryable: bool = False, **data: Any) -> int:
        if phase not in {"external_action", "verified", "reconciliation", "other"}:
            raise ValueError(f"unknown phase: {phase}")
        return self._append("error", {
            "claim_seq": claim_seq, "phase": phase,
            "error_type": error_type, "message": message,
            "retryable": retryable, **data,
        })

    def heartbeat(self, *, status: str = "alive", latency_ms: int | None = None, **data: Any) -> int:
        return self._append("heartbeat", {
            "status": status, "latency_ms": latency_ms, **data,
        })

    def signal_rejected(self, *, would_be_action: str, rejected_by: str,
                        reason: str, **data: Any) -> int:
        return self._append("signal_rejected", {
            "would_be_action": would_be_action,
            "rejected_by": rejected_by,
            "reason": reason,
            **data,
        })

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def head_hash(self) -> str:
        return self._prev_hash

    # ── internal ──────────────────────────────────────────────────────────

    def _append(self, kind: str, data: dict) -> int:
        """Append one event with cross-process locking. Holds an exclusive
        fcntl.flock on the chain file for the critical section: re-reads
        the file's current tail under the lock, builds the new event with
        fresh seq + prev_hash, writes it, releases the lock. Two processes
        writing concurrently serialize on this lock; each sees the other's
        writes."""
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"unknown kind: {kind}")
        with self._lock:  # intra-process
            with self.out_path.open("a+", encoding="utf-8") as f:
                if _HAS_FLOCK:
                    _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
                try:
                    # Re-read tail under the lock so we don't write stale
                    # seq / prev_hash if another process appended since
                    # this Receipt object was constructed.
                    fresh_seq, fresh_prev = self._read_tail_locked(f)
                    self._seq = fresh_seq
                    self._prev_hash = fresh_prev

                    event = {
                        "v": SPEC_VERSION,
                        "schema_version": SCHEMA_VERSION,
                        "ts": _now_ms(),
                        "seq": self._seq,
                        "agent": self.agent,
                        "kind": kind,
                        "data": data,
                        "prev_hash": self._prev_hash,
                    }
                    event["hash"] = compute_hash(event)

                    f.seek(0, 2)  # end
                    f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                    if self.autoflush:
                        f.flush()
                        os.fsync(f.fileno())

                    written_seq = self._seq
                    self._seq += 1
                    self._prev_hash = event["hash"]
                    return written_seq
                finally:
                    if _HAS_FLOCK:
                        _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)

    @staticmethod
    def _read_tail_locked(open_file) -> tuple[int, str]:
        """Read the chain tail from an already-open file handle. Assumes
        flock is held by caller. Returns (next_seq, prev_hash)."""
        open_file.seek(0, 2)  # end
        if open_file.tell() == 0:
            return 0, GENESIS_PREV_HASH
        # Scan backwards to find the last non-empty line. Reading the
        # whole file would be O(n) per append; instead we read backwards
        # in 8 KB chunks until we have a complete last line.
        chunk_size = 8192
        pos = open_file.tell()
        tail = ""
        while pos > 0:
            read_size = min(chunk_size, pos)
            pos -= read_size
            open_file.seek(pos)
            tail = open_file.read(read_size) + tail
            stripped = tail.rstrip("\n")
            if "\n" in stripped:
                # Found a complete line — the last one
                last_line = stripped.rsplit("\n", 1)[-1]
                ev = json.loads(last_line)
                return int(ev["seq"]) + 1, ev["hash"]
        # Whole file was one line (or empty)
        line = tail.strip()
        if not line:
            return 0, GENESIS_PREV_HASH
        ev = json.loads(line)
        return int(ev["seq"]) + 1, ev["hash"]

    def _tail_state(self) -> tuple[int, str]:
        if not self.out_path.exists() or self.out_path.stat().st_size == 0:
            return 0, GENESIS_PREV_HASH
        last = None
        with self.out_path.open("rb") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return 0, GENESIS_PREV_HASH
        ev = json.loads(last)
        return int(ev["seq"]) + 1, ev["hash"]
