"""Tests for the Leg B sizing math.

Anchor case: CENT:POB, the live destination. CENT ~$0.0000549, POB ~$0.000136.

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
      tests.test_lp_sizing -v
"""
import sys
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
from lp_sizing import (SizingError, distinct_reward_tokens, fee_run_rate_usd_per_tick,
                       our_dividend_usd_per_tick, reward_value_per_tick, size_deposit)

PRICES = {"CENT": 0.0000549, "POB": 0.000136,
          "SWAP.HIVE": 0.0564, "GLITTER": 0.0000123,
          "HSBIDAO": 0.02899, "LBI": 0.0123, "THREE": 0.00000000012}


class Pool:
    """PoolData-ish double. Carries pool_id because reward_info is keyed by
    string — a double without it reproduces the silent-zero bug."""
    def __init__(self, total_shares, pool_id="P"):
        self.total_shares = total_shares
        self.pool_id = pool_id
        self.base, self.quote = pool_id.split(":", 1) if ":" in pool_id else ("P", "Q")


class Pos:
    def __init__(self, shares):
        self.shares = shares


class TestRewardTokens(unittest.TestCase):
    """The mined_token key fix."""

    def test_reads_mined_token_not_token(self):
        entries = [{"mined_token": "SWAP.HIVE", "daily_reward": 1.0},
                   {"mined_token": "GLITTER", "daily_reward": 2.0}]
        self.assertEqual(distinct_reward_tokens(entries), ["SWAP.HIVE", "GLITTER"])

    def test_dedupes_repeated_tokens(self):
        entries = [{"mined_token": "SWAP.HIVE"}, {"mined_token": "SWAP.HIVE"}]
        self.assertEqual(distinct_reward_tokens(entries), ["SWAP.HIVE"])

    def test_accepts_legacy_keys_too(self):
        entries = [{"token": "A"}, {"symbol": "B"}, {"mined_token": "C"}]
        self.assertEqual(distinct_reward_tokens(entries), ["A", "B", "C"])

    def test_empty_and_junk_are_safe(self):
        self.assertEqual(distinct_reward_tokens(None), [])
        self.assertEqual(distinct_reward_tokens([None, 5, "x"]), [])


class TestRewardValue(unittest.TestCase):
    """Value, not count — THREE at 1.35e9 must not equal GLITTER at 29."""

    def test_three_and_glitter_are_not_interchangeable(self):
        entries = [{"mined_token": "THREE", "daily_reward": 1.35e9},
                   {"mined_token": "GLITTER", "daily_reward": 29.1}]
        v = reward_value_per_tick(entries, PRICES)
        three = v["per_token"]["THREE"]["usd"]
        glitter = v["per_token"]["GLITTER"]["usd"]
        self.assertGreater(three, glitter,
                           "a count-based metric would call these identical")

    def test_unpriced_token_is_reported_not_zeroed(self):
        entries = [{"mined_token": "SWAP.HIVE", "daily_reward": 1.0},
                   {"mined_token": "MYSTERY", "daily_reward": 5.0}]
        v = reward_value_per_tick(entries, PRICES)
        self.assertFalse(v["comparable"])
        self.assertIn("MYSTERY", v["unpriced_tokens"])
        self.assertIsNone(v["per_token"]["MYSTERY"]["usd"])

    def test_distinct_token_count_is_exposed(self):
        entries = [{"mined_token": "SWAP.HIVE", "daily_reward": 1.0},
                   {"mined_token": "SWAP.HIVE", "daily_reward": 2.0},
                   {"mined_token": "GLITTER", "daily_reward": 3.0}]
        self.assertEqual(len(reward_value_per_tick(entries, PRICES)["distinct_tokens"]), 2)


class TestOurDividend(unittest.TestCase):

    def test_not_computable_without_total_shares(self):
        r = our_dividend_usd_per_tick(
            {"P": [{"mined_token": "SWAP.HIVE", "daily_reward": 1.0}]},
            Pool(0), Pos(10), PRICES)
        self.assertFalse(r["computable"])
        self.assertIn("total_shares", r["reason"])

    def test_share_is_our_fraction_of_the_pool(self):
        r = our_dividend_usd_per_tick(
            {"P": [{"mined_token": "SWAP.HIVE", "daily_reward": 10.0}]},
            Pool(1000), Pos(10), PRICES)          # 1% share
        self.assertTrue(r["computable"])
        self.assertAlmostEqual(r["share_fraction"], 0.01, places=6)
        self.assertAlmostEqual(r["usd_per_tick"], 10.0 * 0.0564 * 0.01, places=8)

    def test_unpriced_blocks_computation_rather_than_guessing(self):
        r = our_dividend_usd_per_tick(
            {"P": [{"mined_token": "MYSTERY", "daily_reward": 10.0}]},
            Pool(1000), Pos(10), PRICES)
        self.assertFalse(r["computable"])

    def test_zero_shares_not_computable(self):
        r = our_dividend_usd_per_tick(
            {"P": [{"mined_token": "SWAP.HIVE", "daily_reward": 1.0}]},
            Pool(1000), Pos(0), PRICES)
        self.assertFalse(r["computable"])


class TestFeeRunRate(unittest.TestCase):

    def test_linear_proportionality(self):
        # $1 over 100h is $0.01/h
        self.assertAlmostEqual(fee_run_rate_usd_per_tick(1.0, 100.0), 0.01, places=9)

    def test_unknown_window_returns_none(self):
        self.assertIsNone(fee_run_rate_usd_per_tick(1.0, 0))
        self.assertIsNone(fee_run_rate_usd_per_tick(None, 10.0))


class TestSizeDeposit(unittest.TestCase):

    def test_balanced_usd_split(self):
        s = size_deposit("CENT:POB", 0.01, PRICES)
        base_usd = s["base_amount"] * PRICES["CENT"]
        quote_usd = s["quote_amount"] * PRICES["POB"]
        self.assertAlmostEqual(base_usd, quote_usd, places=12)
        self.assertAlmostEqual(base_usd + quote_usd, s["deploy_usd"], places=12)

    def test_fee_reserve_is_held_back(self):
        s = size_deposit("CENT:POB", 0.01, PRICES, fee_reserve_ratio=0.5)
        self.assertAlmostEqual(s["deploy_usd"], 0.005, places=6)
        self.assertAlmostEqual(s["fee_reserve_usd"], 0.005, places=6)
        self.assertAlmostEqual(s["deploy_usd"] + s["fee_reserve_usd"], 0.01, places=6)

    def test_budget_below_floor_refuses(self):
        with self.assertRaises(SizingError) as c:
            size_deposit("CENT:POB", 0.0000001, PRICES)
        self.assertIn("management floor", str(c.exception))

    def test_missing_price_refuses_rather_than_guessing(self):
        with self.assertRaises(SizingError) as c:
            size_deposit("NEW:POB", 0.01, PRICES)
        self.assertIn("NEW", str(c.exception))
        self.assertIn("exchange ratios", str(c.exception))

    def test_malformed_pool_id_refuses(self):
        for bad in ("NOCOLON", "", ":QUOTE", "BASE:"):
            with self.assertRaises(SizingError):
                size_deposit(bad, 0.01, PRICES)

    def test_non_numeric_budget_refuses(self):
        with self.assertRaises(SizingError):
            size_deposit("CENT:POB", "lots", PRICES)

    def test_exchange_ratios_are_not_used_as_prices(self):
        """A pool whose USD prices are tiny must still produce sane amounts."""
        s = size_deposit("CENT:POB", 0.01, PRICES)
        self.assertGreater(s["base_amount"], 0)
        self.assertGreater(s["quote_amount"], 0)
        # CENT is a sub-cent token, so a $0.0025 side legitimately buys ~45
        # units. The invariant that matters is the USD identity, not a count:
        # using the pool's exchange ratio (~4.0) as a price would break it.
        self.assertAlmostEqual(s["base_amount"] * PRICES["CENT"],
                               s["quote_amount"] * PRICES["POB"], places=12)
        self.assertAlmostEqual(
            s["base_amount"] * PRICES["CENT"] + s["quote_amount"] * PRICES["POB"],
            s["deploy_usd"], places=9)

    def test_result_carries_prices_used(self):
        s = size_deposit("CENT:POB", 0.01, PRICES)
        self.assertEqual(s["prices_used"]["CENT"], PRICES["CENT"])

    def test_live_shaped_amounts(self):
        s = size_deposit("CENT:POB", 0.001, PRICES)
        self.assertTrue(0 < s["base_amount"] < 1000)
        self.assertTrue(0 < s["quote_amount"] < 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)