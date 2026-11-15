"""Pins for the round-6 triage fixes (Fable round 6, keep-list items 4/5/6/8).

Each test pins a NEW gate: the fee-evidence freshness horizon, share-less
rows contributing APR but never earnings, the reallocation backlog bound,
and the idle-deploy throttle-write abort. Suite-count context: the gates
landed with honest fixture updates across test_lp_fee_apr_aggregation,
test_idle_deploy, test_realloc_executor and test_reward_per_hive.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lp_recommendation as R
import lp_realloc_executor as X

HOUR = 3_600_000
NOW_MS = int(datetime.now().timestamp() * 1000)


class TestFeeFreshness(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "fee_snapshots.jsonl")
        self._orig = R.FEE_SNAPSHOTS_PATH
        R.FEE_SNAPSHOTS_PATH = self.path
        R._SNAPSHOT_CACHE.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        R.FEE_SNAPSHOTS_PATH = self._orig
        R._SNAPSHOT_CACHE.clear()

    def _write(self, rows):
        with open(self.path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def _snap(self, at_ms, base_vol, quote_vol):
        return {"pool": "A:B", "at_ms": at_ms, "snapshot": {
            "at_ms": at_ms, "status": "ok",
            "share": {"num": 1, "den": 1},
            "volumes": {"baseVolume": base_vol, "quoteVolume": quote_vol},
            "reserves": {"A": 1000.0, "B": 1000.0}}}

    def test_stale_evidence_is_unmeasurable_not_a_number(self):
        """Newest snapshot older than the horizon -> fee_apr_pct None +
        stale=True. An out-of-date fee measurement is not a measurement."""
        old = NOW_MS - int(R.FEE_APR_MAX_SNAPSHOT_AGE_H + 5) * HOUR
        self._write([self._snap(old - 5 * HOUR, 0, 0),
                     self._snap(old, 100, 100)])
        out = R.measure_fee_apr("A:B", price_map={"A": 1.0, "B": 1.0})
        self.assertIsNone(out["fee_apr_pct"])
        self.assertTrue(out["stale"])
        self.assertGreater(out["last_snapshot_age_h"],
                           R.FEE_APR_MAX_SNAPSHOT_AGE_H)

    def test_fresh_evidence_still_measures(self):
        now = NOW_MS - HOUR
        self._write([self._snap(now - 5 * HOUR, 0, 0),
                     self._snap(now, 100, 100)])
        out = R.measure_fee_apr("A:B", price_map={"A": 1.0, "B": 1.0})
        self.assertIsNotNone(out["fee_apr_pct"])
        self.assertFalse(out.get("stale", False))

    def test_shareless_rows_contribute_apr_never_earnings(self):
        """A missing share block discards neither the interval nor honesty:
        the APR cancels the share; the earned attribution refuses to invent
        one."""
        fresh = NOW_MS - HOUR
        rows = [self._snap(fresh - 5 * HOUR, 0, 0),
                self._snap(fresh, 100, 100)]
        # the interval reads its share from the EARLIER snapshot
        del rows[0]["snapshot"]["share"]           # share block gone
        self._write(rows)
        out = R.measure_fee_apr("A:B", price_map={"A": 1.0, "B": 1.0})
        self.assertIsNotNone(out["fee_apr_pct"])   # the APR survives
        self.assertEqual(out["shareless_intervals"], 1)
        self.assertIsNone(out["earned_base"])      # the attribution does not
        self.assertIsNone(out["earned_quote"])


class TestBacklogBound(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = X.REALLOC_LOG
        X.REALLOC_LOG = os.path.join(self.dir, "reallocations.jsonl")
        self.addCleanup(setattr, X, "REALLOC_LOG", self._orig)

    def test_outstanding_stage_blocks_new_removals(self):
        with open(X.REALLOC_LOG, "w") as fh:
            fh.write(json.dumps({
                "plan": {"action": "remove_and_stage", "from_pool": "A:B"},
                "removal": {"status": "broadcast"},
                "deposit": {"status": "not-implemented"},
                "timestamp": "2026-11-14T12:00:00"}) + "\n")
        agent = type("A", (), {})()
        decision = {"recommended_reallocation": {"from_pool": "C:D",
                                                 "to_pool": "A:B"}}
        out = X.plan_reallocation(agent, decision)
        self.assertEqual(out["action"], "blocked")
        self.assertIn("staged deposit outstanding", out["reason"])

    def test_no_stage_no_block(self):
        agent = type("A", (), {})()
        decision = {"recommended_reallocation": {"from_pool": "C:D",
                                                 "to_pool": "A:B"}}
        # no route from C:D tokens to A:B either — but the BACKLOG gate must
        # not be the thing that fires; expect a route/verdict refusal instead
        out = X.plan_reallocation(agent, decision)
        self.assertNotIn("staged deposit outstanding",
                         str(out.get("reason", "")))


class TestThrottleWriteAbort(unittest.TestCase):
    def test_failed_throttle_write_returns_false(self):
        # a directory where the throttle file would be: open() raises IsADir
        d = tempfile.mkdtemp()
        old = X.IDLE_DEPLOY_THROTTLE
        X.IDLE_DEPLOY_THROTTLE = d          # a DIRECTORY, not a file
        try:
            self.assertFalse(X._mark_idle_deploy())
        finally:
            X.IDLE_DEPLOY_THROTTLE = old


if __name__ == "__main__":
    unittest.main()
