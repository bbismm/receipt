"""Smoke tests for Receipt v0.1 — schema_version='1', ts in ms."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt import Receipt
from receipt.chain import ChainStatus, verify_chain


GOOD_MATCH = {
    "symbol": "BTC-USD", "side": "buy", "size": 0.1,
    "price_match": True, "time_match": True, "id_match": True,
    "tolerance_used": {"price": 0.002, "time_ms": 5000},
}


class TestReceipt(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        f.close()
        self.path = f.name

    def _events(self):
        with open(self.path) as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_empty_chain(self):
        self.assertEqual(verify_chain([]).status, ChainStatus.EMPTY)

    def test_full_lifecycle_verifies(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        c = r.claim(action="open_long", symbol="BTC-USD", side="buy", size=0.1)
        r.external_action(c, venue="coinbase", response={"order_id": "abc"})
        r.verified(c, trust_tier="exchange_realtime", match=GOOD_MATCH)
        r.reconciliation(period="2026-05-05/2026-05-06",
                         trust_tier="exchange_realtime",
                         matched=1, unmatched=0, errors=0)
        report = verify_chain(self._events())
        self.assertEqual(report.status, ChainStatus.VERIFIED, report.issues)
        self.assertEqual(report.n_claims, 1)
        self.assertEqual(report.n_verified, 1)

    def test_id_match_false_is_suspect(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        c = r.claim(action="open_long", symbol="BTC-USD", side="buy", size=0.1)
        r.external_action(c, venue="coinbase", response={"order_id": "abc"})
        bad_match = {**GOOD_MATCH, "id_match": False}
        r.verified(c, trust_tier="exchange_realtime", match=bad_match)
        r.reconciliation(period="x", trust_tier="exchange_realtime",
                         matched=0, unmatched=1, errors=0)
        report = verify_chain(self._events())
        self.assertEqual(report.status, ChainStatus.SUSPECT_VERIFICATION)

    def test_unmatched_claim_fails(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        r.claim(action="open_long", symbol="BTC-USD", side="buy", size=0.1)
        r.reconciliation(period="x", trust_tier="exchange_realtime",
                         matched=0, unmatched=1, errors=0)
        report = verify_chain(self._events())
        self.assertEqual(report.status, ChainStatus.UNVERIFIED_CLAIM)

    def test_error_terminates_claim(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        c = r.claim(action="open_long", symbol="BTC-USD", side="buy", size=0.1)
        r.error(claim_seq=c, phase="external_action",
                error_type="exchange_5xx", message="venue down", retryable=True)
        r.reconciliation(period="x", trust_tier="exchange_realtime",
                         matched=0, unmatched=0, errors=1)
        report = verify_chain(self._events())
        self.assertEqual(report.status, ChainStatus.VERIFIED)
        self.assertEqual(report.n_errors, 1)

    def test_tampered_data_breaks_chain(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        r.claim(action="open_long", symbol="BTC-USD", side="buy", size=0.1)
        events = self._events()
        events[0]["data"]["size"] = 99.0
        with open(self.path, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        report = verify_chain(self._events())
        self.assertEqual(report.status, ChainStatus.INVALID_CHAIN)

    def test_verified_requires_match_completeness(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        c = r.claim(action="open_long", symbol="BTC-USD", side="buy", size=0.1)
        with self.assertRaises(ValueError):
            r.verified(c, trust_tier="exchange_realtime", match={"side": True})

    def test_verified_requires_known_trust_tier(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        c = r.claim(action="open_long", symbol="BTC-USD", side="buy", size=0.1)
        with self.assertRaises(ValueError):
            r.verified(c, trust_tier="self_attested", match=GOOD_MATCH)

    def test_heartbeat_and_signal_rejected_emit(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        r.heartbeat(status="alive", latency_ms=120)
        r.signal_rejected(would_be_action="open_long",
                          rejected_by="regime_gate",
                          reason="counter-trend")
        events = self._events()
        kinds = [e["kind"] for e in events]
        self.assertIn("heartbeat", kinds)
        self.assertIn("signal_rejected", kinds)
        self.assertEqual(verify_chain(events).status, ChainStatus.VERIFIED)

    def test_ts_is_integer_ms(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        r.heartbeat()
        ev = self._events()[0]
        self.assertIsInstance(ev["ts"], int)
        self.assertGreater(ev["ts"], 1_500_000_000_000)  # post-2017 in ms
        self.assertLess(ev["ts"], 10_000_000_000_000)    # pre-year-2286 in ms

    def test_append_resumes_state(self):
        r1 = Receipt(agent="test/v1", out_path=self.path)
        r1.heartbeat()
        r1.heartbeat()
        head1 = r1.head_hash
        r2 = Receipt(agent="test/v1", out_path=self.path)
        self.assertEqual(r2.seq, 2)
        self.assertEqual(r2.head_hash, head1)


if __name__ == "__main__":
    unittest.main()
