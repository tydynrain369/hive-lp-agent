"""Hardening pins from the 2026-11-13 Claude Code review.

Each test pins a fail-CLOSED behavior the review found open. The review is a
hypothesis set; these tests are the verdicts, so the behaviors cannot silently
revert:

  P0-1   live_armed() chokepoint inside TestExecutor — env alone is not consent
  P0-11  unknown position value refuses instead of removing uncapped
  P0-4   route cost: a needed-but-unpriced conversion REFUSES (was: passed)
  P0-6/7 fee APR UNKNOWN holds — never exit/consolidate on an unmeasured figure
  P0-8   unvalued dividends hold — never release on a pricing gap

Run:  bare `pytest` from ~/.hermes/scripts (testpaths pinned by pytest.ini).
"""
import os
import sys
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")

import liquidity_agent_v2 as LAV2
import lp_executor_test as LE
import lp_realloc_executor as X
import lp_decision as D


# ─── fixtures ────────────────────────────────────────────────────────
class _Pos:
    def __init__(self, shares, value_usd):
        self.shares = shares
        self.value_usd = value_usd


class _Portfolio:
    def __init__(self, positions):
        self.positions = positions


class _Agent:
    def __init__(self, positions=None, pe=None):
        self.portfolio = _Portfolio(positions or {})
        if pe is not None:
            self.pe = pe


class _EmptyPE:
    """A price engine that knows no pools — no adjacency, no route."""
    pools = {}


# ─── P0-1: the chokepoint ────────────────────────────────────────────
class TestLiveChokepoint(unittest.TestCase):
    """LP_TEST_LIVE=1 in the env must NOT be sufficient to sign."""

    def setUp(self):
        self._saved_live = LE.LIVE_MODE
        self._saved_armed = LAV2.live_armed

    def tearDown(self):
        LE.LIVE_MODE = self._saved_live
        LAV2.live_armed = self._saved_armed

    def test_remove_refuses_when_only_env_is_set(self):
        LE.LIVE_MODE = True
        LAV2.live_armed = lambda: (False, "AUTONOMY_ON flag absent")
        ex = LE.TestExecutor(_Agent({"TOK:HUB": _Pos(100.0, 0.01)}))
        entry = ex.remove_liquidity("TOK:HUB", 100.0, reason="chokepoint test")
        self.assertEqual(entry["status"], "skipped")
        self.assertIn("not armed", entry["note"])

    def test_add_refuses_when_only_env_is_set(self):
        LE.LIVE_MODE = True
        LAV2.live_armed = lambda: (False, "AUTONOMY_ON flag absent")
        ex = LE.TestExecutor(_Agent({}))
        entry = ex.add_liquidity("TOK:HUB", 1.0, 1.0, 0.01,
                                 reason="chokepoint test")
        self.assertEqual(entry["status"], "skipped")
        self.assertIn("not armed", entry["note"])


# ─── P0-11: unknown value is not zero ────────────────────────────────
class TestUnknownValueRefuses(unittest.TestCase):

    def test_remove_with_unknown_value_skips(self):
        # value_usd None coerced to 0.0 used to make req_value 0, which never
        # exceeds the cap — the shares sailed through UNCAPPED.
        ex = LE.TestExecutor(_Agent({"TOK:HUB": _Pos(100.0, None)}))
        entry = ex.remove_liquidity("TOK:HUB", 100.0, reason="unknown-value")
        self.assertEqual(entry["status"], "skipped")
        self.assertIn("cap not computable", entry["note"])

    def test_remove_with_zero_value_skips(self):
        ex = LE.TestExecutor(_Agent({"TOK:HUB": _Pos(100.0, 0.0)}))
        entry = ex.remove_liquidity("TOK:HUB", 100.0, reason="zero-value")
        self.assertEqual(entry["status"], "skipped")

    def test_known_value_still_reaches_dry_run(self):
        # the guard must not eat legitimate positions
        ex = LE.TestExecutor(_Agent({"TOK:HUB": _Pos(100.0, 0.01)}))
        entry = ex.remove_liquidity("TOK:HUB", 100.0, reason="control")
        self.assertEqual(entry["status"], "dry-run")


# ─── P0-4: route cost fails closed ───────────────────────────────────
class TestRouteCostFailClosed(unittest.TestCase):

    def test_needed_but_unroutable_refuses(self):
        # a released token needs conversion, but the graph has no pools:
        # the old code returned not-priced and the plan PROCEEDED.
        out = X._route_cost_check(_Agent(pe=_EmptyPE()), "SRC:POOL",
                                  "DST:POOL", {"TOK": 5.0})
        self.assertEqual(out["status"], "refused")
        self.assertIn("no route could be priced", out["verdict"])

    def test_missing_price_engine_refuses_when_conversion_needed(self):
        out = X._route_cost_check(_Agent(pe=None), "SRC:POOL", "DST:POOL",
                                  {"TOK": 5.0})
        self.assertEqual(out["status"], "refused")
        self.assertIn("no price engine", out["verdict"])

    def test_no_conversion_needed_stays_not_priced(self):
        # released tokens are all destination tokens — nothing to price,
        # nothing to refuse. This healthy case must keep flowing.
        out = X._route_cost_check(_Agent(pe=None), "SRC:POOL", "DST:POOL",
                                  {"DST": 3.0})
        self.assertEqual(out["status"], "not-priced")
        self.assertIn("no released token requires", out["detail"])


# ─── P0-6/P0-7: UNKNOWN APR holds, never exits ───────────────────────
class TestUnknownAprHolds(unittest.TestCase):

    def test_unmeasured_apr_holds_and_never_exits(self):
        # local fallback says 0.004% (would have exited under the old code)
        r = D.decide(["TOK:HUB"], {},
                     fee_evidence={"TOK:HUB": {"fee_apr_pct": 0.004}})
        v = r.verdicts["TOK:HUB"]
        self.assertEqual(v.action, "hold")
        self.assertIn("UNKNOWN", v.reason)
        self.assertNotIn("TOK:HUB", r.exit_candidates)

    def test_measured_near_zero_apr_still_exits(self):
        # None is not 0: a MEASURED ~0 APR with no rewards is a legitimate exit
        r = D.decide(["TOK:HUB"], {},
                     fee_evidence={"TOK:HUB":
                                   {"fee_apr_pct_authoritative": 0.005}})
        v = r.verdicts["TOK:HUB"]
        self.assertEqual(v.action, "exit")
        self.assertIn("TOK:HUB", r.exit_candidates)


# ─── P0-8: unvalued rewards hold, never release ──────────────────────
class TestUnvaluedDividendHolds(unittest.TestCase):

    def test_rewards_present_but_unpriced_holds(self):
        # APR between 0.01 and MIN_FEE_APR_FOR_PRODUCTIVE, rewards exist, but
        # no price map → dividend unvalued. The old fall-through emitted
        # consolidate (a release instruction) on a PRICING gap.
        r = D.decide(["TOK:HUB"],
                     {"TOK:HUB": [{"mined_token": "GLOW"}]},
                     fee_evidence={"TOK:HUB":
                                   {"fee_apr_pct_authoritative": 0.05}})
        v = r.verdicts["TOK:HUB"]
        self.assertEqual(v.action, "hold")
        self.assertIn("unvalued", v.reason)
        self.assertNotEqual(v.action, "consolidate")


if __name__ == "__main__":
    unittest.main()
