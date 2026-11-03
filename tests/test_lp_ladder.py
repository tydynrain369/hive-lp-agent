"""Scale-generic property tests for the LP priority ladder (lp_ladder.py).

PURPOSE
  Prove the water-filling ladder is correct from DUST ($1e-8) through large
  sums ($1e6), under the policy Tydyn chose 2026-11-02: marginal-rate
  water-filling — fill the top rung until its marginal rate meets the next,
  then spill down; never lower the blended return.

NO CAPITAL. NO CHAIN. Pure-function tests against lp_ladder.

Run:
  ~/hive-agent-test/.venv/bin/python -m pytest tests/test_lp_ladder.py -q
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import lp_ladder as LL


def rung(pool, rate0, tvl, deployed=0.0, cap=None):
    return {"pool": pool, "rate0": rate0, "tvl_usd": tvl,
            "deployed_usd": deployed, "cap_usd": cap}


class TestMarginalRate(unittest.TestCase):
    def test_units_hand_check(self):
        # fee 5.173%/yr -> per day fraction ; reward 1.0 USD/day/HIVE at
        # HIVE=$0.046 -> 1/0.046 USD/day per USD deposited
        d = LL.marginal_rate_detail(5.173, 1.0, 0.046)
        self.assertAlmostEqual(d["fee"], 5.173 / 100.0 / 365.0, places=12)
        self.assertAlmostEqual(d["reward"], 1.0 / 0.046, places=9)
        self.assertAlmostEqual(d["rate"], d["fee"] + d["reward"], places=12)
        self.assertTrue(d["complete"])

    def test_all_unknown_is_none_not_zero(self):
        d = LL.marginal_rate_detail(None, None, 0.046)
        self.assertIsNone(d["rate"])
        self.assertFalse(d["complete"])
        self.assertIsNone(LL.marginal_rate(None, None, 0.046))

    def test_partial_component_flagged(self):
        d = LL.marginal_rate_detail(5.0, None, 0.046)
        self.assertFalse(d["complete"])
        self.assertIsNotNone(d["rate"])
        self.assertAlmostEqual(d["rate"], 5.0 / 100.0 / 365.0, places=12)

    def test_zero_hive_usd_does_not_divide_by_zero(self):
        d = LL.marginal_rate_detail(5.0, 1.0, 0.0)
        self.assertIsNone(d["reward"])
        self.assertIsNotNone(d["rate"])  # fee still measured


class TestCleanRungs(unittest.TestCase):
    def test_unmeasured_rate_excluded_with_reason(self):
        usable, excluded = LL._clean_rungs([rung("A", None, 100)])
        self.assertEqual(usable, [])
        self.assertEqual(excluded[0]["pool"], "A")
        self.assertIn("unmeasured", excluded[0]["reason"])

    def test_unmeasured_tvl_excluded_not_zeroed(self):
        usable, excluded = LL._clean_rungs([rung("A", 0.5, None)])
        self.assertEqual(usable, [])
        self.assertTrue(any("TVL" in e["reason"] for e in excluded))

    def test_zero_and_negative_rate_excluded(self):
        usable, excluded = LL._clean_rungs([rung("A", 0.0, 100), rung("B", -1.0, 100)])
        self.assertEqual(usable, [])
        self.assertEqual(len(excluded), 2)

    def test_nonpositive_tvl_excluded(self):
        usable, _ = LL._clean_rungs([rung("A", 0.5, 0.0)])
        self.assertEqual(usable, [])


class TestWaterFill(unittest.TestCase):
    def test_allocation_sums_to_capital(self):
        rungs = [rung("A", 0.0010, 1000), rung("B", 0.0005, 2000), rung("C", 0.0002, 500)]
        out = LL.water_fill(rungs, 5000.0)
        self.assertAlmostEqual(sum(out["allocation"].values()), 5000.0, places=6)

    def test_equalizes_marginal_rate_over_active_set(self):
        rungs = [rung("A", 0.0010, 1000), rung("B", 0.0005, 2000)]
        out = LL.water_fill(rungs, 5000.0)
        A, _ = LL._clean_rungs(rungs)
        for r in A:  # active rungs' post-allocation rate == level
            x = out["allocation"][r["pool"]]
            if x > 0:
                self.assertAlmostEqual(LL._rate_at(r, x), out["level"], places=9)

    def test_beats_concentrate_all(self):
        rungs = [rung("A", 0.0010, 1000), rung("B", 0.0005, 1000)]
        out = LL.water_fill(rungs, 5000.0)
        A, _ = LL._clean_rungs(rungs)
        concentrate = LL.income_of(A[0], 5000.0)  # all into the best rung
        self.assertGreater(out["income_usd_per_day"], concentrate)

    def test_never_negative_or_nan_dust_scale(self):
        for cap in (1e-8, 1e-6, 1e-4, 0.01, 1.0, 1e6):
            rungs = [rung("A", 1e-5, 1e-3), rung("B", 1e-6, 1e3)]
            out = LL.water_fill(rungs, cap)
            for v in out["allocation"].values():
                self.assertGreaterEqual(v, 0.0)
                self.assertFalse(math.isnan(v))
            self.assertGreaterEqual(out["income_usd_per_day"], 0.0)

    def test_ladder_sorted_rate0_desc(self):
        rungs = [rung("A", 0.0002, 100), rung("B", 0.0010, 100), rung("C", 0.0005, 100)]
        out = LL.water_fill(rungs, 10.0)
        rates = [row["rate0"] for row in out["ladder"]]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_cap_respected(self):
        rungs = [rung("A", 0.0010, 1000, cap=100.0), rung("B", 0.0001, 1000)]
        out = LL.water_fill(rungs, 5000.0)
        self.assertLessEqual(out["allocation"]["A"], 100.0 + 1e-9)
        self.assertAlmostEqual(sum(out["allocation"].values()), 5000.0, places=6)

    def test_excluded_reported_not_in_allocation(self):
        rungs = [rung("A", 0.0010, 1000), rung("BAD", None, 1000)]
        out = LL.water_fill(rungs, 100.0)
        self.assertIn("A", out["allocation"])
        self.assertNotIn("BAD", out["allocation"])
        self.assertTrue(any(e["pool"] == "BAD" for e in out["excluded"]))


class TestNextDeploy(unittest.TestCase):
    def test_picks_highest_current_rate(self):
        rungs = [rung("A", 0.0010, 1000), rung("B", 0.0005, 1000)]
        out = LL.next_deploy(rungs, 100.0)
        self.assertEqual(out["pool"], "A")

    def test_deployed_dilution_can_flip_the_choice(self):
        # A has higher rate0 but is already heavily filled; B wins on CURRENT rate
        rungs = [rung("A", 0.0010, 1000, deployed=9000), rung("B", 0.0006, 1000)]
        out = LL.next_deploy(rungs, 100.0)
        self.assertEqual(out["pool"], "B")

    def test_crossover_meets_runner_up(self):
        rungs = [rung("A", 0.0010, 1000), rung("B", 0.0005, 1000)]
        out = LL.next_deploy(rungs, 100000.0)  # ample capital -> crossover binds
        self.assertAlmostEqual(out["rate_after"], out["runner_up_rate"], places=12)
        self.assertAlmostEqual(out["amount_usd"], 1000.0, places=6)

    def test_capital_binds_when_crossover_is_far(self):
        rungs = [rung("A", 0.0010, 1e9), rung("B", 0.0001, 1e9)]
        out = LL.next_deploy(rungs, 50.0)
        self.assertAlmostEqual(out["amount_usd"], 50.0, places=9)

    def test_single_rung_fills_to_capital(self):
        out = LL.next_deploy([rung("A", 0.0010, 1000)], 50.0)
        self.assertEqual(out["pool"], "A")
        self.assertAlmostEqual(out["amount_usd"], 50.0, places=9)

    def test_no_rungs_and_no_capital(self):
        self.assertIsNone(LL.next_deploy([], 100.0)["pool"])
        self.assertIsNone(LL.next_deploy([rung("A", 0.001, 100)], 0.0)["pool"])

    def test_deterministic_tie_break(self):
        rungs = [rung("B", 0.0010, 1000), rung("A", 0.0010, 1000)]
        self.assertEqual(LL.next_deploy(rungs, 10.0)["pool"], "A")

    def test_scale_generic_invariants(self):
        for cap in (1e-8, 1e-3, 1.0, 1e3, 1e6):
            rungs = [rung("A", 1e-4, 1000), rung("B", 1e-4, 1e6), rung("C", 1e-7, 10)]
            out = LL.next_deploy(rungs, cap)
            self.assertIn(out["pool"], {"A", "B"})
            self.assertGreaterEqual(out["amount_usd"], 0.0)
            self.assertLessEqual(out["amount_usd"], cap + 1e-12)


class TestState(unittest.TestCase):
    def test_roundtrip_and_accumulate(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ladder_state.json")
            LL.record_deploy("A", 1.5, path=p)
            LL.record_deploy("A", 2.5, path=p)
            st = LL.load_state(p)
            self.assertAlmostEqual(LL.deployed_for(st, "A"), 4.0, places=12)

    def test_missing_state_is_empty(self):
        self.assertEqual(LL.load_state("/nonexistent/xyz.json"), {})
        self.assertEqual(LL.deployed_for({}, "A"), 0.0)


if __name__ == "__main__":
    unittest.main()
