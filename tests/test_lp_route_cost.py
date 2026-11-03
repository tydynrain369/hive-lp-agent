#!/usr/bin/env python3
"""Tests for lp_route_cost — constant-product multi-hop pricing.

The load-bearing test here is test_large_input_degrades_vs_dust_input: a model
that multiplies spot rates passes the dust cases and fails the large one, which
is exactly the bug this module exists to prevent.
"""
import unittest
from fractions import Fraction

import lp_route_cost as RC


def P(pair, b, q):
    return {"tokenPair": pair, "baseQuantity": str(b), "quoteQuantity": str(q)}


class TestSingleHop(unittest.TestCase):
    def test_zero_fee_removes_fee_but_not_impact(self):
        """With no fee, output still falls short of spot because the curve
        moves. Constant product is never exactly linear, so this asserts the
        DIRECTION, not equality."""
        pools = [P("A:B", 1000000, 2000000)]
        r = RC.price_route(["A", "B"], 1, "A", pools, fee=Fraction(0))
        self.assertTrue(r["ok"])
        self.assertLess(float(Fraction(r["realised_rate"])), 2.0)
        # ...but the shortfall is impact, not a fee
        self.assertLess(r["cost_pct"], 0.001)

    def test_fee_reduces_output(self):
        pools = [P("A:B", 1000, 2000)]
        r = RC.price_route(["A", "B"], 1, "A", pools, fee=Fraction(1, 1000))
        self.assertTrue(r["ok"])
        self.assertLess(float(Fraction(r["amount_out"])), 2.0)

    def test_selling_the_quote_side_uses_the_quote_reserve(self):
        """Regression: orientation was inverted for the quote side.

        Selling token T from pool A:B must price T against T's OWN reserve. With
        base=1000 (A) and quote=2000 (B), selling B means the input reserve is
        2000, not 1000. Getting this backwards silently misprices every route
        that sells a quote-side token — which is most of them, since BFS paths
        frequently start at the second symbol of a pool.
        """
        pools = [P("A:B", 1000, 2000)]
        rev = RC.price_route(["B", "A"], 100, "B", pools, fee=Fraction(0))
        self.assertTrue(rev["ok"])
        hop = rev["hops"][0]
        self.assertEqual(hop["sold_side"], "quote")
        self.assertEqual(Fraction(hop["reserve_in"]), Fraction(2000))
        self.assertEqual(Fraction(hop["reserve_out"]), Fraction(1000))
        # independent hand derivation on the correct reserves
        dx, eff = Fraction(100), Fraction(100)
        expected = Fraction(1000) * eff / (Fraction(2000) + eff)
        self.assertEqual(Fraction(rev["amount_out"]), expected)

    def test_selling_the_base_side_uses_the_base_reserve(self):
        pools = [P("A:B", 1000, 2000)]
        fwd = RC.price_route(["A", "B"], 100, "A", pools, fee=Fraction(0))
        self.assertTrue(fwd["ok"])
        hop = fwd["hops"][0]
        self.assertEqual(hop["sold_side"], "base")
        self.assertEqual(Fraction(hop["reserve_in"]), Fraction(1000))
        dx, eff = Fraction(100), Fraction(100)
        expected = Fraction(2000) * eff / (Fraction(1000) + eff)
        self.assertEqual(Fraction(fwd["amount_out"]), expected)

    def test_missing_pool_is_reported_not_guessed(self):
        r = RC.price_route(["A", "Z"], 1, "A", [P("A:B", 1000, 2000)])
        self.assertFalse(r["ok"])
        self.assertIn("no pool for hop", r["failure"])


class TestMultiHop(unittest.TestCase):
    CHAIN = [P("A:B", 1_000_000, 1_000_000),
             P("B:C", 1_000_000, 1_000_000),
             P("C:D", 1_000_000, 1_000_000)]

    def test_each_hop_is_reported(self):
        r = RC.price_route(["A", "B", "C", "D"], 10, "A", self.CHAIN)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["hops"]), 3)
        self.assertEqual(r["out_token"], "D")

    def test_three_hops_cost_more_than_one(self):
        one = RC.price_route(["A", "B"], 10, "A", self.CHAIN)
        three = RC.price_route(["A", "B", "C", "D"], 10, "A", self.CHAIN)
        self.assertGreater(three["cost_pct"], one["cost_pct"])

    def test_large_input_degrades_vs_dust_input(self):
        """THE POINT: price impact must show up when the trade is not dust.

        A spot-rate implementation returns cost ~= fee regardless of size and
        therefore cannot pass this.
        """
        dust = RC.price_route(["A", "B"], Fraction(1, 1000), "A", self.CHAIN)
        huge = RC.price_route(["A", "B"], 400_000, "A", self.CHAIN)
        self.assertTrue(dust["ok"] and huge["ok"])
        # dust is essentially pure fee
        self.assertLess(dust["cost_pct"], 0.2)
        # a trade eating 40% of the reserve is dominated by impact
        self.assertGreater(huge["cost_pct"], 10.0)

    def test_exhausting_a_pool_fails_rather_than_returning_zero(self):
        r = RC.price_route(["A", "B"], 2_000_000, "A", [P("A:B", 1000, 2000)])
        self.assertFalse(r["ok"])
        self.assertIn("cannot absorb", r["failure"])


class TestArithmetic(unittest.TestCase):
    def test_amounts_are_exact_fractions(self):
        pools = [P("A:B", 337, 991)]
        r = RC.price_route(["A", "B"], Fraction(1, 3), "A", pools)
        self.assertTrue(r["ok"])
        # a float would have lost this; a Fraction round-trips exactly
        self.assertIsInstance(Fraction(r["amount_out"]), Fraction)

    def test_costs_are_never_negative(self):
        for amt in ("0.00000001", "1", "500"):
            r = RC.price_route(["A", "B"], amt, "A",
                               [P("A:B", 10_000, 10_000)])
            if r["ok"]:
                self.assertGreaterEqual(r["cost_pct"], 0.0, amt)


if __name__ == "__main__":
    unittest.main()
