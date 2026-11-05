"""Tests for the market-price fallback.

The anchor is the token Tydyn checked by hand: BBHO, which he said has many buy
and sell orders. If this module cannot return a real price for BBHO it has
failed, regardless of what the other tests say.

2026-11-05 — HERMETICITY PASS. These tests used to subscript the result of
`market_price(<LIVE SYMBOL>)` directly, so a run where the market API was rate
limited or returned no row for a symbol died with
`TypeError: 'NoneType' object is not subscriptable`. The drift guard therefore
flapped between "431 passed" and "10 failed" for reasons that had nothing to do
with the code: `market_price()` returns None BY DESIGN for "unknown / no
market", and every production caller already guards it (`market_prices`,
`enrich_price_map`, `lp_realloc_executor`'s `(market_price(tok) or {})`).

So the tests are now split by intent:

  * LOGIC is pinned HERMETICALLY. A synthetic row is injected through
    `M._find`, and the USD conversion through `M._hive_anchor_usd` — the two
    seams the code actually reads — so spread bands, the bid-is-exit-price rule
    and the result shape are pinned whatever any real book is doing today.
    These never touch the network (asserted by `HermeticityTests`).
  * ONE live anchor remains (BBHO), and it splits by CAUSE: a probe that RAISES
    is environmental and SKIPS loudly; a reachable API that returns no row is a
    real measurement and FAILS. A failed probe is not an empty book.

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
      tests.test_lp_market_price -v
"""
import sys
import unittest
from contextlib import contextmanager

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import lp_market_price as M


# ---------------------------------------------------------------- synthetic rows
def _row(symbol, last, bid=None, ask=None, volume="1"):
    return {"symbol": symbol, "lastPrice": last, "highestBid": bid,
            "lowestAsk": ask, "volume": volume}


# A healthy book: tight spread with a real bid and ask (BBH's shape).
TIGHT = _row("SYNTH_TIGHT", "0.0445", bid="0.0445", ask="0.0450")
# An ask with NO bid: buyable, not sellable — the FRESA shape.
NOBID = _row("SYNTH_NOBID", "0.08", bid="0", ask="0.1")
# A bid well above the ask is not a market; ~200% spread (bid 0.045, ask 1).
ILLIQUID = _row("SYNTH_ILLIQ", "0.045", bid="0.00001", ask="1.0")
# Priced, but effectively zero in HIVE terms (HOP/IDD/WRD's shape).
DUST = _row("SYNTH_DUST", "0.0000013", bid="0.0000012", ask="1.0")
# No bid, no ask: lastPrice is all there is.
PRINT_ONLY = _row("SYNTH_PRINT", "0.25", bid="0", ask="0")

ANCHOR = 0.05   # fixed HIVE/USD so assertions are exact and offline


@contextmanager
def _stub_market(row=None, find=None, anchor=ANCHOR):
    """Patch BOTH network seams the code reads, then restore them.

    Stub the module-level bindings (`M._find`, `M._hive_anchor_usd`) — NOT a
    parameter slot — because that is how `market_price` and `enrich_price_map`
    actually resolve them. Any test that does not stub these has reached the
    network, which is the whole failure class this file now guards against.
    """
    real_find, real_anchor = M._find, M._hive_anchor_usd
    if find is not None:
        M._find = find
    elif row is not None:
        M._find = lambda sym, _r=row: [_r] if sym == _r["symbol"] else real_find(sym)
    M._hive_anchor_usd = lambda: anchor
    try:
        yield
    finally:
        M._find, M._hive_anchor_usd = real_find, real_anchor


def _synthetic(row, anchor=ANCHOR):
    """Price a synthetic row hermetically — no network at all."""
    with _stub_market(row=row, anchor=anchor):
        return M.market_price(row["symbol"])


def _live_anchor(tc, symbol):
    """Probe the LIVE market, splitting the two causes of a None price.

    An unreachable API is an environment fact, not a finding about the symbol,
    so it SKIPS with a loud reason. A reachable API returning rows is a real
    measurement and is returned for assertion — never skipped.
    """
    try:
        rows = M._find(symbol)
    except Exception as e:
        tc.skipTest(f"market API unreachable for {symbol} "
                    f"({type(e).__name__}: {e}) — a failed probe is not an "
                    "empty book")
    return rows


class TestFieldShape(unittest.TestCase):
    """The mistake that caused the whole problem: a nested key that is not there.

    Tested by BEHAVIOUR, not by asserting on docstring prose — a docstring
    assertion proves nothing about whether the code reads the right field.
    """

    def test_price_is_read_from_the_row_and_uses_the_bid(self):
        """The row's price is read from the top level, and we price on the BID.

        Do NOT assert an exact figure: the order book MOVES. A hard-coded price
        fails for reasons unrelated to the code, so assert the SHAPE and the
        relationships, which are the invariants that matter.
        """
        m = _synthetic(TIGHT)
        self.assertIsNotNone(m)
        self.assertGreater(m["price"], 0)
        self.assertGreater(m["lastPrice"], 0)
        # price is USD, highest_bid/lowest_ask/lastPrice are the RAW SWAP.HIVE
        # book figures (all HE books quote in SWAP.HIVE). They are NOT
        # comparable across units — price = price_hive * hive_anchor.
        self.assertIsNotNone(m["price_hive"])
        self.assertGreater(m["price_hive"], 0)
        self.assertEqual(m["price_hive"], m["highest_bid"],
                         "the exit price basis is the highest bid")
        self.assertEqual(m["hive_anchor"], ANCHOR)
        self.assertGreater(m["hive_anchor"], 0)
        self.assertAlmostEqual(m["price"], m["price_hive"] * m["hive_anchor"],
                               places=9,
                               msg="price (USD) must equal price_hive * hive_anchor")

    def test_no_anchor_never_fabricates_a_usd_price(self):
        """If the HIVE anchor cannot be fetched, NO USD price is returned.

        The unit must then SAY so rather than passing a HIVE figure off as USD
        (the 2026-10-17 ~19x unit-blindness bug).
        """
        m = _synthetic(TIGHT, anchor=None)
        self.assertIsNone(m["price"])
        self.assertGreater(m["price_hive"], 0)
        self.assertIn("HIVE", m["units"])

    def test_params_are_not_list_wrapped(self):
        """A list-wrapped params returns zero rows SILENTLY, which looks like
        'no market'. Guard the shape so that regression cannot return."""
        import inspect
        src = inspect.getsource(M._find)
        self.assertNotIn('"params": [', src,
                        "params must be the query object directly, not a list")


class TestMarketPrice(unittest.TestCase):
    """LIVE anchor + hermetic logic. See the module docstring."""

    def test_bbho_has_a_real_price(self):
        """Tydyn checked this by hand — BBHO has many buy and sell orders.

        The one live test. Splits by cause so it cannot flap: an unreachable API
        skips, a reachable API returning no row for BBHO FAILS (a real finding).
        """
        rows = _live_anchor(self, "BBHO")
        self.assertTrue(rows, "market API reachable but returned NO ROW for BBHO")
        m = M.market_price("BBHO")
        self.assertIsNotNone(m, "BBHO has a row but no price could be derived")
        self.assertGreater(m["price_hive"], 0)
        self.assertGreater(m["volume"], 0)
        self.assertIsNotNone(m["highest_bid"])
        self.assertIsNotNone(m["lowest_ask"])
        # The USD leg depends on the live anchor. Pin the INVARIANT (never claim
        # USD without one) instead of the anchor's momentary value.
        if m["price"] is None:
            self.assertEqual(m["units"], "HIVE (anchor unavailable)",
                             "a missing anchor must be declared, not silently USD")

    def test_fresa_shape_prices_on_a_print_when_no_bid_exists(self):
        """FRESA has only sell orders, but still records a lastPrice.

        Hermetic: the FRESA shape (ask, no bid) is fed synthetically, because
        the live book has gained and lost a bid before and moved this test.
        """
        m = _synthetic(NOBID)
        self.assertIsNotNone(m)
        self.assertGreater(m["lastPrice"], 0)
        self.assertEqual(m["liquidity"], "no-bid")
        self.assertFalse(m["sizeable"], "a token with no bid cannot be exited")

    def test_dust_tokens_are_priced_at_near_zero_not_missing(self):
        """HOP/IDD/WRD are priced — just at effectively zero.

        lastPrice is the RAW SWAP.HIVE book figure, so the dust threshold is in
        HIVE units, not USD. The exact cutoff is NOT pinned — the book moves —
        only the invariant that these tokens are priced NEAR zero and are
        unsellable.
        """
        for t in ("HOP", "IDD", "WRD"):
            row = _row(f"SYNTH_DUST_{t}", "0.0000013", bid="0.0000012", ask="1.0")
            m = _synthetic(row)
            self.assertIsNotNone(m, f"{t} should resolve, not be missing")
            self.assertGreater(m["lastPrice"], 0)
            self.assertLess(m["lastPrice"], 1e-3,
                            f"{t} is dust-priced in HIVE, not liquid")
            self.assertFalse(m["sizeable"], f"{t} must not be sizeable")

    def test_unknown_symbol_returns_none(self):
        """Hermetic: 'no row' is the answer, whether the API is up or not."""
        with _stub_market(find=lambda sym: []):
            self.assertIsNone(M.market_price("DEFINITELY_NOT_A_TOKEN_XYZ"))

    def test_empty_symbol_returns_none(self):
        self.assertIsNone(M.market_price(""))

    def test_zero_price_is_no_price_not_free(self):
        with _stub_market(find=lambda sym: [_row("SYNTH_ZERO", "0")]):
            self.assertIsNone(M.market_price("SYNTH_ZERO"))

    def test_result_carries_provenance_and_liquidity(self):
        m = _synthetic(TIGHT)
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
        m = _synthetic(TIGHT)
        self.assertEqual(m["liquidity"], "tight")
        self.assertTrue(m["sizeable"])
        # The invariant is that the exit basis IS the bid (price_hive == bid),
        # not a cross-unit equality.
        self.assertEqual(m["price_hive"], m["highest_bid"],
                         "exit price basis is the highest bid")
        self.assertLessEqual(m["highest_bid"], m["lowest_ask"])
        self.assertLessEqual(m["spread_pct"], 10.0)

    def test_no_bid_falls_back_to_lastprice_and_refuses_sizing(self):
        """SYNTHETIC: born reading the LIVE FRESA book, which then had an ask
        and no bid. FRESA later GAINED a bid and the test failed — the market
        moved, not the code. A market-state snapshot is not an invariant; feed
        the reader a synthetic no-bid row so the LOGIC is what is pinned.
        """
        m = _synthetic(NOBID)
        self.assertEqual(m["liquidity"], "no-bid")
        self.assertFalse(m["sizeable"], "a token with no bid cannot be exited")
        self.assertEqual(m["price_hive"], m["lastPrice"],
                         "no-bid fallback: price_hive is lastPrice")
        self.assertAlmostEqual(m["price"], m["lastPrice"] * m["hive_anchor"],
                               places=9)
        self.assertIn("NO BID", m["basis"])

    def test_spread_band_matches_the_measured_spread(self):
        """Band classification must agree with the spread it measured.

        Hermetic across ALL bands — this used to assert a live snapshot (BBHO
        was 'wide' from a days-old 41.7% reading, then tightened to 0.0% and the
        test failed because the MARKET moved, not because the classifier broke).
        """
        wide = _row("SYNTH_WIDE", "1.0", bid="1.0", ask="1.25")  # 22.2% mid
        for row, band in ((TIGHT, "tight"), (wide, "wide"),
                          (ILLIQUID, "illiquid")):
            m = _synthetic(row)
            self.assertEqual(m["liquidity"], band, row["symbol"])
            sp = m["spread_pct"]
            if band == "tight":                     # <=10% : sizeable
                self.assertLessEqual(sp, 10.0, row["symbol"])
                self.assertTrue(m["sizeable"], row["symbol"])
            elif band == "wide":                    # 10-50% : size down
                self.assertGreater(sp, 10.0, row["symbol"])
                self.assertLessEqual(sp, 50.0, row["symbol"])
                self.assertTrue(m["sizeable"], row["symbol"])
            else:                                   # >50% : value, never size
                self.assertGreater(sp, 50.0, row["symbol"])
                self.assertFalse(m["sizeable"], row["symbol"])

    def test_two_hundred_percent_spread_is_illiquid(self):
        """A token with NO bid is unsellable — a stronger condition than
        illiquid. Pin 'not sizeable' (the invariant that matters) and accept
        either no-bid or illiquid as the band.

        This is the test that failed in the drift guard with
        `TypeError: 'NoneType' object is not subscriptable`; it now drives
        synthetic rows instead of live HOP/IDD/WRD/BRN.
        """
        rows = [ILLIQUID,
                NOBID,
                _row("SYNTH_ILLIQ2", "0.001", bid="0.0000001", ask="0.9"),
                PRINT_ONLY]
        for row in rows:
            m = _synthetic(row)
            self.assertIsNotNone(m, row["symbol"])
            self.assertIn(m["liquidity"], ("no-bid", "illiquid"), row["symbol"])
            self.assertFalse(m["sizeable"], row["symbol"])

    def test_spread_is_computed_between_bid_and_ask(self):
        m = _synthetic(TIGHT)
        bid, ask = m["highest_bid"], m["lowest_ask"]
        expected = ((ask - bid) / ((ask + bid) / 2.0)) * 100
        self.assertAlmostEqual(m["spread_pct"], expected, places=2)


class TestEnrich(unittest.TestCase):

    def test_fills_only_zero_prices(self):
        row = _row("SYNTH_ENRICH", "0.0445", bid="0.0445", ask="0.0450")
        with _stub_market(row=row):
            existing = {"CENT": 0.0000549, "SYNTH_ENRICH": 0}
            out, added, skipped = M.enrich_price_map(existing)
        self.assertEqual(out["CENT"], 0.0000549,
                         "a pool-derived price must never be overwritten")
        self.assertIn("SYNTH_ENRICH", added)
        self.assertGreater(out["SYNTH_ENRICH"], 0)

    def test_enrichment_preserves_liquidity_verdict(self):
        """SYNTHETIC: was live-FRESA; the book moved (a bid appeared, spread
        ~18% = wide/sizeable) and broke the assumption. Feed enrich a synthetic
        no-bid row and assert the liquidity verdict travels through unchanged."""
        with _stub_market(row=NOBID):
            out, added, _ = M.enrich_price_map({NOBID["symbol"]: 0})
        self.assertIn(NOBID["symbol"], out)
        self.assertFalse(added[NOBID["symbol"]]["sizeable"],
                         "an unsellable token must not pass as sizeable")

    def test_returns_all_three_buckets(self):
        out, added, skipped = M.enrich_price_map({"CENT": 1.0})
        self.assertIsInstance(out, dict)
        self.assertIsInstance(added, dict)
        self.assertIsInstance(skipped, list)

    def test_does_not_mutate_the_input(self):
        with _stub_market(row=TIGHT):
            existing = {TIGHT["symbol"]: 0}
            M.enrich_price_map(existing)
        self.assertEqual(existing[TIGHT["symbol"]], 0,
                         "input map must not be mutated")


class HermeticityTests(unittest.TestCase):
    """The skill's hermeticity proof: a unit test that takes longer than ~0.1s
    has reached the network, regardless of what its stubs claim to do."""

    def test_synthetic_path_never_reaches_the_network(self):
        import time
        t0 = time.monotonic()
        m = _synthetic(TIGHT)
        dt = time.monotonic() - t0
        self.assertIsNotNone(m)
        self.assertLess(dt, 0.1,
                        f"synthetic pricing took {dt:.3f}s — it hit the network")


if __name__ == "__main__":
    unittest.main(verbosity=2)
