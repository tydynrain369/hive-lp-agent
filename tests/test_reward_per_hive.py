"""Regression tests for the reward-per-HIVE scoring term (2026-10-10).

Anchor case (Tydyn's CENT question, chain-measured 2026-10-08): CENT:POB
paid $0.026/day whole-pool rewards on 1,819 HIVE TVL ($0.000014/HIVE/day)
while CENT:TRIBE paid $1.80/day on 14,398 HIVE ($0.000125/HIVE/day) — ~9x
better per deposited HIVE. Yet the expand ranking picked CENT:POB because
it scored by OUR held position's threshold distance alone. The fix scores
candidates ALSO by what a NEW HIVE deposited there would capture per day,
normalized to fee-APR-equivalent points so it sits beside the fee term.

IMPORTANT (learned from the first version of these tests): with our
fixture's threshold distances (0.6x) and position values, decide() routes
through the DUST-EXPAND branch — the same branch that fires in production
(CENT:POB 0.4x came from there). The term is wired into BOTH branches;
these tests exercise and pin the dust-branch arithmetic:
    score = (fee*W_fee + count*W_count + rph_apr_eq*W_rph*W_fee) * viability
where rph_apr_eq = (dividend_usd / tvl_hive) / HIVE_USD * 100.

Run:
    /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
        tests.test_reward_per_hive -v
HERMETICITY: fully synthetic; must complete in <0.1s.
"""
import sys
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import lp_decision as L


def _mk_rewards(qty, days_remaining=200.0):
    """reward_info entries shaped like discover_rewards output."""
    return [{
        "mined_token": "RICH" if qty > 26 else "POORTOK",
        "daily_reward": qty,
        "whole_batch_quantity": qty * 100,
        "num_ticks": 100,
        "period_hours": 24.0,
        "tick_hours": 24.0,
        "days_remaining": days_remaining,
        "type": "distribution",
        "bonus_curve": None,
    }]


# Two pools with identical fee APR and identical threshold distance, but
# POOR pays 1/9th the reward per HIVE of TVL. Price map: the mined token at
# 0.001 USD -> RICH emits 1800/tick = $1.80/tick whole-pool; POOR 26/tick
# = $0.026/tick. TVLs: 14,398 HIVE vs 1,819 HIVE — the anchor numbers.
HELD = ["CENT:TRIBE", "CENT:POOR"]
FEE = {"CENT:TRIBE": {"fee_apr_pct": 5.0},
       "CENT:POOR": {"fee_apr_pct": 5.0}}
ECON = {"CENT:TRIBE": {"our_position_value_usd": 0.001},
        "CENT:POOR": {"our_position_value_usd": 0.001}}
THRESH = {"CENT:TRIBE": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6},
          "CENT:POOR": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6}}
REWARDS = {
    "CENT:TRIBE": _mk_rewards(1800.0),
    "CENT:POOR": _mk_rewards(26.0),
}
PRICE_MAP = {"RICH": 0.001, "POORTOK": 0.001}
TVL_MAP = {"CENT:TRIBE": 14398.0, "CENT:POOR": 1819.0}
SPREAD_MAP = {"RICH": 0.02, "POORTOK": 0.02}

VIABILITY = 1.6            # _viability_factor at 0.6x threshold (verified)
DUST_BASE = 5.1            # fee 5.0*1.0 + 1 token*0.1


def _rph_apr_eq(dividend_usd, tvl):
    # 2026-11-02: match the live code's 2026-10-17 fix. reward-per-HIVE is a
    # USD-per-DAY-per-HIVE figure; the fee term is an ANNUAL percentage, so the
    # APR-equivalent is rph*365/HIVE_USD*100. The old rph/HIVE_USD*100 (which
    # treated APR as a daily rate) understated the term 365x — it was correct
    # before the fix and is wrong now. The invariant these tests pin (the term
    # is APR-equivalent and scales with reward-per-HIVE) is unchanged; only the
    # arithmetic helper was stale.
    return (dividend_usd / tvl) * 365.0 / L.HIVE_USD_ASSUMED * 100.0


def _decide(tvl_map=None, weight=None):
    old_w = L.REWARD_PER_HIVE_WEIGHT
    if weight is not None:
        L.REWARD_PER_HIVE_WEIGHT = weight
    try:
        return L.decide(HELD, REWARDS, fee_evidence=FEE,
                        position_economics=ECON,
                        threshold_analysis=THRESH,
                        fee_evidence_override={
                            p: {"fee_apr_pct_authoritative": FEE[p]["fee_apr_pct"]}
                            for p in HELD},
                        price_map=PRICE_MAP,
                        spread_map=SPREAD_MAP,
                        pool_tvl_map=tvl_map)
    finally:
        L.REWARD_PER_HIVE_WEIGHT = old_w


class TestRewardPerHiveScoring(unittest.TestCase):

    def test_richer_per_hive_pool_wins_expansion(self):
        """The anchor case: identical economics, 9x reward-per-HIVE gap ->
        the RICH pool must win the expand candidacy."""
        r = _decide(tvl_map=TVL_MAP)
        self.assertEqual(r.expand_candidate, "CENT:TRIBE",
                         "with identical fee APR and threshold distance, the "
                         "pool paying 9x more reward per deposited HIVE must "
                         "win the expansion")

    def test_term_inert_without_tvl_map(self):
        """No pool_tvl_map -> exactly the pre-2026-10-10 score. Dust-branch
        arithmetic: (fee*1.0 + count*0.1) * viability, no term."""
        r = _decide(tvl_map=None)
        self.assertEqual(r.expand_candidate, "CENT:TRIBE",
                         "no TVL map: legacy order decides, term contributed nothing")
        v = r.verdicts["CENT:TRIBE"]
        self.assertAlmostEqual(v.expand_score, DUST_BASE * VIABILITY, places=6,
                               msg="no TVL map: score must be the pure "
                                   "pre-2026-10-10 dust-branch score")

    def test_weight_zero_disables_term(self):
        """REWARD_PER_HIVE_WEIGHT = 0 must reproduce pre-2026-10-10 scores
        even WITH the TVL map present."""
        r = _decide(tvl_map=TVL_MAP, weight=0.0)
        v = r.verdicts["CENT:POOR"]
        self.assertAlmostEqual(v.expand_score, DUST_BASE * VIABILITY, places=6,
                               msg="weight 0: POOR's score must have NO "
                                   "reward-per-HIVE contribution")

    def test_missing_tvl_for_one_pool_does_not_block_the_term(self):
        """Unknown TVL for CENT:POOR alone: CENT:TRIBE still gets its term
        (unknown != zero, and one pool's gap never silences the others)."""
        r = _decide(tvl_map={"CENT:TRIBE": 14398.0})   # POOR's TVL missing
        self.assertEqual(r.expand_candidate, "CENT:TRIBE")
        v_rich = r.verdicts["CENT:TRIBE"]
        expected = (DUST_BASE
                    + _rph_apr_eq(1.80, 14398.0) * L.REWARD_PER_HIVE_WEIGHT) * VIABILITY
        self.assertAlmostEqual(v_rich.expand_score, expected, places=6)

    def test_scores_carry_the_term_in_apr_equivalent_units(self):
        """The term's units, pinned by hand-check: rph 1.80/14398 = 1.25e-4
        USD/day/HIVE -> APR-equiv 1.25e-4*365/0.057*100 = 79.99 points ->
        (5.1 + 79.99) * 1.6 = 136.16. Matches the live code exactly."""
        r = _decide(tvl_map=TVL_MAP)
        v = r.verdicts["CENT:TRIBE"]
        expected = (DUST_BASE
                    + _rph_apr_eq(1.80, 14398.0) * L.REWARD_PER_HIVE_WEIGHT
                    * L.FEE_APR_SCORE_WEIGHT) * VIABILITY
        self.assertAlmostEqual(v.expand_score, expected, places=6)
        # and the POOR pool's smaller term is present too
        v_poor = r.verdicts["CENT:POOR"]
        expected_poor = (DUST_BASE
                         + _rph_apr_eq(0.026, 1819.0) * L.REWARD_PER_HIVE_WEIGHT
                         * L.FEE_APR_SCORE_WEIGHT) * VIABILITY
        self.assertAlmostEqual(v_poor.expand_score, expected_poor, places=6)


if __name__ == "__main__":
    unittest.main()
