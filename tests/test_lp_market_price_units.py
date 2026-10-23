"""Units regression tests for lp_market_price (the ~19x defect pin).

2026-10-17 live lesson: every Hive Engine market figure is
SWAP.HIVE-denominated (the books quote in SWAP.HIVE), but consumers
downstream priced in USD — a silent 1/HIVE-price inflation (~19x at
$0.0524): the deploy sizing quoted CENT at "$0.0011" when the executable
truth was 0.0011 HIVE = $0.0000577.

market_price() now returns USD in "price" (converted via the live
HIVE/USD anchor) and keeps the raw book figure as "price_hive"; when the
anchor is unavailable NO USD figure is returned — never fabricate one.

These tests pin exactly that contract, hermetically (_find and the anchor
are monkeypatched; no network).
"""
import unittest
from unittest import mock

import lp_market_price as mp


def _book_row(last="0.0011", bid="0.0011", ask="0.0012", vol="1000"):
    """A market.metrics row shaped like the live RPC's (string-typed)."""
    return [{"symbol": "CENT", "lastPrice": last, "highestBid": bid,
             "lowestAsk": ask, "volume": vol}]


class TestMarketPriceUnits(unittest.TestCase):
    def setUp(self):
        # Hermetic: the two network seams of market_price().
        self.find_patch = mock.patch.object(mp, "_find", return_value=_book_row())
        self.anchor_patch = mock.patch.object(
            mp, "_hive_anchor_usd", return_value=0.05)
        self.find_patch.start()
        self.anchor_patch.start()
        self.addCleanup(self.find_patch.stop)
        self.addCleanup(self.anchor_patch.stop)

    def test_price_is_usd_via_anchor_not_the_raw_book_figure(self):
        m = mp.market_price("CENT")
        self.assertIsNotNone(m)
        # THE 19x PIN: 0.0011 HIVE bid * $0.05 anchor = $0.000055 —
        # NOT 0.0011 (the pre-fix silent unit-blind figure).
        self.assertAlmostEqual(m["price"], 0.0011 * 0.05, places=10)
        self.assertNotAlmostEqual(m["price"], 0.0011, places=6)
        self.assertEqual(m["price_hive"], 0.0011)
        self.assertEqual(m["units"], "USD")
        self.assertEqual(m["hive_anchor"], 0.05)

    def test_no_anchor_never_fabricates_usd(self):
        with mock.patch.object(mp, "_hive_anchor_usd", return_value=None):
            m = mp.market_price("CENT")
        self.assertIsNone(m["price"],
                          "a USD price was fabricated from a HIVE-denominated "
                          "book print without the anchor")
        self.assertEqual(m["price_hive"], 0.0011,
                         "the raw book figure must survive for the caller")
        self.assertIn("HIVE", m["units"])

    def test_price_is_the_bid_not_the_stale_print(self):
        with mock.patch.object(mp, "_find",
                               return_value=_book_row(last="0.0099",
                                                      bid="0.0010",
                                                      ask="0.0012")):
            m = mp.market_price("CENT")
        # A sale realises the bid; lastPrice is only a stale print.
        self.assertEqual(m["price_hive"], 0.0010)
        self.assertEqual(m["basis"], "highestBid (exit price for a sale)")

    def test_enrich_price_map_feeds_the_usd_figure(self):
        price_map = {"CENT": 0}
        result, added, skipped = mp.enrich_price_map(
            price_map, symbols=["CENT"])
        self.assertAlmostEqual(result["CENT"], 0.0011 * 0.05, places=10)
        self.assertIn("CENT", added)
        self.assertEqual(skipped, [])

    def test_pool_derived_price_not_overwritten(self):
        price_map = {"CENT": 0.123}
        result, _, _ = mp.enrich_price_map(price_map, symbols=["CENT"])
        self.assertEqual(result["CENT"], 0.123,
                         "pool-derived prices are the better source where they "
                         "exist; the market fallback must not overwrite them")


if __name__ == "__main__":
    unittest.main()
