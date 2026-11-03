#!/usr/bin/env python3
"""Regression tests for operator token lists and held-position semantics
(2026-10-05, he-nuked discovery session).

Pins three behaviors:
1. AVOID_TOKENS filter pools from being destinations, but do NOT exit-force
   held positions (operator preference, not an impairment verdict).
2. COMPROMISED/EOL on a HELD pool produce a loud EXIT verdict, not silence
   (heartbeatonhive held SWAP.HIVE:SWAP.MATIC; the first both-tokens fix
   made the position VANISH from the report — silence hides a live-money
   fact the operator needs to see).
3. OPERATOR_CONFIRMED_GOOD tokens never get caught by any filter class —
   Tydyn confirmed SIM/DVS/DHEDGE/POB remain good despite dead-project
   narratives; POB underpins live positions and the SWAP.BTC:POB expand.
"""
import unittest
from lp_lifecycle import (PoolLifecycleFilter, COMPROMISED_TOKENS,
                           AVOID_TOKENS, OPERATOR_CONFIRMED_GOOD)
from lp_decision import decide


def _state(pools, fees):
    eco = {p: {"economics_known": True, "our_position_value_usd": 0.001,
               "fee_evidence": "measured", "reason": ""} for p in pools}
    th  = {p: {"computable": False, "current_value_usd": 0.0,
               "target_fee_usd": 1e-6} for p in pools}
    fee_map = {p: {"fee_apr_pct": f} for p, f in fees.items()}
    over   = {p: {"fee_apr_pct_authoritative": f} for p, f in fees.items()}
    return eco, th, fee_map, over


class TestAvoidListSemantics(unittest.TestCase):
    """AVOID: never a destination; held positions there are not exit-forced."""

    def test_avoid_tokens_never_expand_destinations(self):
        """A pool containing an avoid token must never win expansion, even
        with a dominant APR score."""
        pools = ("CENT:POB", "BRO:SWAP.HIVE")
        eco, th, fee_map, over = _state(pools, {"CENT:POB": 1.5, "BRO:SWAP.HIVE": 999})
        d = decide(list(pools), {}, position_economics=eco, threshold_analysis=th,
                   fee_evidence=fee_map, fee_evidence_override=over)
        self.assertNotEqual(d.expand_candidate, "BRO:SWAP.HIVE",
                            "avoid-token pool must never be the expand destination")

    def test_avoid_held_position_not_exit_forced(self):
        """A HELD avoid pool filters silently (like self-issued) — the
        operator knowingly holds it; avoid is a preference, not impairment.
        It must appear in notes as filtered, NOT get an exit verdict."""
        pools = ("CENT:POB", "BRO:SWAP.HIVE")
        eco, th, fee_map, over = _state(pools, {"CENT:POB": 1.5, "BRO:SWAP.HIVE": 0.0})
        d = decide(list(pools), {}, position_economics=eco, threshold_analysis=th,
                   fee_evidence=fee_map, fee_evidence_override=over)
        bro = d.verdicts.get("BRO:SWAP.HIVE")
        self.assertIsNone(bro,
                           "avoid-held pool must be filtered (silent), not verdict-exited")

    def test_avoid_reason_names_the_token(self):
        f = PoolLifecycleFilter()
        ok, reason = f.is_dividend_eligible("BXT", "SWAP.HIVE:BXT")
        self.assertFalse(ok)
        self.assertIn("operator-AVOID", reason)
        self.assertIn("BXT", reason)


class TestHeldImpairedPoolExits(unittest.TestCase):
    """COMPROMISED/EOL on a held pool = loud EXIT, never silence."""

    def test_held_compromised_pool_gets_exit_verdict(self):
        """The heartbeatonhive case: a held MATIC pool must produce an exit
        verdict in the report — it cannot vanish from verdicts."""
        pools = ("CENT:POB", "SWAP.HIVE:SWAP.MATIC")
        eco, th, fee_map, over = _state(
            pools, {"CENT:POB": 1.5, "SWAP.HIVE:SWAP.MATIC": 500.0})
        d = decide(list(pools), {}, position_economics=eco, threshold_analysis=th,
                   fee_evidence=fee_map, fee_evidence_override=over)
        v = d.verdicts.get("SWAP.HIVE:SWAP.MATIC")
        self.assertIsNotNone(v, "held compromised pool must not vanish from the report")
        self.assertEqual(v.action, "exit")
        self.assertIn("COMPROMISED", v.reason)

    def test_held_compromised_pool_can_be_reallocation_source(self):
        """The exit verdict must feed exit_candidates so the reallocation
        logic can name it as the source pool."""
        pools = ("CENT:POB", "SWAP.HIVE:SWAP.MATIC")
        eco, th, fee_map, over = _state(
            pools, {"CENT:POB": 1.5, "SWAP.HIVE:SWAP.MATIC": 500.0})
        d = decide(list(pools), {}, position_economics=eco, threshold_analysis=th,
                   fee_evidence=fee_map, fee_evidence_override=over)
        self.assertIn("SWAP.HIVE:SWAP.MATIC", d.exit_candidates)
        if d.recommended_reallocation:
            self.assertEqual(d.recommended_reallocation["from_pool"],
                             "SWAP.HIVE:SWAP.MATIC")

    def test_held_eol_pool_gets_exit_verdict(self):
        pools = ("CENT:POB", "SWAP.HIVE:FORGE")
        eco, th, fee_map, over = _state(
            pools, {"CENT:POB": 1.5, "SWAP.HIVE:FORGE": 500.0})
        d = decide(list(pools), {}, position_economics=eco, threshold_analysis=th,
                   fee_evidence=fee_map, fee_evidence_override=over)
        v = d.verdicts.get("SWAP.HIVE:FORGE")
        self.assertIsNotNone(v)
        self.assertEqual(v.action, "exit")
        self.assertIn("end-of-life", v.reason)

    def test_impairment_exit_beats_dust_expand(self):
        """Impairment override must run BEFORE dust verdict application.
        dust_verdict can say 'expand' for a tiny held position with a big
        on-chain APR — the impairment must win."""
        pools = ("SWAP.HIVE:SWAP.MATIC",)  # held, high APR, compromised
        eco, th, fee_map, over = _state(pools, {"SWAP.HIVE:SWAP.MATIC": 500.0})
        # make threshold computable so dust_verdict actually fires
        th["SWAP.HIVE:SWAP.MATIC"] = {"computable": True,
                                      "current_value_usd": 0.001,
                                      "target_fee_usd": 1e-9,
                                      "note": "test"}
        d = decide(list(pools), {}, position_economics=eco, threshold_analysis=th,
                   fee_evidence=fee_map, fee_evidence_override=over)
        v = d.verdicts.get("SWAP.HIVE:SWAP.MATIC")
        self.assertEqual(v.action, "exit",
                         "compromised override must beat dust expand")

    def test_all_32_compromised_blocked(self):
        f = PoolLifecycleFilter()
        for tok in sorted(COMPROMISED_TOKENS):
            ok, reason = f.is_dividend_eligible(tok, f"SWAP.HIVE:{tok}")
            self.assertFalse(ok, f"{tok} must be blocked")
            self.assertIn("COMPROMISED", reason,
                          f"{tok} must report COMPROMISED, got: {reason}")


class TestOperatorConfirmedGood(unittest.TestCase):
    """Confirmed-good tokens must pass through every filter class."""

    def test_confirmed_good_tokens_pass(self):
        f = PoolLifecycleFilter()
        for tok in sorted(OPERATOR_CONFIRMED_GOOD):
            ok, reason = f.is_dividend_eligible(tok, f"CENT:{tok}")
            self.assertTrue(ok, f"{tok} confirmed-good but filtered: {reason}")

    def test_confirmed_good_not_in_any_filter_set(self):
        for tok in OPERATOR_CONFIRMED_GOOD:
            self.assertNotIn(tok, COMPROMISED_TOKENS)
            self.assertNotIn(tok, AVOID_TOKENS)


if __name__ == "__main__":
    unittest.main()
