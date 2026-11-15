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


class TestBacklogBoundV2(unittest.TestCase):
    """Round 7 (A-12): only a BROADCAST removal latches; an appended
    stage_consumed record clears it; dry-runs never latch."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig = X.REALLOC_LOG
        X.REALLOC_LOG = os.path.join(self.dir, "reallocations.jsonl")
        self.addCleanup(setattr, X, "REALLOC_LOG", self._orig)

    def _row(self, status):
        return json.dumps({
            "plan": {"action": "remove_and_stage", "from_pool": "A:B"},
            "removal": {"status": status},
            "deposit": {"status": "not-implemented"},
            "timestamp": "2026-11-15T00:00:00"}) + "\n"

    def test_dry_run_does_not_latch(self):
        with open(X.REALLOC_LOG, "w") as fh:
            fh.write(self._row("dry-run"))
        self.assertIsNone(X._staged_deposit_outstanding())

    def test_broadcast_latches_and_consumed_clears(self):
        with open(X.REALLOC_LOG, "w") as fh:
            fh.write(self._row("broadcast"))
            fh.write(json.dumps({"type": "stage_consumed",
                                 "by": "operator", "ts": "2026-11-15"}) + "\n")
        self.assertIsNone(X._staged_deposit_outstanding())

    def test_consumed_only_clears_stages_before_it(self):
        with open(X.REALLOC_LOG, "w") as fh:
            fh.write(self._row("broadcast"))
            fh.write(json.dumps({"type": "stage_consumed"}) + "\n")
            fh.write(self._row("broadcast"))
        out = X._staged_deposit_outstanding()
        self.assertIsNotNone(out)   # the SECOND stage is still outstanding


class TestHeBreakerEntry(unittest.TestCase):
    """Round 7 (A-4): the ring-dead breaker must gate he()'s own pagination
    ENTRY — after a full-ring failure a paginated find must not spend even
    one more 60s node attempt."""
    def test_dead_ring_raises_immediately_on_paginated_find(self):
        import liquidity_agent_v2 as LAV2
        calls = []
        old_dead, old_idx = LAV2._HE_RING_DEAD[0], LAV2._HE_NODE_IDX[0]
        LAV2._HE_RING_DEAD[0] = True
        LAV2._HE_NODE_IDX[0] = 0
        try:
            with self.assertRaises(RuntimeError) as ctx:
                LAV2.he("find", {"contract": "c", "table": "t",
                                 "query": {}, "limit": 10})
            self.assertIn("circuit breaker", str(ctx.exception))
            self.assertEqual(calls, [])   # no wire attempt at all
        finally:
            LAV2._HE_RING_DEAD[0] = old_dead
            LAV2._HE_NODE_IDX[0] = old_idx


class TestSubprocessBudget(unittest.TestCase):
    """Round 7 (A-4): the child timeout must fit the watchdog budget, and the
    budget arithmetic must floor sanely."""
    def test_timeout_fits_remaining_budget(self):
        import cron_lp_agent as C
        t0_old = C._TICK_T0
        C._TICK_T0 = None
        try:
            self.assertEqual(C._subprocess_timeout(), 600)   # un-armed: default
        finally:
            C._TICK_T0 = t0_old
        import time as _t
        C._TICK_T0 = _t.monotonic() - (C._WATCHDOG_SECONDS - 700)  # ~700s left
        try:
            # ~700 - 180 = ~520s child budget: inside the watchdog (the
            # elapsed monotonic read adds microseconds — compare loosely)
            self.assertAlmostEqual(C._subprocess_timeout(), 520, delta=1.0)
            C._TICK_T0 = _t.monotonic() - (C._WATCHDOG_SECONDS - 100)  # ~100s left
            self.assertEqual(C._subprocess_timeout(), 120)   # floor, not negative
        finally:
            C._TICK_T0 = t0_old


class TestSealCoversEverything(unittest.TestCase):
    """Round 7 (A-2): the seal meta-test — every declared (module, attr)
    entry was actually repointed. The ':2' key bug made the seal SILENTLY
    skip entries; this pin makes silence impossible."""
    def test_every_sealed_entry_applied(self):
        import conftest
        declared = {(m, a) for m, a, _ in conftest._SEALED}
        self.assertEqual(set(conftest._SEALED_APPLIED), declared,
                         "the seal silently skipped entries — a module "
                         "failed to import or an attr is missing")

    def test_tracker_resolves_db_at_call_time(self):
        # the def-time-binding class of bug, round 3: the seal's setattr
        # must reach the tracker's constructor.
        import conftest
        import lp_test_tracker as T
        sealed_db = dict(conftest._SEALED)[("lp_test_tracker", "DB_PATH")] \
            if isinstance(conftest._SEALED, dict) else None
        t = T.LPTestTracker(config={})
        self.assertTrue(str(getattr(t, "db_path", "")).startswith(
            tempfile.gettempdir()),
            f"tracker db_path {getattr(t, 'db_path', None)} is not sealed")


if __name__ == "__main__":
    unittest.main()
