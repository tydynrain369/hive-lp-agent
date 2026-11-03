"""Tests for realisable-vs-gross dividend.

Tydyn 2026-10-02: "Healthy tokens have a narrow difference between buy and sell
orders, while those that are not doing well tend to have very divergent buy and
sell orders." A dividend paid in tokens you cannot sell is not income, so the two
must be kept apart before any capital is sized on it.

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
      tests.test_realisable_dividend -v
"""
import sys
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
from lp_sizing import (dividend_is_income, realisable_dividend,
                        MIN_REALISABLE_DIVIDEND_FRACTION)

TIGHT, WIDE, ILLIQ = 0.03, 0.30, 0.95


class TestRealisableSplit(unittest.TestCase):

    def test_all_tight_is_fully_realisable(self):
        r = realisable_dividend(
            {"A": {"usd": 1.0}, "B": {"usd": 2.0}},
            {"A": TIGHT, "B": TIGHT})
        self.assertAlmostEqual(r["realisable_usd"], 3.0, places=9)
        self.assertAlmostEqual(r["illiquid_usd"], 0.0, places=9)
        self.assertAlmostEqual(r["realisable_fraction"], 1.0, places=9)

    def test_all_illiquid_is_zero_realisable(self):
        r = realisable_dividend({"A": {"usd": 1.0}}, {"A": ILLIQ})
        self.assertEqual(r["realisable_usd"], 0.0)
        self.assertAlmostEqual(r["gross_usd"], 1.0, places=9)
        self.assertEqual(r["realisable_fraction"], 0.0)

    def test_gross_always_equals_the_parts(self):
        """A split that does not add up is worse than no split."""
        r = realisable_dividend(
            {"A": {"usd": 1.0}, "B": {"usd": 2.0}, "C": {"usd": 4.0}},
            {"A": TIGHT, "B": WIDE, "C": ILLIQ})
        parts = r["realisable_usd"] + r["illiquid_usd"] + r["unknown_usd"]
        self.assertAlmostEqual(r["gross_usd"], parts, places=9)

    def test_missing_spread_is_unknown_not_assumed_sizeable(self):
        """An absent liquidity reading is not evidence of a tight book."""
        r = realisable_dividend({"A": {"usd": 1.0}}, {})
        self.assertEqual(r["realisable_usd"], 0.0)
        self.assertAlmostEqual(r["unknown_usd"], 1.0, places=9)
        self.assertIsNone(r["realisable_fraction"])

    def test_unpriced_token_is_not_counted_as_zero_value(self):
        r = realisable_dividend(
            {"A": {"usd": None}, "B": {"usd": 1.0}}, {"B": TIGHT})
        self.assertEqual(r["per_token"]["A"]["class"], "unpriced")
        self.assertAlmostEqual(r["gross_usd"], 1.0, places=9)

    def test_empty_input_is_not_a_crash(self):
        r = realisable_dividend({}, {})
        self.assertEqual(r["gross_usd"], 0.0)
        self.assertIsNone(r["realisable_fraction"])


class TestCountsAsIncome(unittest.TestCase):

    def test_mostly_tight_dividend_counts(self):
        r = dividend_is_income(
            "P:A", {"P:A": [{"mined_token": "T", "daily_reward": 100.0}]},
            {"T": 1.0}, {"T": TIGHT})
        self.assertTrue(r["counts_as_income"])
        self.assertAlmostEqual(r["realisable_fraction"], 1.0, places=9)

    def test_mostly_illiquid_dividend_does_not_count(self):
        r = dividend_is_income(
            "P:A", {"P:A": [{"mined_token": "I", "daily_reward": 100.0}]},
            {"I": 1.0}, {"I": ILLIQ})
        self.assertFalse(r["counts_as_income"],
                         "a dividend you cannot sell is not income")
        self.assertIn("sellable", r["reason"])

    def test_mixed_dividend_below_threshold_is_rejected(self):
        """One rich sellable token must not launder a wall of illiquid ones."""
        r = dividend_is_income(
            "P:A", {"P:A": [{"mined_token": "T", "daily_reward": 100.0},
                            {"mined_token": "I", "daily_reward": 10000.0}]},
            {"T": 1.0, "I": 1.0}, {"T": TIGHT, "I": ILLIQ})
        self.assertFalse(r["counts_as_income"])
        self.assertLess(r["realisable_fraction"], 0.5)

    def test_unpriced_dividend_never_counts(self):
        r = dividend_is_income(
            "P:A", {"P:A": [{"mined_token": "MYSTERY", "daily_reward": 100.0}]},
            {}, {})
        self.assertFalse(r["counts_as_income"])
        self.assertIn("not comparable", r["reason"])

    def test_threshold_is_a_named_tunable(self):
        self.assertGreater(MIN_REALISABLE_DIVIDEND_FRACTION, 0)
        self.assertLessEqual(MIN_REALISABLE_DIVIDEND_FRACTION, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)