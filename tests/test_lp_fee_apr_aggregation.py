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
        out = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
        self.assertEqual(out["intervals"], 10)
        self.assertAlmostEqual(out["fee_apr_pct"], STEADY_APR, places=2)

    def test_one_whale_hour_cannot_dominate_the_window(self):
        """Cumulative (the old method) let a single hour own the result."""
        vols = _steady_vols(11)
        vols[6] = vols[5] + 10_000            # interval 5->6 is the whale
        for i in range(7, 11):
            vols[i] = vols[6] + (i - 6) * 100
        self._write([_snap(i, vols[i], vols[i]) for i in range(11)])

        out = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
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
        wide = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
        self._write(kept)
        R._SNAPSHOT_CACHE.clear()
        narrow = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
        self.assertAlmostEqual(wide["fee_apr_pct"], narrow["fee_apr_pct"],
                               places=6)
        self.assertLess(narrow["intervals"], wide["intervals"])

    def test_counter_reset_is_skipped_not_read_as_a_negative_fee(self):
        """A re-created/re-funded pool resets volumes; that is not a fee."""
        vols = _steady_vols(11)
        for i in range(6, 11):                  # counter restarts mid-window
            vols[i] = (i - 6) * 100
        self._write([_snap(i, vols[i], vols[i]) for i in range(11)])
        out = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
        # The single straddling interval 5->6 is dropped (delta -500); the 5
        # before it and the 4 after it survive, all steady.
        self.assertEqual(out["intervals"], 9)
        self.assertAlmostEqual(out["fee_apr_pct"], STEADY_APR, places=2)

    def test_a_gap_is_not_charged_as_hours_we_earned_nothing_in(self):
        rows = self._series(11)
        rows[5]["snapshot"]["status"] = "error"      # one failed probe
        self._write(rows)
        out = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
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
        out = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
        self.assertIsNotNone(out)
        self.assertEqual(out["fee_apr_pct"], 0.0)

    def test_usd_valuation_kills_the_unit_mix(self):
        """P0-14 (Opus round 4): the old math summed raw token quantities —
        (d_base + d_quote) fees over (r0 + r1) TVL — which skews the APR by
        the tokens' price ratio. With AAA worth 10x BBB, equal VOLUME in the
        AAA side must read ~10x the fee income of the BBB side, and the APR
        must reflect USD, not a mixed-unit sum."""
        # 100 base vol (AAA @$10) + 100 quote vol (BBB @$0.1) per hour:
        # USD fees/interval = (100*10 + 100*0.1) * 0.0025 = 2.525
        # USD TVL = (1000*10 + 1000*0.1) * 1 = 10100
        # APR = 2.525 * 8760 / 10100 * 100 = 219.0 — the SYMMETRIC answer:
        # the raw-unit math would have read (200*0.0025)*8760/2000*100 = 219
        # here too (coincidence of this fixture), so assert the DIRECTION
        # with a one-sided volume instead: all volume in AAA.
        rows = [dict(_snap(i, i * 100, 0)) for i in range(11)]
        self._write(rows)
        out = R.measure_fee_apr(POOL, price_map={"AAA": 10.0, "BBB": 0.1})
        # fees = 100*10*0.0025 = 2.5 USD/h; TVL = 10100 USD
        # APR = 2.5 * 8760 / 10100 * 100 = 216.83
        self.assertAlmostEqual(out["fee_apr_pct"], 216.832, places=2)
        # and the raw-unit figure would have been 100*0.0025*8760/2000*100
        # = 109.5 — assert we are NOT that.
        self.assertNotAlmostEqual(out["fee_apr_pct"], 109.5, places=1)

    def test_time_weighting_makes_a_long_gap_count_as_long(self):
        """P1-14 companion (Opus round 4): a 40-hour QUIET gap must weigh as
        40 quiet hours, not as one equal-weighted interval. (First draft of
        this fixture kept the volume rising across the gap — that interval
        then EARNED at a low rate, 5.475%/h, and time-weighting correctly
        gave (219*4 + 5.475*40)/44 = 24.886. A quiet gap holds volume
        constant.)"""
        rows = [_snap(0, 0, 0), _snap(1, 100, 100), _snap(2, 200, 200),
                _snap(3, 300, 300), _snap(43, 300, 300), _snap(44, 400, 400)]
        # intervals: 1h earn, 1h earn, 1h earn, 40h QUIET (d=0), 1h earn
        self._write(rows)
        out = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
        # earning 1h intervals: (200 * 0.0025) * 8760 / 2000 * 100 = 219.0
        # time-weighted: (219*1 + 219*1 + 219*1 + 0*40 + 219*1) / 44 = 19.909
        self.assertAlmostEqual(out["fee_apr_pct"],
                               (219.0 * 4 + 0.0 * 40) / 44.0, places=1)
        # the equal-weighted figure would have been 219*4/5 = 175.2
        self.assertLess(out["fee_apr_pct"], 30.0)

    def test_unpriced_intervals_are_counted_not_valued(self):
        """An interval whose token prices are missing is UNKNOWN — skipped
        and counted, never valued at a fabricated rate."""
        rows = self._series(11)
        self._write(rows)
        out = R.measure_fee_apr(POOL, price_map={"AAA": 1.0})   # BBB unpriced
        self.assertIsNone(out, "no priced intervals -> UNMEASURABLE, not 0")

    def test_earned_totals_are_the_sum_over_contributing_intervals(self):
        self._write(self._series(11))
        out = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
        # 10 intervals x 100 volume x 0.0025 fee x share 1
        self.assertAlmostEqual(out["earned_base"], 2.5, places=9)
        self.assertAlmostEqual(out["earned_quote"], 2.5, places=9)

    def test_share_fraction_scales_the_earnings_and_the_tvl_alike(self):
        """Our entitlement, not the pool's whole flow."""
        third = self._series(11, share_num=1, share_den=3)
        self._write(third)
        out = R.measure_fee_apr(POOL, price_map={"AAA": 1.0, "BBB": 1.0})
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
                self.assertIsNotNone(R.measure_fee_apr(
                    pool, price_map={"A": 1.0, "B": 1.0, "C": 1.0,
                                     "D": 1.0, "E": 1.0, "F": 1.0}))
        finally:
            builtins.open = real_open
        self.assertEqual(len(reads), 1, f"store read {len(reads)}x, want 1")


if __name__ == "__main__":
    unittest.main()