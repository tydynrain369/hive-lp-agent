"""Tests for the market-price fallback.

The anchor is the token Tydyn checked by hand: BBHO, which he said has many buy
and sell orders. If this module cannot return a real price for BBHO it has
failed, regardless of what the other tests say.

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
      tests.test_lp_market_price -v
"""
import sys
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import lp_market_price as M


class TestFieldShape(unittest.TestCase):
    """The mistake that caused the whole problem: a nested key that is not there.

    Tested by BEHAVIOUR, not by asserting on docstring prose — a docstring
    assertion proves nothing about whether the code reads the right field.
    """

    def test_price_is_read_from_the_row_and_uses_the_bid(self):
        """The row's price is read from the top level, and we price on the BID.

        Do NOT assert an exact figure: the order book MOVES (BBHO's bid was
        0.04452762, then 0.0445225, then equal to lastPrice on a later call).
        A hard-coded price fails for reasons unrelated to the code. Assert the
        SHAPE and the relationships, which are the invariants that matter.
        """
        m = M.market_price("BBHO")
        self.assertIsNotNone(m)
        self.assertGreater(m["price"], 0)
        self.assertGreater(m["lastPrice"], 0)
        # 2026-11-02: price is USD, highest_bid/lowest_ask/lastPrice are the
        # RAW SWAP.HIVE book figures (all HE books quote in SWAP.HIVE). They
        # are NOT comparable across units — price = price_hive * hive_anchor.
        # Assert the SHAPE and the unit contract instead of a cross-unit equality.
        self.assertIsNotNone(m["price_hive"])
        self.assertGreater(m["price_hive"], 0)
        self.assertEqual(m["price_hive"], m["highest_bid"],
                         "the exit price basis is the highest bid")
        self.assertGreater(m["hive_anchor"], 0)
        self.assertAlmostEqual(m["price"], m["price_hive"] * m["hive_anchor"],
                               places=9,
                               msg="price (USD) must equal price_hive * hive_anchor")

    def test_params_are_not_list_wrapped(self):
        """A list-wrapped params returns zero rows SILENTLY, which looks like
        'no market'. Guard the shape so that regression cannot return."""
        import inspect
        src = inspect.getsource(M._find)
        self.assertNotIn('"params": [', src,
                        "params must be the query object directly, not a list")


class TestMarketPrice(unittest.TestCase):

    def test_bbho_has_a_real_price(self):
        """Tydyn checked this by hand — BBHO has many buy and sell orders."""
        m = M.market_price("BBHO")
        self.assertIsNotNone(m, "BBHO must resolve to a real market price")
        self.assertGreater(m["price"], 0)
        self.assertGreater(m["volume"], 0)
        # Both sides of the book exist, so a spread is computable.
        self.assertIsNotNone(m["highest_bid"])
        self.assertIsNotNone(m["lowest_ask"])

    def test_fresa_price_is_positive(self):
        """FRESA has only sell orders, but still records a lastPrice."""
        m = M.market_price("FRESA")
        self.assertIsNotNone(m)
        self.assertGreater(m["lastPrice"], 0)

    def test_dust_tokens_are_priced_at_near_zero_not_missing(self):
        """HOP/IDD/WRD are priced — just at effectively zero.

        2026-11-02: lastPrice is the RAW SWAP.HIVE book figure, so the dust
        threshold is in HIVE units, not USD. The exact cutoff is NOT pinned —
        the book moves (HOP printed 1.0e-6, then 1.3e-6) and a hard 1e-6 fails
        for reasons unrelated to the code. The invariant is that these tokens
        are priced NEAR zero relative to a liquid token and are unsellable.
        """
        for t in ("HOP", "IDD", "WRD"):
            m = M.market_price(t)
            self.assertIsNotNone(m, f"{t} should resolve, not be missing")
            self.assertGreater(m["lastPrice"], 0)
            # Near-zero relative to a liquid token (BBHO ~0.05 HIVE).
            self.assertLess(m["lastPrice"], 1e-3,
                            f"{t} is dust-priced in HIVE, not liquid")
            self.assertFalse(m["sizeable"], f"{t} must not be sizeable")

    def test_unknown_symbol_returns_none(self):
        self.assertIsNone(M.market_price("DEFINITELY_NOT_A_TOKEN_XYZ"))

    def test_empty_symbol_returns_none(self):
        self.assertIsNone(M.market_price(""))

    def test_result_carries_provenance_and_liquidity(self):
        m = M.market_price("BBHO")
        self.assertEqual(m["source"], "market.metrics")
        self.assertIn("exit price", m["basis"])
        for k in ("price", "lastPrice", "highest_bid", "lowest_ask",
                  "spread_pct", "liquidity", "sizeable"):
            self.assertIn(k, m)


class TestBidIsTheExitPrice(unittest.TestCase):
    """Tydyn 2026-10-02: price from the highest BID, not lastPrice.

    "Pool prices are usually derived from the highest buy order, and lowest
    sell order... Healthy tokens have a narrow difference between buy and sell
    orders, while those that are not doing well tend to have very divergent buy
    and sell orders."
    """

    def test_tight_spread_token_prices_on_the_bid(self):
        """BBH has a tight spread - verify bid is used for pricing and liquidity is tight."""
        m = M.market_price("BBH")
        self.assertEqual(m["liquidity"], "tight")
        self.assertTrue(m["sizeable"])
        # 2026-11-02: price is USD; highest_bid is the raw HIVE book figure.
        # The invariant is that the exit basis IS the bid (price_hive == bid),
        # not a cross-unit equality.
        self.assertEqual(m["price_hive"], m["highest_bid"],
                         "exit price basis is the highest bid")
        # And bid must be <= ask
        self.assertLessEqual(m["highest_bid"], m["lowest_ask"])
        # Spread must be tight (<= 10%)
        self.assertLessEqual(m["spread_pct"], 10.0)

    def test_no_bid_falls_back_to_lastprice_and_refuses_sizing(self):
        """SYNTHETIC (2026-10-10): this test was born reading the LIVE FRESA
        book, which then had an ask and no bid. On 2026-10-09 FRESa GAINED a
        bid (2.50/3.00, ~18% spread -> wide/sizeable) and the test failed —
        the market moved, not the code. A market-state snapshot is not an
        invariant; feed the reader a synthetic no-bid row so the LOGIC is
        what is pinned, whatever any real book does.
        """
        row = {"symbol": "SYNTH_NOBID", "lastPrice": "0.08",
               "highestBid": "0", "lowestAsk": "0.1", "volume": "1"}
        real_find = M._find
        M._find = lambda sym: [row] if sym == "SYNTH_NOBID" else real_find(sym)
        try:
            m = M.market_price("SYNTH_NOBID")
        finally:
            M._find = real_find
        self.assertEqual(m["liquidity"], "no-bid")
        self.assertFalse(m["sizeable"], "a token with no bid cannot be exited")
        # 2026-11-02: the synthetic row is HIVE-denominated (as all real books
        # are), so lastPrice IS price_hive and price = lastPrice * anchor.
        self.assertEqual(m["price_hive"], m["lastPrice"],
                         "no-bid fallback: price_hive is lastPrice")
        self.assertAlmostEqual(m["price"], m["lastPrice"] * m["hive_anchor"],
                               places=9)
        self.assertIn("NO BID", m["basis"])

    def test_spread_band_matches_the_measured_spread(self):
        """Band classification must agree with the spread it measured.

        2026-10-04: this asserted BBHO was 'wide' from a 41.7% snapshot taken
        days earlier. BBHO's book has since tightened to 0.0% and the test failed
        because the MARKET moved, not because the classifier broke. Same class as
        the earlier exact-price assertion. Assert the INVARIANT — the band is
        derived from the spread actually measured — so it holds whatever the book
        is doing today.
        """
        for tok in ("BBHO", "BBH", "HOP"):
            m = M.market_price(tok)
            if not m:
                continue
            sp = m["spread_pct"]
            band = m["liquidity"]
            if band == "tight":
                self.assertLessEqual(sp, 10.0, tok)
            elif band == "wide":
                self.assertGreater(sp, 10.0, tok)
                self.assertLessEqual(sp, 50.0, tok)
            elif band == "illiquid":
                self.assertGreater(sp, 50.0, tok)
                self.assertFalse(m["sizeable"], tok)
            else:
                self.fail(f"{tok}: unknown band {band!r}")

    def test_two_hundred_percent_spread_is_illiquid(self):
        # 2026-11-02: a token with NO bid is unsellable, which is a stronger
        # condition than illiquid. The test pins "not sizeable" — the invariant
        # that matters — and accepts either no-bid or illiquid as the band.
        for t in ("HOP", "IDD", "WRD", "BRN"):
            m = M.market_price(t)
            self.assertIn(m["liquidity"], ("no-bid", "illiquid"), t)
            self.assertFalse(m["sizeable"], t)

    def test_spread_is_computed_between_bid_and_ask(self):
        m = M.market_price("BBH")
        bid, ask = m["highest_bid"], m["lowest_ask"]
        expected = ((ask - bid) / ((ask + bid) / 2.0)) * 100
        self.assertAlmostEqual(m["spread_pct"], expected, places=2)


class TestEnrich(unittest.TestCase):

    def test_fills_only_zero_prices(self):
        existing = {"CENT": 0.0000549, "BBHO": 0}
        out, added, skipped = M.enrich_price_map(existing)
        self.assertEqual(out["CENT"], 0.0000549,
                         "a pool-derived price must never be overwritten")
        self.assertIn("BBHO", added)
        self.assertGreater(out["BBHO"], 0)

    def test_enrichment_preserves_liquidity_verdict(self):
        """SYNTHETIC (2026-10-10): was live-FRESA; the book moved (a bid
        appeared, spread ~18% = wide/sizeable) and broke the assumption.
        Feed enrich a synthetic no-bid row and assert the liquidity verdict
        travels through to the enrichment result unchanged."""
        row = {"symbol": "SYNTH_NOBID2", "lastPrice": "0.08",
               "highestBid": "0", "lowestAsk": "0.1", "volume": "1"}
        real_find = M._find
        M._find = lambda sym: [row] if sym == "SYNTH_NOBID2" else real_find(sym)
        try:
            out, added, _ = M.enrich_price_map({"SYNTH_NOBID2": 0})
        finally:
            M._find = real_find
        self.assertIn("SYNTH_NOBID2", out)
        self.assertFalse(added["SYNTH_NOBID2"]["sizeable"],
                         "an unsellable token must not pass as sizeable")

    def test_returns_all_three_buckets(self):
        out, added, skipped = M.enrich_price_map({"CENT": 1.0})
        self.assertIsInstance(out, dict)
        self.assertIsInstance(added, dict)
        self.assertIsInstance(skipped, list)

    def test_does_not_mutate_the_input(self):
        existing = {"BBHO": 0}
        M.enrich_price_map(existing)
        self.assertEqual(existing["BBHO"], 0, "input map must not be mutated")


if __name__ == "__main__":
    unittest.main(verbosity=2)