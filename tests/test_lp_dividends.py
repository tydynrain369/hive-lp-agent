"""Tests for dividend knowledge and our-share dividend sizing.

The controlling requirement from Tydyn: "just make sure that it's our share of
the total tick payout." A whole-pool figure presented as our income is the
dangerous error, so these tests hammer that case specifically.

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
      tests.test_lp_dividends -v
"""
import sys
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
from lp_dividends import (PROGRAMS, annotate_pool, expected_dividend_usd,
                          is_program_active, programs_for_pool)

PRICES = {"SWAP.HBD": 0.62, "HIVE": 0.0564, "HSBIDAO": 0.02899, "SWAP.HIVE": 0.0564}


class TestPrograms(unittest.TestCase):

    def test_cent_pattern_matches_cent_pools(self):
        self.assertTrue(programs_for_pool("CENT:POB"))
        self.assertEqual(programs_for_pool("CENT:POB")[0]["reward_token"], "SWAP.HBD")

    def test_does_not_leak_across_patterns(self):
        self.assertFalse([p for p in programs_for_pool("ALIVE:HEARTBEAT")
                          if p["reward_token"] == "SWAP.HBD"])

    def test_thresholds_are_flagged_approximate(self):
        self.assertTrue(PROGRAMS["PIMP_HIVE"]["threshold_approx"])
        self.assertFalse(PROGRAMS["CENT_SWAP.HBD"]["threshold_approx"])

    def test_every_program_records_provenance(self):
        for k, p in PROGRAMS.items():
            self.assertTrue(p["source"], f"{k} has no source")
            self.assertIn("Tydyn", p["source"])
            self.assertFalse(p["verified_on_chain"],
                             f"{k} must not claim chain verification")


class TestActivationFromChain(unittest.TestCase):

    def test_active_when_a_batch_pays_the_token(self):
        ok, why = is_program_active("CENT:POB", "SWAP.HBD",
                                    {("CENT:POB", "SWAP.HBD")})
        self.assertTrue(ok)

    def test_inactive_when_no_batch_names_it(self):
        ok, _ = is_program_active("CENT:POB", "SWAP.HBD", {("OTHER:X", "SWAP.HBD")})
        self.assertFalse(ok)

    def test_unknown_without_data_never_assumes_active(self):
        ok, why = is_program_active("CENT:POB", "SWAP.HBD", None)
        self.assertIsNone(ok)
        self.assertIn("unknown", why)


class TestOurShareOnly(unittest.TestCase):
    """The requirement: OUR share, never the whole pool."""

    def test_dividend_is_scaled_by_our_fraction(self):
        r = expected_dividend_usd("SWAP.HBD", per_tick_usd=0.564,
                                  period_hours=24, our_share_fraction=0.01,
                                  threshold_reached=True)
        self.assertTrue(r["computable"])
        self.assertAlmostEqual(r["usd_per_tick_our_share"], 0.00564, places=10)

    def test_missing_share_fraction_refuses_rather_than_quoting_whole_pool(self):
        r = expected_dividend_usd("SWAP.HBD", per_tick_usd=0.564,
                                  period_hours=24, our_share_fraction=None,
                                  threshold_reached=True)
        self.assertFalse(r["computable"])
        self.assertIsNone(r["usd_per_tick_our_share"])
        self.assertIn("whole-pool", r["reason"])

    def test_zero_share_fraction_refuses(self):
        r = expected_dividend_usd("SWAP.HBD", 0.564, 24, 0.0, threshold_reached=True)
        self.assertFalse(r["computable"])

    def test_unactivated_program_pays_nothing(self):
        for flag in (None, False):
            r = expected_dividend_usd("SWAP.HBD", 0.564, 24, 0.01,
                                      threshold_reached=flag)
            self.assertFalse(r["computable"], f"threshold_reached={flag}")
            self.assertIsNone(r["usd_per_tick_our_share"])

    def test_period_uses_the_declared_one_tick_per_day(self):
        """Tydyn 2026-10-02: each distribution tick happens once per day.

        numTicks (365/777) is the number of ticks IN THE BATCH, not the tick
        length, so one-per-day is a declared equivalence rather than something
        derivable from numTicks. A batch-supplied tick_hours overrides it.
        """
        # No batch tick length -> declared one-per-day -> 7 ticks in a week.
        r2 = expected_dividend_usd("SWAP.HBD", 0.564, 168, 0.01,
                                   threshold_reached=True)
        self.assertAlmostEqual(r2["usd_per_period_our_share"],
                               0.00564 * 7, places=10)
        self.assertIn("declared", r2["tick_hours_source"])
        self.assertAlmostEqual(r2["ticks_per_period"], 7.0, places=10)

        # An explicit 24h tick agrees with the equivalence.
        r3 = expected_dividend_usd("SWAP.HBD", 0.564, 168, 0.01,
                                   threshold_reached=True, tick_hours=24.0)
        self.assertAlmostEqual(r3["usd_per_period_our_share"],
                               0.00564 * 7, places=10)
        self.assertEqual(r3["tick_hours_source"], "batch")

        # A batch saying otherwise (1h ticks) must win over the equivalence.
        r4 = expected_dividend_usd("SWAP.HBD", 0.564, 168, 0.01,
                                   threshold_reached=True, tick_hours=1.0)
        self.assertAlmostEqual(r4["usd_per_period_our_share"],
                               0.00564 * 168, places=10)

    def test_no_period_stays_per_tick(self):
        r2 = expected_dividend_usd("HSBIDAO", 0.1, None, 0.01, threshold_reached=True)
        self.assertIsNone(r2["usd_per_period_our_share"])
        self.assertIsNotNone(r2["usd_per_tick_our_share"])

    def test_our_share_is_always_far_below_the_pool_figure(self):
        """Guard against the whole-pool number leaking through."""
        pool = 0.564
        r = expected_dividend_usd("SWAP.HBD", pool, 24, 1e-6, threshold_reached=True)
        self.assertLess(r["usd_per_tick_our_share"], pool)


class TestAnnotate(unittest.TestCase):

    def test_annotate_reports_declared_vs_observed(self):
        entries = [{"mined_token": "SWAP.HBD", "daily_reward": 0.0649}]
        a = annotate_pool("CENT:POB", entries, PRICES)
        self.assertEqual(a["declared_but_not_observed"], [])
        self.assertIn("SWAP.HBD", a["observed_reward_value"]["distinct_tokens"])

    def test_annotate_does_not_invent_unobserved_rewards(self):
        a = annotate_pool("CENT:POB", [], PRICES)
        self.assertEqual(a["observed_reward_value"]["distinct_tokens"], [])
        self.assertIn("SWAP.HBD", a["declared_but_not_observed"])

    def test_annotate_surfaces_undeclared_tokens(self):
        entries = [{"mined_token": "MYSTERY", "daily_reward": 1.0}]
        a = annotate_pool("CENT:POB", entries, PRICES)
        self.assertEqual(a["observed_but_undeclared"], ["MYSTERY"])

    def test_annotate_carries_the_not_verified_warning(self):
        a = annotate_pool("CENT:POB", [], PRICES)
        self.assertIn("NOT chain-verified", a["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)