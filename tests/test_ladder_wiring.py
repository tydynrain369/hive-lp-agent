"""Wiring tests for the LP priority ladder (Tydyn's 2026-11-02 call).

Two contracts pinned:
  1. DECISION layer emits `ladder` + `ladder_destination`, and the destination
     is always a legitimate destination (expand/hold), never an exit/consolidate
     (release) pool — no contradictory artifact.
  2. EXECUTOR (`plan_idle_deploy`) deploys to `ladder_destination` (spilling down
     a rung is the point), while refusing anything that is not a real
     destination.

NO CAPITAL. NO CHAIN. Stubs + pure functions.

Run:
  ~/hive-agent-test/.venv/bin/python -m pytest tests/test_ladder_wiring.py -q
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import lp_decision as L
import lp_realloc_executor as E


# --------------------------------------------------------------------------
# decision-layer contract
# --------------------------------------------------------------------------
HELD = ["NEAR:SWAP.HIVE", "FAR:SWAP.HIVE", "WEAK:SWAP.HIVE"]
FEE = {"NEAR:SWAP.HIVE": {"fee_apr_pct": 4.0},
       "FAR:SWAP.HIVE": {"fee_apr_pct": 9.0},
       "WEAK:SWAP.HIVE": {"fee_apr_pct": 0.0}}
OVERRIDE = {"NEAR:SWAP.HIVE": {"fee_apr_pct_authoritative": 4.0},
            "FAR:SWAP.HIVE": {"fee_apr_pct_authoritative": 9.0},
            "WEAK:SWAP.HIVE": {"fee_apr_pct_authoritative": 0.0}}
ECON = {"NEAR:SWAP.HIVE": {"our_position_value_usd": 0.5},
        "FAR:SWAP.HIVE": {"our_position_value_usd": 1e-8},
        "WEAK:SWAP.HIVE": {"our_position_value_usd": 0.2}}
THRESH = {"NEAR:SWAP.HIVE": {"computable": True, "multiple_to_1e6_fee_threshold": 0.5},
          "FAR:SWAP.HIVE": {"computable": True, "multiple_to_1e6_fee_threshold": 40.0},
          "WEAK:SWAP.HIVE": {"computable": True, "multiple_to_1e6_fee_threshold": 0.8}}
TVL_HIVE = {"NEAR:SWAP.HIVE": 1000.0, "FAR:SWAP.HIVE": 100.0, "WEAK:SWAP.HIVE": 500.0}
PRICE = {"SWAP.HIVE": 0.05}


class TestDecisionLadderContract(unittest.TestCase):
    def setUp(self):
        self.r = L.decide(HELD, {}, fee_evidence=FEE, position_economics=ECON,
                          threshold_analysis=THRESH, fee_evidence_override=OVERRIDE,
                          price_map=PRICE, pool_tvl_map=TVL_HIVE)

    def test_ladder_present_and_sorted(self):
        self.assertTrue(self.r.ladder, "ladder missing/empty on a populated fixture")
        now = [row["rate_now"] for row in self.r.ladder]
        self.assertEqual(now, sorted(now, reverse=True))

    def test_destination_is_a_real_destination(self):
        d = self.r.ladder_destination
        self.assertIsNotNone(d, "no ladder destination emitted")
        self.assertIn(self.r.verdicts[d].action, ("expand", "hold"))

    def test_release_pools_never_become_rungs(self):
        # a 'consolidate'/'exit' pool must never appear as an expand rung
        for row in (self.r.ladder or []):
            self.assertNotIn(self.r.verdicts[row["pool"]].action,
                             ("exit", "consolidate"))

    def test_far_pool_viability_demotes_it(self):
        rows = {r["pool"]: r for r in (self.r.ladder or [])}
        self.assertIn("FAR:SWAP.HIVE", rows)
        # 40x away -> viability floor 0.25, strictly below a near pool's 1.6
        self.assertLess(rows["FAR:SWAP.HIVE"]["viability"],
                        rows["NEAR:SWAP.HIVE"]["viability"])

    def test_no_divergence_note_when_they_agree(self):
        joined = " ".join(self.r.notes)
        if self.r.expand_candidate == self.r.ladder_destination:
            self.assertNotIn("divergence", joined)

    def test_rungs_report_completeness(self):
        for row in (self.r.ladder or []):
            self.assertIn("complete", row)


# --------------------------------------------------------------------------
# executor wiring
# --------------------------------------------------------------------------
def _agent(hive_bal=0.05, hive_usd=0.05):
    a = types.SimpleNamespace()
    a.portfolio = types.SimpleNamespace()
    a.portfolio.balances = {"SWAP.HIVE": types.SimpleNamespace(balance=hive_bal)}
    a.pe = types.SimpleNamespace(token_prices={"SWAP.HIVE": hive_usd},
                                 market_liquidity={},
                                 # 2026-11-13: the plan pre-flight (Opus N1)
                                 # refuses unknown precision — the production
                                 # PriceEngine always carries this table
                                 token_precisions={"SWAP.HIVE": 8, "PEPE": 8,
                                                   "A": 8, "B": 8})
    return a


def _decision(dest, action, expand=None, blocked=None):
    return {
        "ladder_destination": dest,
        "expand_candidate": expand if expand is not None else dest,
        "verdicts": {dest: {"action": action,
                            "expand_blocked_reason": blocked}},
    }


_SIZED = {"status": "sized", "base_token": "PEPE", "quote_token": "SWAP.HIVE"}


class TestExecutorPrefersLadder(unittest.TestCase):
    def _plan(self, decision):
        ag = _agent()
        with patch.object(E, "_size_deposit_stage", return_value=dict(_SIZED)), \
             patch.object(E, "_idle_deploy_throttled", return_value=False):
            return E.plan_idle_deploy(ag, decision)

    def test_deploys_to_ladder_destination_when_it_differs(self):
        # ladder says B (a demoted 'hold' runner-up), composite says A
        d = {"ladder_destination": "B:SWAP.HIVE", "expand_candidate": "A:SWAP.HIVE",
             "verdicts": {"B:SWAP.HIVE": {"action": "hold"},
                          "A:SWAP.HIVE": {"action": "expand"}}}
        out = self._plan(d)
        self.assertEqual(out["action"], "deploy_idle")
        self.assertEqual(out["pool"], "B:SWAP.HIVE")

    def test_hold_at_ladder_destination_is_deployable(self):
        # 2026-11-13: the pre-flight's precision check exposed a fixture typo
        # here — "B:SWAP:HIVE" (colon) named a token that does not exist.
        out = self._plan(_decision("B:SWAP.HIVE", "hold"))
        self.assertEqual(out["action"], "deploy_idle")

    def test_exit_destination_refused(self):
        out = self._plan(_decision("B:SWAP:HIVE", "exit"))
        self.assertNotEqual(out["action"], "deploy_idle")

    def test_consolidate_destination_refused(self):
        out = self._plan(_decision("B:SWAP:HIVE", "consolidate"))
        self.assertNotEqual(out["action"], "deploy_idle")

    def test_blocked_destination_refused(self):
        out = self._plan(_decision("B:SWAP:HIVE", "hold", blocked="runway too short"))
        self.assertNotEqual(out["action"], "deploy_idle")

    def test_falls_back_to_expand_candidate_without_ladder(self):
        d = {"ladder_destination": None, "expand_candidate": "A:SWAP.HIVE",
             "verdicts": {"A:SWAP.HIVE": {"action": "expand"}}}
        out = self._plan(d)
        self.assertEqual(out["pool"], "A:SWAP.HIVE")


if __name__ == "__main__":
    unittest.main()
