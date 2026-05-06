"""Cross-implementation parity test.

Confirms that receipt/score.py (Python) and viewer/score.js (JS, run via Node)
produce byte-identical output on the same inputs. This is the "different
implementations same result" guarantee SPEC §0 promises.

Run:
    cd ~/Documents/receipt
    PYTHONPATH=. python3 -m unittest tests.test_cross_impl

Requires Node >= 16 on PATH.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt import Receipt, compute_score, verify_chain

REPO = Path(__file__).parent.parent
NODE_RUNNER = REPO / "tests" / "cross_impl_runner.js"


def _node_path() -> str:
    """Pick the first available node binary."""
    for p in ("/opt/homebrew/opt/node@22/bin/node", "/opt/homebrew/bin/node",
              "/usr/local/bin/node", "node"):
        try:
            if subprocess.run([p, "--version"], capture_output=True, timeout=5).returncode == 0:
                return p
        except Exception:
            continue
    raise RuntimeError("node not found; tests/test_cross_impl needs Node ≥16")


def _build_clean_chain(path: Path) -> None:
    r = Receipt(agent="cross-impl/test", out_path=path)
    r.heartbeat(status="alive", latency_ms=10)
    c = r.claim(action="open_long", symbol="BTC-USD", side="buy", size=0.1,
                price_intended=67000.0,
                ai={"model": "rule_based", "provider": "internal",
                    "decision_mode": "rule_based", "agent_version": "test/v1"})
    r.external_action(c, venue="coinbase", request={"endpoint": "/orders", "method": "POST"},
                      response={"status": 200, "order_id": "abc-123"})
    r.verified(c, trust_tier="exchange_realtime",
               source="coinbase", fill_price=67005.0, filled_size=0.1,
               fill_ts=int(time.time() * 1000),
               match={"symbol": "BTC-USD", "side": True, "size": True,
                      "price_match": True, "time_match": True, "id_match": True,
                      "tolerance_used": {"price": 0.002, "time_ms": 5000}})
    r.reconciliation(period="t1", trust_tier="exchange_realtime",
                     matched=1, unmatched=0, errors=0)
    r.anchor(merkle_root=r.head_hash, anchor_uri="ipfs://Qm...", anchor_kind="ipfs")


def _build_tampered_chain(path: Path) -> None:
    """Build a clean chain, then mutate one event to break the chain."""
    _build_clean_chain(path)
    events = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    events[1]["data"]["size"] = 9999.0
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _build_unverified_chain(path: Path) -> None:
    r = Receipt(agent="cross-impl/test", out_path=path)
    r.claim(action="open_long", symbol="BTC-USD", side="buy", size=0.1,
            ai={"model": "rule_based", "provider": "internal",
                "decision_mode": "rule_based", "agent_version": "test/v1"})
    r.reconciliation(period="t1", trust_tier="exchange_realtime",
                     matched=0, unmatched=1, errors=0)
    # No anchor → fails Verified gate.


FIXTURES = [
    ("clean_chain", _build_clean_chain),
    ("tampered_chain", _build_tampered_chain),
    ("unverified_chain", _build_unverified_chain),
]

# fields whose values must match exactly between Python and JS
COMPARE_KEYS = (
    "verdict", "verdict_color", "score", "raw_score_pre_clamp",
    "trust_tier_majority", "schema_version",
)
DIM_KEYS = ("Coverage", "Accuracy", "Consistency", "Transparency", "Integrity")


class TestCrossImpl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = _node_path()
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="receipt_xtest_"))

    def _run_node(self, jsonl_path: Path) -> dict:
        out = subprocess.check_output(
            [self.node, str(NODE_RUNNER), str(jsonl_path)],
            timeout=10,
        )
        return json.loads(out)

    def _check_parity(self, name: str, builder):
        path = self.tmpdir / f"{name}.jsonl"
        builder(path)

        events = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        py_out = compute_score(events, verify_chain(events))
        js_out = self._run_node(path)

        # primary fields
        for k in COMPARE_KEYS:
            self.assertEqual(py_out.get(k), js_out.get(k),
                             f"[{name}] field '{k}' diverges: py={py_out.get(k)} js={js_out.get(k)}")
        # dimension scores
        for dim in DIM_KEYS:
            py_dim = (py_out.get("dimensions") or {}).get(dim, {})
            js_dim = (js_out.get("dimensions") or {}).get(dim, {})
            self.assertEqual(py_dim.get("score"), js_dim.get("score"),
                             f"[{name}] dim '{dim}' score diverges: py={py_dim.get('score')} js={js_dim.get('score')}")
        # rule_breaks + suspicion_flags must be equal as sets
        self.assertEqual(set(py_out.get("rule_breaks") or []),
                         set(js_out.get("rule_breaks") or []),
                         f"[{name}] rule_breaks diverge")
        self.assertEqual(set(py_out.get("suspicion_flags") or []),
                         set(js_out.get("suspicion_flags") or []),
                         f"[{name}] suspicion_flags diverge")
        # summary numeric fields
        for sk in ("total_claims", "receipts", "verified", "mismatch", "errors"):
            self.assertEqual((py_out.get("summary") or {}).get(sk),
                             (js_out.get("summary") or {}).get(sk),
                             f"[{name}] summary.{sk} diverges")

    def test_clean_chain_parity(self):
        self._check_parity(*FIXTURES[0])

    def test_tampered_chain_parity(self):
        self._check_parity(*FIXTURES[1])

    def test_unverified_chain_parity(self):
        self._check_parity(*FIXTURES[2])

    def test_real_backfill_chain_parity(self):
        path = Path("~/ibitlabs/audit_export/sniper-v5.1.receipt.jsonl").expanduser()
        if not path.exists():
            self.skipTest("backfill chain not present (skipped)")
        events = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        py_out = compute_score(events, verify_chain(events))
        js_out = self._run_node(path)
        for k in COMPARE_KEYS:
            self.assertEqual(py_out.get(k), js_out.get(k),
                             f"[real backfill] field '{k}' diverges: py={py_out.get(k)} js={js_out.get(k)}")


if __name__ == "__main__":
    unittest.main()
