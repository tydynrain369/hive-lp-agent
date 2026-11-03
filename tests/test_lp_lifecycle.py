#!/usr/bin/env python3
"""Tests for lp_lifecycle.py — lifecycle filter must override score in decision layer."""
import unittest
from lp_decision import decide, MIN_FEE_APR_FOR_PRODUCTIVE
from lp_lifecycle import PoolLifecycleFilter


def _state(pools, fees, prices):
    eco = {p: {"economics_known": True, "our_position_value_usd": 0.001,
               "fee_evidence": "measured", "reason": ""} for p in pools}
    th  = {p: {"computable": False, "current_value_usd": 0.0,
               "target_fee_usd": 1e-6} for p in pools}
    fee_map = {p: {"fee_apr_pct": f} for p, f in fees.items()}
    over   = {p: {"fee_apr_pct_authoritative": f} for p, f in fees.items()}
    return eco, th, fee_map, over


class TestLifecycleFilterIntegration(unittest.TestCase):
    """2026-10-05: EOL pools and operator-issued tokens must not be expanded into."""

    def test_forge_is_not_expand_candidate_even_with_inflated_apr(self):
        """FORGE shut down June 2025. Its APR, if we forced 999, dominates scoring,
        so the lifecycle filter must outweigh the score."""
        pools = ("CENT:POB", "SWAP.HIVE:FORGE")
        eco, th, fee_map, over = _state(pools, {"CENT:POB": 1.5, "SWAP.HIVE:FORGE": 999}, {"CENT":0.001, "POB":0.001, "SWAP.HIVE":0.057, "FORGE":0.001})
        d = decide(list(pools), {}, position_economics=eco, threshold_analysis=th,
                   fee_evidence=fee_map, fee_evidence_override=over)
        self.assertNotEqual(d.expand_candidate, "SWAP.HIVE:FORGE",
                            "EOL pool must not win regardless of APR score")

    def test_operator_issued_tokens_filtered(self):
        """Tokens created/managed by the operator must not be a destination."""
        f = PoolLifecycleFilter()
        for tok in ("HOP", "IDD", "WRD", "POKEMON", "BEATCZ",
                  "PEPECASH", "SWAP.PEPE", "SWAP.PEPET"):
            ok, reason = f.is_dividend_eligible(tok)
            self.assertFalse(ok, f"{tok} filtered as operator-issued")

    def test_unlisted_cannot_be_valued_filtered(self):
        """Tokens with no Hive-Engine market cannot be valued for exit sizing."""
        f = PoolLifecycleFilter()
        for tok in ("BBHO", "FRESA"):
            ok, reason = f.is_dividend_eligible(tok)
            self.assertFalse(ok)
            self.assertIn("no Hive-Engine market", reason)

    def test_legitimate_tokens_pass(self):
        """CENT and POB are real tokens with real markets — pass through."""
        f = PoolLifecycleFilter()
        ok, reason = f.is_dividend_eligible("CENT", "CENT:POB")
        self.assertTrue(ok)

    def test_forge_filter_overrides_SCORE_and_ignores_pool(self):
        """Even when FORGE dominates by score and its account holds the position,
        it must be out, and the report must note why."""
        f = PoolLifecycleFilter()
        ok, reason = f.is_dividend_eligible("FORGE", "SWAP.HIVE:FORGE")
        self.assertFalse(ok)
        self.assertIn("end-of-life", reason)


if __name__ == "__main__":
    unittest.main()
