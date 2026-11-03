"""Regression tests for age-bonus + destination-safety mechanics (2026-10-07).

Sources of the anchor figures, all browser/API-verified 2026-10-07:
  - PsyopDaily per-position display, hermes-sandbox ALIVE:HEARTBEAT:
    "2.30x time bonus, held 27 days"; "a 10% top-up: time bonus drops
    2.30x → 2.20x (−4% of effective weight)"
  - PsyopDaily /api/hive/pools safety_reasons: "WIT has 0.2432 HIVE of total
    exit liquidity (0.2432 in order-book bids) — under the 0.5 HIVE floor";
    "REVX ... 0.2656 in Diesel Pools" (pool-side depth = counter-side
    SWAP.HIVE reserve, NOT TVL — verified against chain data)
  - The deployed curve quirks are pinned from lp_reward_math.estimate_batch,
    verified against upstream distribution.js.
NOTE: this file has now been deleted from tests/ AND from
lp_tests_canonical/ once each (7th+8th reap events, 2026-10-07) — the
canonical-mirror assumption ("outside the kernel cwd survives") FAILED.
A durable copy also lives at
~/MEGA/Hermes-Backups/lp_tests_durable/test_age_bonus.py.

Run:
    /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
        tests.test_age_bonus -v
"NO TESTS RAN" means the tests are GONE, not passing.
"""
import sys
import time
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
from lp_dividends import (bonus_multiplier, effective_share_fraction,
                          topup_dilution)
import lp_decision as L

# ALIVE-style curve: string numPeriods (curve ACTIVE), 5%/period, cap 365
CURVE = {"numPeriods": "30", "periodBonusPct": "5"}
AGE_ANCHOR = 26.9  # site displays "27 days" rounded


class TestBonusMultiplierCurve(unittest.TestCase):
    """The curve math against the PsyopDaily anchor + deployed quirks."""

    def test_anchor_2_30x(self):
        self.assertAlmostEqual(bonus_multiplier(AGE_ANCHOR, CURVE), 2.30, places=2)

    def test_anchor_topup_to_2_20x(self):
        d = topup_dilution(AGE_ANCHOR, CURVE, 0.10)
        self.assertAlmostEqual(d["old_mult"], 2.30, places=2)
        self.assertAlmostEqual(d["new_mult"], 2.20, places=2)
        self.assertTrue(d["active_curve"])
        self.assertLess(d["ratio"], 0.97)
        self.assertGreater(d["ratio"], 0.93)

    def test_cap_saturates(self):
        self.assertAlmostEqual(bonus_multiplier(40.0, CURVE), bonus_multiplier(30.0, CURVE))

    def test_numeric_numperiods_means_no_curve(self):
        self.assertIsNone(bonus_multiplier(26.9, {"numPeriods": 30, "periodBonusPct": "5"}))

    def test_no_curve(self):
        self.assertIsNone(bonus_multiplier(26.9, None))
        self.assertIsNone(bonus_multiplier(26.9, {}))

    def test_immature_excluded(self):
        self.assertIsNone(bonus_multiplier(0.5, CURVE))

    def test_exactly_one_tick_no_bonus(self):
        self.assertIsNone(bonus_multiplier(1.0, CURVE))

    def test_first_bonus_at_just_over_one_tick(self):
        self.assertAlmostEqual(bonus_multiplier(1.01, CURVE), 1.05, places=2)

    def test_zero_topup_is_identity(self):
        d = topup_dilution(AGE_ANCHOR, CURVE, 0.0)
        self.assertEqual(d["ratio"], 1.0)
        self.assertFalse(d["active_curve"])

    def test_no_curve_no_penalty(self):
        d = topup_dilution(AGE_ANCHOR, None, 0.10)
        self.assertEqual(d["ratio"], 1.0)
        self.assertFalse(d["active_curve"])
        self.assertIn("no active bonus curve", d["reason"])

    def test_diluted_age_below_threshold_ends_curve(self):
        d = topup_dilution(1.05, CURVE, 0.10)
        self.assertFalse(d["active_curve"])
        self.assertIn("threshold", d["reason"])


class TestEffectiveShareFraction(unittest.TestCase):
    """Effective (bonus-weighted) share vs raw share — 'our share' extended."""

    def test_matured_position_earns_more_than_raw_share(self):
        own, others = 100.0, 1000.0
        mult = bonus_multiplier(AGE_ANCHOR, CURVE)
        total_eff = own * mult + others * mult
        r = effective_share_fraction(own, AGE_ANCHOR, CURVE, total_eff)
        self.assertTrue(r["computable"])
        self.assertGreater(r["effective_share"], r["raw_share"])
        self.assertAlmostEqual(r["bonus_multiplier"], 2.30, places=2)

    def test_unknown_total_refuses(self):
        r = effective_share_fraction(100.0, AGE_ANCHOR, CURVE, None)
        self.assertFalse(r["computable"])
        self.assertIn("unknown", r["reason"])

    def test_immature_position_refuses(self):
        r = effective_share_fraction(100.0, 0.5, CURVE, 5000.0)
        self.assertFalse(r["computable"])
        self.assertIn("younger than one tick", r["reason"])

    def test_zero_shares_refuses(self):
        r = effective_share_fraction(0.0, AGE_ANCHOR, CURVE, 5000.0)
        self.assertFalse(r["computable"])


def _mk_reward_entry(pool, curve=CURVE, age_days=AGE_ANCHOR, tick_hours=24.0,
                     days_remaining=None):
    """discover_rewards-shaped entry with the 2026-10-07 age-bonus fields."""
    tf_ms = (time.time() - age_days * 86400) * 1000.0
    return [{
        "mined_token": "HEARTBEAT",
        "daily_reward": 100.0,
        "whole_batch_quantity": 100.0,
        "num_ticks": 1,
        "period_hours": tick_hours,
        "denominator": "whole batch tokenBalances / numTicks (NOT our share)",
        "units_comparable_across_tokens": False,
        "lottery_winners": 0,
        "lottery_interval_hours": 24,
        "token_balance": 100.0,
        "type": "distribution",
        "bonus_curve": curve,
        "our_time_factor_ms": tf_ms,
        "tick_hours": tick_hours,
        "days_remaining": days_remaining,
    }]


class TestDecisionLayerWiring(unittest.TestCase):
    """The dilution term must be live in decide() — not inert."""

    HELD = ["ALIVE:HEARTBEAT", "CENT:POB"]
    FEE = {"ALIVE:HEARTBEAT": {"fee_apr_pct": 5.0},
           "CENT:POB": {"fee_apr_pct": 5.0}}
    ECON = {"ALIVE:HEARTBEAT": {"our_position_value_usd": 0.001},
            "CENT:POB": {"our_position_value_usd": 0.001}}
    THRESH = {"ALIVE:HEARTBEAT": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6},
              "CENT:POB": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6}}

    def _decide(self, rewards, exit_map=None):
        return L.decide(self.HELD, rewards, fee_evidence=self.FEE,
                        position_economics=self.ECON,
                        threshold_analysis=self.THRESH,
                        fee_evidence_override={
                            p: {"fee_apr_pct_authoritative": self.FEE[p]["fee_apr_pct"]}
                            for p in self.HELD},
                        exit_liquidity_map=exit_map)

    def test_non_curve_pool_wins_expansion(self):
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT"),
            "CENT:POB": _mk_reward_entry("CENT:POB", curve=None),
        }
        r = self._decide(rewards)
        self.assertEqual(r.expand_candidate, "CENT:POB")
        v = r.verdicts["ALIVE:HEARTBEAT"]
        self.assertIsNotNone(v.topup_dilution_ratio)
        self.assertLess(v.topup_dilution_ratio, 0.97)
        self.assertIn("dilutes", v.topup_dilution_note)

    def test_both_pools_no_curve_matches_legacy_choice(self):
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT", curve=None),
            "CENT:POB": _mk_reward_entry("CENT:POB", curve=None),
        }
        r = self._decide(rewards)
        for v in r.verdicts.values():
            self.assertIsNone(v.topup_dilution_ratio)
            self.assertIsNone(v.topup_dilution_note)

    def test_weight_zero_disables_penalty(self):
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT"),
            "CENT:POB": _mk_reward_entry("CENT:POB", curve=None),
        }
        old = L.TOPUP_DILUTION_WEIGHT
        try:
            L.TOPUP_DILUTION_WEIGHT = 0.0
            r = self._decide(rewards)
            self.assertEqual(r.expand_candidate, "ALIVE:HEARTBEAT")
        finally:
            L.TOPUP_DILUTION_WEIGHT = old

    def test_score_stored_on_verdicts(self):
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT"),
            "CENT:POB": _mk_reward_entry("CENT:POB", curve=None),
        }
        r = self._decide(rewards)
        for v in r.verdicts.values():
            self.assertIsNotNone(v.expand_score)

    def test_dilution_ratio_present_in_report_dict(self):
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT"),
            "CENT:POB": _mk_reward_entry("CENT:POB", curve=None),
        }
        r = self._decide(rewards)
        d = r.to_dict()
        v = d["verdicts"]["ALIVE:HEARTBEAT"]
        self.assertIn("topup_dilution_ratio", v)
        self.assertIn("expand_score", v)


class TestRewardRunwayGate(unittest.TestCase):
    """PsyopDaily 'rewards end soon' (<14d) — applied on the destination side."""

    HELD = ["ALIVE:HEARTBEAT", "CENT:POB"]
    FEE = {"ALIVE:HEARTBEAT": {"fee_apr_pct": 5.0},
           "CENT:POB": {"fee_apr_pct": 5.0}}
    ECON = {"ALIVE:HEARTBEAT": {"our_position_value_usd": 0.001},
            "CENT:POB": {"our_position_value_usd": 0.001}}
    THRESH = {"ALIVE:HEARTBEAT": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6},
              "CENT:POB": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6}}

    def _decide(self, rewards):
        return L.decide(self.HELD, rewards, fee_evidence=self.FEE,
                        position_economics=self.ECON,
                        threshold_analysis=self.THRESH,
                        fee_evidence_override={
                            p: {"fee_apr_pct_authoritative": self.FEE[p]["fee_apr_pct"]}
                            for p in self.HELD})

    def test_expiring_rewards_lose_expansion(self):
        """A pool with 5 days of rewards left must not win over a 200-day one."""
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT", curve=None,
                                                 days_remaining=5.0),
            "CENT:POB": _mk_reward_entry("CENT:POB", curve=None, days_remaining=200.0),
        }
        r = self._decide(rewards)
        self.assertEqual(r.expand_candidate, "CENT:POB")
        v = r.verdicts["ALIVE:HEARTBEAT"]
        self.assertIsNotNone(v.expand_blocked_reason)
        self.assertIn("expire", v.expand_blocked_reason)
        self.assertEqual(v.reward_days_remaining, 5.0)

    def test_unknown_runway_does_not_gate(self):
        """days_remaining missing/None → must not block (unknown ≠ expired)."""
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT", curve=None),
            "CENT:POB": _mk_reward_entry("CENT:POB", curve=None, days_remaining=None),
        }
        r = self._decide(rewards)
        # no runway data anywhere: legacy order, nothing blocked
        for v in r.verdicts.values():
            self.assertIsNone(v.expand_blocked_reason)

    def test_runway_field_reaches_artifact(self):
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT", curve=None,
                                                 days_remaining=5.0),
            "CENT:POB": _mk_reward_entry("CENT:POB", curve=None, days_remaining=200.0),
        }
        r = self._decide(rewards)
        d = r.to_dict()
        self.assertIn("reward_days_remaining", d["verdicts"]["CENT:POB"])
        self.assertIn("expand_blocked_reason", d["verdicts"]["ALIVE:HEARTBEAT"])


class TestExitLiquidityGate(unittest.TestCase):
    """PsyopDaily 'unsafe' metric: total exit liquidity under 0.5 HIVE floor."""

    HELD = ["ALIVE:HEARTBEAT", "WIT:XXX"]
    FEE = {"ALIVE:HEARTBEAT": {"fee_apr_pct": 5.0},
           "WIT:XXX": {"fee_apr_pct": 5.0}}
    ECON = {"ALIVE:HEARTBEAT": {"our_position_value_usd": 0.001},
            "WIT:XXX": {"our_position_value_usd": 0.001}}
    THRESH = {"ALIVE:HEARTBEAT": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6},
              "WIT:XXX": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6}}

    def _decide(self, rewards, exit_map=None):
        return L.decide(self.HELD, rewards, fee_evidence=self.FEE,
                        position_economics=self.ECON,
                        threshold_analysis=self.THRESH,
                        fee_evidence_override={
                            p: {"fee_apr_pct_authoritative": self.FEE[p]["fee_apr_pct"]}
                            for p in self.HELD},
                        exit_liquidity_map=exit_map)

    def test_thin_book_loses_expansion(self):
        """A token with 0.24 HIVE of exit depth cannot win over a deep pool."""
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT", curve=None),
            "WIT:XXX": _mk_reward_entry("WIT:XXX", curve=None),
        }
        exit_map = {"ALIVE": 50.0, "HEARTBEAT": 50.0,
                    "WIT": 0.2432,  # the site's verified WIT anchor
                    "XXX": 10.0}
        r = self._decide(rewards, exit_map)
        self.assertEqual(r.expand_candidate, "ALIVE:HEARTBEAT")
        v = r.verdicts["WIT:XXX"]
        self.assertIsNotNone(v.expand_blocked_reason)
        self.assertIn("exit liquidity", v.expand_blocked_reason)
        self.assertIn("WIT", v.expand_blocked_reason)

    def test_hazard_on_quote_side_also_blocks(self):
        """Pair rule: hazard riding on the QUOTE token must block too."""
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT", curve=None),
            "WIT:XXX": _mk_reward_entry("WIT:XXX", curve=None),
        }
        exit_map = {"ALIVE": 50.0, "HEARTBEAT": 50.0,
                    "WIT": 50.0,
                    "XXX": 0.24}  # thin depth on the QUOTE side
        r = self._decide(rewards, exit_map)
        self.assertEqual(r.expand_candidate, "ALIVE:HEARTBEAT")
        v = r.verdicts["WIT:XXX"]
        self.assertIsNotNone(v.expand_blocked_reason)
        self.assertIn("XXX", v.expand_blocked_reason)

    def test_unknown_depth_does_not_gate(self):
        """None depth (probe failure) must not read as below-floor."""
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT", curve=None),
            "WIT:XXX": _mk_reward_entry("WIT:XXX", curve=None),
        }
        exit_map = {"ALIVE": 50.0, "HEARTBEAT": 50.0,
                    "WIT": None, "XXX": 10.0}  # WIT UNKNOWN
        r = self._decide(rewards, exit_map)
        # The invariant that matters: UNKNOWN depth must NOT produce a block.
        # (Which pool wins the tie is insertion-order legacy behaviour.)
        for v in r.verdicts.values():
            self.assertIsNone(v.expand_blocked_reason)
        # And WIT must not have been gated despite unknown depth — it kept
        # its eligibility in the artifact.
        self.assertNotIn("WIT", r.verdicts["WIT:XXX"].expand_blocked_reason or "")

    def test_no_map_means_no_gate(self):
        """exit_liquidity_map=None → pre-2026-10-07 behaviour."""
        rewards = {
            "ALIVE:HEARTBEAT": _mk_reward_entry("ALIVE:HEARTBEAT", curve=None),
            "WIT:XXX": _mk_reward_entry("WIT:XXX", curve=None),
        }
        r = self._decide(rewards)
        self.assertEqual(r.expand_candidate, "ALIVE:HEARTBEAT")  # legacy order
        for v in r.verdicts.values():
            self.assertIsNone(v.expand_blocked_reason)


if __name__ == "__main__":
    unittest.main()
