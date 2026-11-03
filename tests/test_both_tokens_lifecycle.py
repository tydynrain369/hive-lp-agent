#!/usr/bin/env python3
"""Regression tests for the both-tokens lifecycle check (2026-10-05).

SWAP.HIVE:SWAP.MATIC slipped through the lifecycle filter because the
original integration only checked pool.split(":")[0] — the BASE token.
The compromised token was on the QUOTE side. A compromised token on
EITHER side of a pair must filter the whole pool.
"""
import unittest
from lp_lifecycle import PoolLifecycleFilter


class TestBothTokensChecked(unittest.TestCase):
    """A compromised/filtered token on EITHER side of the pair must block."""

    def test_compromised_quote_side_filters_the_pool(self):
        """SWAP.MATIC is the QUOTE in SWAP.HIVE:SWAP.MATIC — the exact
        configuration that slipped through the base-only check."""
        f = PoolLifecycleFilter()
        # quote side
        ok, reason = f.is_dividend_eligible("SWAP.MATIC", "SWAP.HIVE:SWAP.MATIC")
        self.assertFalse(ok)
        self.assertIn("COMPROMISED", reason)

    def test_compromised_base_side_filters_the_pool(self):
        """And a compromised BASE token must also filter (already worked,
        pinned here so the symmetry is explicit)."""
        f = PoolLifecycleFilter()
        ok, reason = f.is_dividend_eligible("SWAP.MATIC", "SWAP.MATIC:SWAP.HIVE")
        self.assertFalse(ok)

    def test_clean_pair_passes(self):
        f = PoolLifecycleFilter()
        ok, _ = f.is_dividend_eligible("SWAP.HIVE", "SWAP.HIVE:POB")
        self.assertTrue(ok)

    def test_self_issued_on_either_side_blocks(self):
        """HOP is self-issued; a pool pairing HOP with anything must be out,
        whichever side HOP sits on."""
        f = PoolLifecycleFilter()
        for tok, pool in (("HOP", "HOP:SWAP.HIVE"), ("HOP", "SWAP.HIVE:HOP")):
            ok, reason = f.is_dividend_eligible(tok, pool)
            self.assertFalse(ok, f"{tok} in {pool} must be filtered")


if __name__ == "__main__":
    unittest.main()
