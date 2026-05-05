"""Receipt — append-only logger conforming to Receipt Spec v0.1.

Drop-in for any AI agent. Writes hash-chained JSONL to disk. Read-only by
design — never mutates exchange state, never edits prior events.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from receipt.chain import GENESIS_PREV_HASH, compute_hash

SPEC_VERSION = "0.1"

# kinds permitted in v0.1 (Spec §2)
ALLOWED_KINDS = frozenset(
    {"claim", "external_action", "verified", "reconciliation", "anchor"}
)


def _utc_now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


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

    # ── public API ──

    def claim(self, **data: Any) -> int:
        """Log what the agent is about to do. Returns the seq for linkage."""
        if "action" not in data:
            raise ValueError("claim requires 'action'")
        return self._append("claim", data)

    def external_action(self, claim_seq: int, **data: Any) -> int:
        """Log the external call that supposedly executes a claim."""
        data = {"claim_seq": claim_seq, **data}
        if "venue" not in data:
            raise ValueError("external_action requires 'venue'")
        return self._append("external_action", data)

    def verified(self, claim_seq: int, **data: Any) -> int:
        """Log external-truth confirmation of a claim. `match` dict required."""
        data = {"claim_seq": claim_seq, **data}
        if "match" not in data:
            raise ValueError("verified requires 'match' dict (size/side/...)")
        if "source" not in data:
            raise ValueError("verified requires 'source' (e.g. 'coinbase_intx')")
        return self._append("verified", data)

    def reconciliation(self, **data: Any) -> int:
        """Log a periodic full-state check. SPEC §2.4 requires ≥1 per 24h."""
        if "match" not in data:
            raise ValueError("reconciliation requires 'match' bool")
        return self._append("reconciliation", data)

    def anchor(self, merkle_root: str, anchor_uri: str, anchor_kind: str, **data: Any) -> int:
        """Log a public timestamp anchor (twitter / moltbook / github_commit / btc_op_return)."""
        if not merkle_root.startswith("sha256:"):
            raise ValueError("merkle_root must be 'sha256:...' format")
        return self._append(
            "anchor",
            {
                "merkle_root": merkle_root,
                "anchor_uri": anchor_uri,
                "anchor_kind": anchor_kind,
                "covers_seq_range": [0, self._seq - 1] if self._seq > 0 else [0, 0],
                **data,
            },
        )

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def head_hash(self) -> str:
        return self._prev_hash

    # ── internal ──

    def _append(self, kind: str, data: dict) -> int:
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"unknown kind: {kind} (spec v{SPEC_VERSION})")
        with self._lock:
            event = {
                "v": SPEC_VERSION,
                "ts": _utc_now_iso(),
                "seq": self._seq,
                "agent": self.agent,
                "kind": kind,
                "data": data,
                "prev_hash": self._prev_hash,
            }
            event["hash"] = compute_hash(event)
            with self.out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                if self.autoflush:
                    f.flush()
                    os.fsync(f.fileno())
            written_seq = self._seq
            self._seq += 1
            self._prev_hash = event["hash"]
            return written_seq

    def _tail_state(self) -> tuple[int, str]:
        """Recover (next_seq, prev_hash) by scanning to the last event."""
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
