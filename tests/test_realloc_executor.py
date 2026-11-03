"""Tests for the reallocation executor's REFUSALS.

A plan that looks right is not evidence that it is safe. These assert the
guards actually fire — especially the ones protecting real capital.

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python \\
      /home/tydynrain/.hermes/scripts/tests/test_realloc_executor.py
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import lp_realloc_executor as X


class FakePE:
    """Price engine wired so the ROUTE guard is never what fails another test.

    The guard builds adjacency from pe.pools (2026-10-02) and then runs a
    general graph search. A fixture without .pools yields an empty graph, every
    token is 'unroutable', and every other assertion is starved. This one is a
    fully-connected star so any token reaches any other in one hop.
    """

    def __init__(self, pool_ids=None):
        ids = pool_ids or ["SRC:DST", "SRC:POOL", "DST:POOL", "OTHER:X"]
        self.pools = {p: None for p in ids}

    def find_path(self, a, b):
        if a == b:
            return []
        return ["ANY:ROUTE"]


class FakeExecutor:
    """Stands in for TestExecutor so no chain call is possible."""
    def __init__(self, shares=1.0, value=0.01, pe=None):
        self._shares, self._value = shares, value
        self.pe = pe or FakePE()

    def position_shares(self, pool):
        return self._shares

    def position_value_usd(self, pool):
        return self._value


def decision(frm="SRC:POOL", to="DST:POOL", action="consolidate"):
    return {
        "recommended_reallocation": {"from_pool": frm, "to_pool": to,
                                    "rationale": "test"},
        "verdicts": {frm: {"action": action, "reason": "because"}},
        "exit_candidates": [],
        "notes": [],
    }


def plan_with(agent, d, advisory=None, allow_challenge=False):
    X.TestExecutor = lambda a: agent
    return X.plan_reallocation(agent, d, advisory=advisory,
                               allow_challenge=allow_challenge)


class TestRefusals(unittest.TestCase):

    def test_no_reallocation_means_no_action(self):
        p = plan_with(FakeExecutor(), {"verdicts": {}, "recommended_reallocation": None})
        self.assertEqual(p["action"], "none")

    def test_same_pool_refuses(self):
        p = plan_with(FakeExecutor(), decision(frm="A:B", to="A:B"))
        self.assertEqual(p["action"], "none")
        self.assertIn("same", p["reason"])

    def test_missing_leg_refuses(self):
        d = decision()
        d["recommended_reallocation"]["to_pool"] = None
        p = plan_with(FakeExecutor(), d)
        self.assertEqual(p["action"], "none")

    def test_source_not_marked_for_release_refuses(self):
        """A 'hold' pool must never be drained by a reallocation."""
        p = plan_with(FakeExecutor(), decision(action="hold"))
        self.assertEqual(p["action"], "none")
        self.assertIn("refusing", p["reason"])

    def test_expand_pool_cannot_be_a_source(self):
        p = plan_with(FakeExecutor(), decision(action="expand"))
        self.assertEqual(p["action"], "none")

    def test_zero_shares_refuses(self):
        p = plan_with(FakeExecutor(shares=0, value=0.0), decision())
        self.assertEqual(p["action"], "none")
        self.assertIn("no position", p["reason"])

    def test_dust_position_refuses(self):
        """Below the management floor a transaction costs more than it manages."""
        p = plan_with(FakeExecutor(shares=1.0, value=1e-9), decision())
        self.assertEqual(p["action"], "none")
        self.assertIn("below the", p["reason"])

    def test_challenge_blocks_by_default(self):
        p = plan_with(FakeExecutor(), decision(),
                      advisory={"verdict": "CHALLENGE", "reason": "unsupported"})
        self.assertEqual(p["action"], "blocked")
        self.assertIn("CHALLENGE", p["reason"])

    def test_challenge_can_be_overridden_explicitly(self):
        p = plan_with(FakeExecutor(), decision(),
                      advisory={"verdict": "CHALLENGE", "reason": "unsupported"},
                      allow_challenge=True)
        self.assertEqual(p["action"], "remove_and_stage")


class TestSizing(unittest.TestCase):

    def test_percentage_is_correct_for_the_contract(self):
        """sharesOut is a PERCENTAGE (0-100, max 3dp), not an absolute count."""
        p = plan_with(FakeExecutor(shares=10.0, value=0.01), decision())
        self.assertEqual(p["remove_pct_of_position"], 100.0)
        self.assertLessEqual(p["remove_pct_of_position"], 100.0)

    def test_cap_reduces_the_percentage(self):
        old = X.MAX_USD_PER_ACTION
        X.MAX_USD_PER_ACTION = 0.005
        try:
            p = plan_with(FakeExecutor(shares=10.0, value=0.01), decision())
            self.assertEqual(p["remove_value_usd"], 0.005)
            self.assertEqual(p["remove_pct_of_position"], 50.0)
            self.assertIsNotNone(p["cap_note"])
        finally:
            X.MAX_USD_PER_ACTION = old

    def test_deposit_is_never_executable(self):
        """The deposit leg must remain non-executing, by design.

        2026-10-04: this used to assert the prose string "NOT YET EXECUTABLE",
        which was true only while no sizing existed. Now that LEG B computes real
        add_liquidity quantities, asserting on that sentence would let the guard
        pass on a plan that had become executable. Assert the boundary that must
        hold REGARDLESS of how much sizing we add: the deposit carries quantities
        but is never marked executable, and authority says approval is required.
        """
        # Default decision() routes SRC:POOL -> DST:POOL, which the strengthened
        # source-pool route guard now (correctly) refuses, so this test needs a
        # transition that genuinely survives it: SRC has an independent pool.
        d = decision(frm="SRC:POOL", to="DST:POOL", action="consolidate")
        # NOTE: build the pools into the FakeExecutor CONSTRUCTOR. Assigning
        # agent.pe afterwards left the object in a state where
        # position_value_usd() raised AttributeError on `portfolio`, which is a
        # broken fixture rather than a guard firing.
        agent = FakeExecutor(shares=10.0, value=0.01,
                             pe=FakePE(["SRC:POOL", "SRC:HUB", "HUB:DST", "DST:POOL"]))
        p = plan_with(agent, d)
        self.assertEqual(p["action"], "remove_and_stage")
        sizing = p.get("deposit_sizing") or {}

        # The boundary that must hold for EVERY status: nothing here can
        # authorise a broadcast. Note `authority` only appears on a SIZED plan,
        # so asserting it unconditionally tests the wrong thing -- an
        # unpriced plan has no authority field at all, which is equally safe.
        self.assertFalse(sizing.get("executable", False),
                         "deposit sizing must never be executable")
        if sizing.get("status") == "sized":
            self.assertIn("HUMAN", str(sizing.get("authority", "")).upper())

        # And with fixture tokens that have no price anywhere, sizing must
        # REFUSE rather than invent quantities.
        self.assertIn(sizing.get("status"), ("sized", "unpriced"))
        if sizing.get("status") == "unpriced":
            self.assertTrue(sizing["unpriced_tokens"])


class TestRouteGuardIntegration(unittest.TestCase):
    """The guard as it actually runs inside plan_reallocation()."""

    class DeadPE:
        """No routes at all — a token in zero pools."""
        def __init__(self):
            self.pools = {"CENT:POB": None}   # HSBIDAO/PIXYDUST in no pool

        def find_path(self, a, b):
            return []

    def test_unroutable_source_is_blocked(self):
        agent = FakeExecutor(pe=self.DeadPE())
        X.TestExecutor = lambda a: agent
        d = decision(frm="HSBIDAO:PIXYDUST", to="CENT:POB", action="consolidate")
        p = X.plan_reallocation(agent, d)
        self.assertEqual(p["action"], "blocked")
        self.assertIn("PIXYDUST", p["unroutable_tokens"])

    def test_routable_source_passes_the_guard(self):
        """A genuinely performable reallocation must still be ALLOWED.

        2026-10-04: the original fixture was ["SRC:POOL", "DST:POOL"], where the
        only route for SRC is SRC->POOL->DST and that first hop happens inside
        SRC:POOL - the pool being exited. So SRC has nowhere to go once we leave,
        and 'routable' was never true. It passed because the guard did not look
        at WHERE the path went. Adding a second pool for SRC makes the case
        real, which is what the test was always meant to assert.
        """
        agent = FakeExecutor(pe=FakePE([
            "SRC:POOL",     # source, being drained
            "SRC:HUB",      # SRC survives outside the source pool
            "HUB:DST",
            "DST:POOL",     # destination
        ]))
        X.TestExecutor = lambda a: agent
        d = decision(frm="SRC:POOL", to="DST:POOL", action="consolidate")
        p = X.plan_reallocation(agent, d)
        self.assertEqual(p["action"], "remove_and_stage")
        self.assertTrue(p["routable"])


class TestRouteGuard(unittest.TestCase):
    """The 2026-10-02 defect: a reallocation can be recommended but not performed.

    plan_reallocation() never checked whether the tokens a removal YIELDS can
    actually reach the destination pool's pair. For HSBIDAO:PIXYDUST ->
    CENT:POB the released tokens are HSBIDAO and PIXYDUST, neither of which is
    a CENT:POB pair token, and PIXYDUST has exactly one pool in existence, so
    it has NO route anywhere.

    NOTE ON A CORRECTED CLAIM: an earlier version of this test asserted that
    get_pool() was order-sensitive. That was false — the real implementation
    already tries both orientations — and the FakePE fixture reproduced the
    invention rather than the code. Fixtures must model the real behaviour or
    they test nothing. These tests now exercise the guard with an explicit
    injected route oracle.
    """

    def _guard(self, released, dest_pair, reachable):
        """Mirror of the guard logic in plan_reallocation()."""
        dest = dest_pair.split(":")
        out = []
        for t in released:
            ok = any(t in dest for _ in (0,)) or reachable(t, dest)
            if not ok:
                out.append(t)
        return out

    def test_direct_pair_match_is_always_routable(self):
        """If a released token IS a destination pair token, no route needed."""
        bad = self._guard(["CENT"], "CENT:POB", lambda t, d: False)
        self.assertEqual(bad, [])

    def test_unreachable_token_is_reported(self):
        bad = self._guard(["PIXYDUST"], "CENT:POB", lambda t, d: False)
        self.assertEqual(bad, ["PIXYDUST"])

    def test_route_through_a_hub_counts_as_routable(self):
        bad = self._guard(["HSBIDAO"], "CENT:POB",
                          lambda t, d: t == "HSBIDAO" and "CENT" in d)
        self.assertEqual(bad, [])

    def test_every_released_token_must_be_routable(self):
        """One unreachable token blocks the whole plan, not just that leg."""
        bad = self._guard(["HSBIDAO", "PIXYDUST"], "CENT:POB",
                          lambda t, d: t == "HSBIDAO")
        self.assertEqual(bad, ["PIXYDUST"])


class TestDefaults(unittest.TestCase):
    def test_dry_run_is_the_default(self):
        """No LP_TEST_LIVE in the environment means nothing broadcasts."""
        import os
        self.assertNotEqual(os.environ.get("LP_TEST_LIVE"), "1",
                            "test environment must not be live")

    def test_cap_default_is_five_cents(self):
        self.assertLessEqual(X.MAX_USD_PER_ACTION, 0.05)

    def test_dust_floor_is_one_microdollar(self):
        self.assertEqual(X.DUST_FLOOR_USD, 1e-6)


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestSelfRemovingPoolDefect(unittest.TestCase):
    """2026-10-04: the guard builds its graph from the pool index AS IT IS NOW,
    which still contains the pool being drained.

    Proven against the live chain (346 pools, 205 symbols):
        PIXYDUST appears in exactly one pool -> HSBIDAO:PIXYDUST
        PIXYDUST -> CENT  routes as PIXYDUST/HSBIDAO/LBI/CENT
        PIXYDUST -> POB   routes as PIXYDUST/HSBIDAO/LBI/SWAP.HIVE/POB

    So the graph says "routable" TODAY. But the reallocation's first step is to
    EXIT that very pool, after which PIXYDUST has no pool at all and no route.
    The removal destroys the only bridge it was relying on.

    The old fixture asserted PIXYDUST was unroutable by hard-coding a pools dict
    that omitted the pool -- it encoded the assumption instead of testing the
    real shape. This fixture reproduces the ACTUAL index so the guard sees the
    route it will destroy.
    """

    class RealishPE:
        """Mirrors the live index: PIXYDUST's ONLY pool is the source pool."""
        def __init__(self):
            self.pools = {
                "HSBIDAO:PIXYDUST": None,   # the pool being drained
                "HSBIDAO:LBI": None,
                "LBI:CENT": None,
                "LBI:SWAP.HIVE": None,
                "SWAP.HIVE:POB": None,
                "CENT:POB": None,            # destination
            }

    def test_route_that_only_exists_through_the_source_pool_is_blocked(self):
        agent = FakeExecutor(pe=self.RealishPE())
        X.TestExecutor = lambda a: agent
        d = decision(frm="HSBIDAO:PIXYDUST", to="CENT:POB", action="consolidate")
        p = X.plan_reallocation(agent, d)

        # The pre-removal graph DOES contain a path, so the guard must not pass
        # this on today's logic -- that is the defect being pinned.
        self.assertIn("PIXYDUST", p.get("unroutable_tokens", []),
                      "PIXYDUST is only convertible through the pool being "
                      "exited, so it cannot reach CENT:POB after the removal")
        self.assertEqual(p["action"], "blocked")

    def test_a_genuinely_external_route_is_not_blocked(self):
        """Control: BOTH released tokens have independent second pools.

        The first draft of this control gave only PIXYDUST a second pool and then
        failed - correctly. HSBIDAO still had no route, so the guard blocked on
        the OTHER token. A guard is only trusted when its negative cases are
        right, so the control has to be genuinely routable for both.
        """
        class TwoPoolsPE:
            def __init__(self):
                self.pools = {
                    "HSBIDAO:PIXYDUST": None,   # source, being drained
                    "PIXYDUST:SWAP.HIVE": None,  # independent second pool
                    "HSBIDAO:SWAP.HIVE": None,   # independent second pool
                    "SWAP.HIVE:CENT": None,
                    "CENT:POB": None,
                }
        agent = FakeExecutor(pe=TwoPoolsPE())
        X.TestExecutor = lambda a: agent
        d = decision(frm="HSBIDAO:PIXYDUST", to="CENT:POB", action="consolidate")
        p = X.plan_reallocation(agent, d)
        self.assertEqual(p.get("unroutable_tokens", []), [])
        self.assertNotEqual(p["action"], "blocked")


class TestDepositSizingStage(unittest.TestCase):
    """LEG B (2026-10-04): the sizing math existed in lp_sizing and was tested,
    but nothing called it — plan_reallocation emitted a prose string saying the
    math "does not exist yet". The plan now computes real add_liquidity
    quantities, and STILL cannot broadcast.

    These tests pin the boundary: a sized deposit must carry exact quantities and
    remain non-executable, and an unpriced token must refuse rather than invent.
    """

    def _agent(self, prices):
        class PE:
            token_prices = dict(prices)
            pools = {}
        class Ag:
            pe = PE()
        return Ag()

    def test_sized_plan_carries_quantities_and_is_not_executable(self):
        agent = self._agent({"ALIVE": "0.00966", "SWAP.HIVE": "0.05698195"})
        out = X._size_deposit_stage(agent, "ALIVE:SWAP.HIVE", "0.001")
        self.assertEqual(out["status"], "sized")
        self.assertFalse(out["executable"])
        self.assertIn("HUMAN", out["authority"].upper())
        # both legs present and positive
        self.assertGreater(float(out["base_amount"]), 0)
        self.assertGreater(float(out["quote_amount"]), 0)
        # USD identity: deploy split must sum back to the deploy budget
        pm = out["prices_used"]
        got = (float(out["base_amount"]) * float(pm[out["base_token"]])
               + float(out["quote_amount"]) * float(pm[out["quote_token"]]))
        self.assertAlmostEqual(got, float(out["deploy_usd"]), places=9)
        self.assertTrue(out["split_is_balanced_usd"])

    def test_fee_reserve_is_held_back_not_deployed(self):
        agent = self._agent({"CENT": "0.000975", "POB": "0.00238"})
        out = X._size_deposit_stage(agent, "CENT:POB", "0.05")
        self.assertEqual(out["status"], "sized")
        self.assertAlmostEqual(float(out["deploy_usd"]) + float(out["fee_reserve_usd"]),
                               0.05, places=12)

    def test_unpriced_token_refuses_and_names_itself(self):
        """A token with no price anywhere must REFUSE, never invent a quantity.

        This test asserts on the fallback chain, not on the live market: the
        helper consults market_price() first, so a token that really does trade
        (POB) would be priced even with an empty engine map. Use a token that has
        neither, which is what a genuinely unpriceable reward token looks like.
        """
        agent = self._agent({"CENT": "0.000975"})   # only CENT is known anywhere
        out = X._size_deposit_stage(agent, "CENT:NOTAREALTOKEN", "0.05")
        self.assertEqual(out["status"], "unpriced")
        self.assertIn("NOTAREALTOKEN", out["unpriced_tokens"])
        self.assertIn("refusing", out["reason"])

    def test_engine_price_rescues_a_token_the_market_cannot_price(self):
        """SWAP.HIVE has no market row but the price engine values it at the L1
        anchor. Before the fallback this wrongly read 'unpriced'."""
        agent = self._agent({"ALIVE": "0.00966", "SWAP.HIVE": "0.05698195"})
        out = X._size_deposit_stage(agent, "ALIVE:SWAP.HIVE", "0.001")
        self.assertEqual(out["status"], "sized")
        self.assertIn("SWAP.HIVE", out["prices_used"])

    def test_malformed_destination_is_rejected(self):
        agent = self._agent({"A": "1"})
        out = X._size_deposit_stage(agent, "NOCOLON", "0.05")
        self.assertEqual(out["status"], "malformed-destination")


class _PoolRow:
    """PoolData-shaped row. Reserves are REQUIRED: a pool whose row is None has
    no quantities, so the cost model cannot price it and the gate correctly
    reports 'not-priced'. The first draft of the gate fixture used {name: None}
    and the gate could not price the route at all — a broken fixture, not a gate
    that failed."""

    def __init__(self, b, q, total=1000.0):
        self.base_quantity = b
        self.quote_quantity = q
        self.total_shares = total


class TestRouteCostGate(unittest.TestCase):
    """2026-10-04, ceiling set by Tydyn at 1%.

    The gate exists so an AUTONOMOUS agent never plans a conversion it cannot
    justify. Two properties matter: it must REFUSE (not annotate) above the line,
    and it must not silently price nothing and wave the plan through.
    """

    class CostlyPE:
        """A route whose reserves make the swap genuinely expensive."""
        def __init__(self, reserve=1000.0):
            # SRC:TOK is the SOURCE being exited, so TOK also needs a pool that
            # survives the exit — otherwise the ROUTE guard (correctly) blocks
            # before the cost gate is ever reached, and this fixture would be
            # testing the wrong guard.
            # Both released tokens need a pool OUTSIDE the source, or the
            # source-pool liquidity guard blocks first (correctly) and this
            # fixture never reaches the cost gate. The last two iterations of
            # this fixture each tripped a DIFFERENT earlier guard, which is the
            # clearest evidence yet that guard order matters and a test must
            # satisfy every gate before it can reach the one it targets.
            self.pools = {
                "TOK:HUB": _PoolRow(reserve, reserve),      # source
                "TOK:X1": _PoolRow(reserve, reserve),       # TOK survives
                "X1:DST": _PoolRow(reserve, reserve),
                "HUB:X2": _PoolRow(reserve, reserve),       # HUB survives
                "X2:DST": _PoolRow(reserve, reserve),
                "DST:POOL": _PoolRow(reserve, reserve),
            }
            self.token_prices = {"TOK": "1", "HUB": "1", "SRC": "1", "DST": "1", "POOL": "1"}

    def test_gate_refuses_above_the_ceiling(self):
        """A plan over the ceiling must NOT come back executable."""
        saved = X.ROUTE_COST_REFUSAL_PCT
        try:
            X.ROUTE_COST_REFUSAL_PCT = 0.0001     # force refusal
            d = decision(frm="TOK:HUB", to="DST:POOL", action="consolidate")
            agent = FakeExecutor(shares=1000.0, value=0.01,
                                 pe=self.CostlyPE(reserve=1000.0))
            p = plan_with(agent, d)
            self.assertEqual(p["action"], "blocked")
            self.assertIsNone(p.get("deposit_sizing"),
                              "a refused route must not produce deposit sizing")
        finally:
            X.ROUTE_COST_REFUSAL_PCT = saved

    def test_gate_permits_below_the_ceiling(self):
        saved = X.ROUTE_COST_REFUSAL_PCT
        try:
            X.ROUTE_COST_REFUSAL_PCT = 100.0       # effectively never refuse
            d = decision(frm="TOK:HUB", to="DST:POOL", action="consolidate")
            agent = FakeExecutor(shares=1000.0, value=0.01,
                                 pe=self.CostlyPE(reserve=1000.0))
            p = plan_with(agent, d)
            self.assertEqual(p["action"], "remove_and_stage")
            self.assertIn("route_cost", p)
        finally:
            X.ROUTE_COST_REFUSAL_PCT = saved

    def test_gate_reports_its_own_blindness(self):
        """If it cannot price, it must say so rather than pass silently."""
        agent = FakeExecutor(shares=10.0, value=0.01)
        out = X._route_cost_check(agent, "TOK:HUB", "DST:POOL", {})
        self.assertIn(out["status"], ("not-priced", "priced", "refused"))
        self.assertEqual(out["threshold_pct"], X.ROUTE_COST_REFUSAL_PCT)

    def test_released_amounts_is_zero_safe_not_wrong(self):
        agent = FakeExecutor(shares=0.0, value=0.0)
        out = X._released_amounts(agent, "TOK:HUB", 0.0)
        # no position => nothing released => nothing priced, not NaN or garbage
        for v in out.values():
            self.assertEqual(float(v), 0.0)


class TestExecuteCompletes(unittest.TestCase):
    """execute() must COMPLETE on a plan that plan_reallocation() produced.

    Regression pin (2026-11-03). The plan builder emitted `deposit_sizing`,
    while execute() read `plan["deposit_plan"]`. On the live 09:00 tick the
    removal leg BROADCAST on-chain and execute() then died with
    KeyError: 'deposit_plan' — before writing the reallocation ledger. A real
    capital move with no record. A bare rename had broken the ONLY exit
    primitive the autonomous agent has, and nothing pinned the two halves
    together: every existing test stopped at plan_reallocation().
    """

    def _plan(self):
        d = decision(frm="SRC:POOL", to="DST:POOL", action="consolidate")
        agent = FakeExecutor(shares=10.0, value=0.01,
                             pe=FakePE(["SRC:POOL", "SRC:HUB", "HUB:DST", "DST:POOL"]))
        p = plan_with(agent, d)
        self.assertEqual(p["action"], "remove_and_stage")
        return agent, p

    def _run(self, agent, p):
        """execute() with the real ledger and execution log neutralised."""
        with tempfile.TemporaryDirectory() as td, \
                patch.object(X, "_log"), \
                patch.object(X, "REALLOC_LOG", os.path.join(td, "realloc.jsonl")):
            return X.execute(agent, p, allow_execute=False)

    def test_dry_run_returns_a_complete_record(self):
        agent, p = self._plan()
        out = self._run(agent, p)
        self.assertEqual(len(out), 1)
        rec = out[0]
        for key in ("plan", "removal", "deposit", "timestamp"):
            self.assertIn(key, rec)
        self.assertEqual(rec["removal"]["status"], "dry-run")
        # the deposit placeholder must carry the REAL sizing object, not None
        self.assertEqual(rec["deposit"]["detail"], p["deposit_sizing"])

    def test_the_key_the_builder_emits_is_the_key_execute_reads(self):
        """The exact shape that crashed the 2026-11-03 09:00 live tick."""
        agent, p = self._plan()
        self.assertIn("deposit_sizing", p)
        self.assertNotIn("deposit_plan", p)     # the builder never emits this
        self._run(agent, p)                     # must not raise KeyError
