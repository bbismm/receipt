"""Smoke tests for Receipt v0.1.

Run with: cd ~/Documents/receipt && python -m unittest tests/test_chain.py
"""
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt import Receipt
from receipt.chain import ChainStatus, verify_chain


class TestReceiptBasics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def _events(self):
        return [json.loads(l) for l in open(self.path) if l.strip()]

    def test_empty_chain_status(self):
        report = verify_chain([])
        self.assertEqual(report.status, ChainStatus.EMPTY)

    def test_full_lifecycle_verifies(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        c = r.claim(action="open_short", symbol="SOL", size=0.1)
        r.external_action(c, venue="mock", request={"x": 1}, response={"order_id": "abc"})
        r.verified(c, source="mock", fill_price=100, match={"size": True, "side": True})
        r.reconciliation(local={"bal": 1000}, external={"bal": 1000}, match=True)

        report = verify_chain(self._events())
        self.assertEqual(report.status, ChainStatus.VERIFIED, report.issues)
        self.assertEqual(report.n_claims, 1)
        self.assertEqual(report.n_verified, 1)

    def test_unmatched_claim_fails(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        r.claim(action="open_short", symbol="SOL", size=0.1)
        r.reconciliation(local={"bal": 1000}, external={"bal": 1000}, match=True)
        # no verified event

        report = verify_chain(self._events())
        self.assertEqual(report.status, ChainStatus.UNVERIFIED_CLAIM)

    def test_tampered_data_breaks_chain(self):
        r = Receipt(agent="test/v1", out_path=self.path)
        r.claim(action="open_short", symbol="SOL", size=0.1)

        # tamper: rewrite the line with a different action, keep the hash
        events = self._events()
        events[0]["data"]["action"] = "open_long"  # silent edit
        with open(self.path, "w") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        report = verify_chain(self._events())
        self.assertEqual(report.status, ChainStatus.INVALID_CHAIN)

    def test_append_resumes_state(self):
        r1 = Receipt(agent="test/v1", out_path=self.path)
        r1.claim(action="a", symbol="X")
        r1.claim(action="b", symbol="Y")
        head1 = r1.head_hash

        r2 = Receipt(agent="test/v1", out_path=self.path)  # reopen
        self.assertEqual(r2.seq, 2)
        self.assertEqual(r2.head_hash, head1)

        r2.reconciliation(match=True, local={}, external={})
        events = self._events()
        self.assertEqual(events[2]["prev_hash"], head1)


if __name__ == "__main__":
    unittest.main()
