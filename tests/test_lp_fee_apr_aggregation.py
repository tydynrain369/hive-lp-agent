"""The authoritative fee-APR aggregation: outlier suppression and honesty.

`measure_fee_apr` used to report the FIRST-to-LAST cumulative volume delta over
a pool's whole retained history. That is the anti-pattern
hive-lp-decision-preferences names: one anomalous hour flips the window, and
since it divided by the window span, trimming history for retention silently
re-scoped the figure. These tests pin the replacement.

Hermetic: a synthetic store is written to a temp dir and FEE_SNAPSHOTS_PATH is
pointed at it, so no test here touches the live store or the network.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lp_recommendation as R

HOUR = 3_600_000
POOL = "AAA:BBB"

# 11 snapshots, 100 base / 100 quote volume per hour, share 1, tvl 2000:
#   fees/interval = 200 * 0.0025 = 0.5  ->  0.5 * 8760 / 2000 * 100 = 219.0 %
STEADY_APR = 219.0


def _snap(at_h, base_vol, quote_vol, base_res=1000.0, quote_res=1000.0,
          share_num=1, share_den=1, status="ok"):
    return {"pool": POOL, "at_ms": at_h * HOUR, "snapshot": {
        "at_ms": at_h * HOUR, "status": status,
        "share": {"num": share_num, "den": share_den},
        "volumes": {"baseVolume": base_vol, "quoteVolume": quote_vol},
        "reserves": {"AAA": base_res, "BBB": quote_res},
    }}


def _steady_vols(n, step=100):
    return [i * step for i in range(n)]


class FeeAprAggregation(unittest.TestCase):
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

    def _series(self, n, **kw):
        vols = _steady_vols(n)
        return [_snap(i, vols[i], vols[i], **kw) for i in range(n)]

    # --- the aggregation itself -------------------------------------------

    def test_steady_volume_matches_the_analytic_apr(self):
        self._write(self._series(11))
        out = R.measure_fee_apr(POOL)
        self.assertEqual(out["intervals"], 10)
        self.assertAlmostEqual(out["fee_apr_pct"], STEADY_APR, places=2)

    def test_one_whale_hour_cannot_dominate_the_window(self):
        """Cumulative (the old method) let a single hour own the result."""
        vols = _steady_vols(11)
        vols[6] = vols[5] + 10_000            # interval 5->6 is the whale
        for i in range(7, 11):
            vols[i] = vols[6] + (i - 6) * 100
        self._write([_snap(i, vols[i], vols[i]) for i in range(11)])

        out = R.measure_fee_apr(POOL)
        # Old first-to-last cumulative would have read ~2387 %.
        self.assertAlmostEqual(out["fee_apr_pct"], STEADY_APR, places=2)
        self.assertGreater(out["capped_intervals"], 0)
        self.assertLess(out["fee_apr_pct"], 400.0)

    def test_result_does_not_depend_on_how_much_history_retention_keeps(self):
        """Retention must not silently re-scope the reported APR."""
        vols = _steady_vols(61)
        full = [_snap(i, vols[i], vols[i]) for i in range(61)]
        kept = full[-21:]                       # as if rotation kept only 20 ivs
        self._write(full)
        wide = R.measure_fee_apr(POOL)
        self._write(kept)
        R._SNAPSHOT_CACHE.clear()
        narrow = R.measure_fee_apr(POOL)
        self.assertAlmostEqual(wide["fee_apr_pct"], narrow["fee_apr_pct"],
                               places=6)
        self.assertLess(narrow["intervals"], wide["intervals"])

    def test_counter_reset_is_skipped_not_read_as_a_negative_fee(self):
        """A re-created/re-funded pool resets volumes; that is not a fee."""
        vols = _steady_vols(11)
        for i in range(6, 11):                  # counter restarts mid-window
            vols[i] = (i - 6) * 100
        self._write([_snap(i, vols[i], vols[i]) for i in range(11)])
        out = R.measure_fee_apr(POOL)
        # The single straddling interval 5->6 is dropped (delta -500); the 5
        # before it and the 4 after it survive, all steady.
        self.assertEqual(out["intervals"], 9)
        self.assertAlmostEqual(out["fee_apr_pct"], STEADY_APR, places=2)

    def test_a_gap_is_not_charged_as_hours_we_earned_nothing_in(self):
        rows = self._series(11)
        rows[5]["snapshot"]["status"] = "error"      # one failed probe
        self._write(rows)
        out = R.measure_fee_apr(POOL)
        # Intervals 4->5 and 5->6 are dropped; 8 of 10 contribute.
        self.assertEqual(out["intervals"], 8)
        self.assertAlmostEqual(out["window_h"], 8.0, places=6)
        # The dropped gap must NOT drag the rate down: still the steady APR.
        self.assertAlmostEqual(out["fee_apr_pct"], STEADY_APR, places=2)

    # --- honesty contract -------------------------------------------------

    def test_insufficient_data_is_None_not_a_fabricated_zero(self):
        self._write([_snap(0, 0, 0)])                # a single snapshot
        self.assertIsNone(R.measure_fee_apr(POOL))

    def test_unknown_pool_is_None(self):
        self._write(self._series(11))
        self.assertIsNone(R.measure_fee_apr("NOPE:NOPE"))

    def test_all_ok_but_no_volume_reports_a_real_zero(self):
        """Genuinely idle is a MEASURED 0, which is different from None."""
        self._write([_snap(i, 0, 0) for i in range(11)])
        out = R.measure_fee_apr(POOL)
        self.assertIsNotNone(out)
        self.assertEqual(out["fee_apr_pct"], 0.0)

    def test_earned_totals_are_the_sum_over_contributing_intervals(self):
        self._write(self._series(11))
        out = R.measure_fee_apr(POOL)
        # 10 intervals x 100 volume x 0.0025 fee x share 1
        self.assertAlmostEqual(out["earned_base"], 2.5, places=9)
        self.assertAlmostEqual(out["earned_quote"], 2.5, places=9)

    def test_share_fraction_scales_the_earnings_and_the_tvl_alike(self):
        """Our entitlement, not the pool's whole flow."""
        third = self._series(11, share_num=1, share_den=3)
        self._write(third)
        out = R.measure_fee_apr(POOL)
        # tvl and fees both scale by 1/3, so the APR is unchanged...
        self.assertAlmostEqual(out["fee_apr_pct"], STEADY_APR, places=2)
        # ...while the earnings we actually booked are a third.
        self.assertAlmostEqual(out["earned_base"], 2.5 / 3, places=9)

    # --- the read is once per file, not once per pool ---------------------

    def test_store_is_read_once_per_file_regardless_of_pool_count(self):
        rows = []
        for pool in ("A:B", "C:D", "E:F"):
            for s in self._series(11):
                s = json.loads(json.dumps(s))
                s["pool"] = pool
                s["snapshot"]["reserves"] = {pool.split(":")[0]: 1000.0,
                                             pool.split(":")[1]: 1000.0}
                rows.append(s)
        self._write(rows)
        reads = []
        real_open = open

        def counting_open(p, *a, **kw):
            if p == self.path:
                reads.append(p)
            return real_open(p, *a, **kw)

        import builtins
        builtins.open = counting_open
        try:
            for pool in ("A:B", "C:D", "E:F"):
                self.assertIsNotNone(R.measure_fee_apr(pool))
        finally:
            builtins.open = real_open
        self.assertEqual(len(reads), 1, f"store read {len(reads)}x, want 1")


if __name__ == "__main__":
    unittest.main()