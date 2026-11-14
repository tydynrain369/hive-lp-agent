"""Tests for the from-idle deploy path (2026-11-13, Opus review round 2).

`execute_idle_deploy` is the ONLY autonomous multi-broadcast path, and until
today it had ZERO tests — while the live ledger showed it looping (PEPE,
11-01/11-02: swap broadcast, then "no pool SWAP.HIVE/SWAP.HIVE", then a
"NO EFFECT" misread of a successful swap). These tests pin the N1 fixes:

  - pre-flight refuses SWAP.HIVE-on-one-side pools BEFORE any broadcast
  - pre-flight refuses unknown token precision
  - a missing leg pool refuses BEFORE the throttle is marked
  - the throttle is marked BEFORE the first broadcast (no hourly re-swap loop)
  - a leg-2 failure keeps the throttle (no re-run next tick)
  - the verify step polls (a laggy read is retried, not misread as NO EFFECT)
  - TestExecutor.add_liquidity quantizes to token precision and recomputes
    the cap from the actual amounts

All chain access is faked (requests.post, LiquidityPool, TestExecutor);
nothing here can touch the real chain.
"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")

import lp_realloc_executor as X
import liquidity_agent_v2 as LAV2
import lp_executor_test as LE
import lp_broadcast_guard as LBG


class _PoolRow:
    """PoolData-shaped row for price_route (deep reserves: fees-only cost)."""

    def __init__(self, b=1_000_000.0, q=1_000_000.0, total=1_000_000.0):
        self.base_quantity = b
        self.quote_quantity = q
        self.total_shares = total


class _Bal:
    def __init__(self, balance):
        self.balance = balance


class _Portfolio:
    def __init__(self, balances):
        self.balances = balances


class FakePE:
    """Price engine with known precisions/prices and deep pool rows."""

    def __init__(self, precisions=None, prices=None, pools=None):
        self.token_precisions = precisions or {
            "SWAP.HIVE": 8, "GLD": 8, "SLV": 8, "ALIVE": 4, "TRIBE": 8,
            "PEPE": 8,
        }
        self.token_prices = prices or {
            "SWAP.HIVE": 0.3, "GLD": 1.0, "SLV": 2.0, "ALIVE": 0.01,
        }
        self.pools = pools if pools is not None else {
            "SWAP.HIVE:GLD": _PoolRow(),
            "SWAP.HIVE:SLV": _PoolRow(),
            "SWAP.HIVE:ALIVE": _PoolRow(),
            "SWAP.HIVE:PEPE": _PoolRow(),
            "GLD:SLV": _PoolRow(),
        }
        self.market_liquidity = {}

    def round_amount(self, symbol, amount):
        """Same contract as PriceEngine.round_amount: floor at the token's
        precision; an unknown symbol REFUSES rather than inventing 8."""
        import math
        prec = self.token_precisions.get(symbol)
        if prec is None:
            raise ValueError(
                f"unknown precision for {symbol!r}: refusing to size "
                f"capital against an invented precision")
        return math.floor(amount * 10 ** prec) / 10 ** prec


class FakeAgent:
    def __init__(self, pe=None):
        self.pe = pe or FakePE()
        self.acct = {"account": "testacct"}
        self.portfolio = _Portfolio({"SWAP.HIVE": _Bal(1.0)})


class FakeChain:
    """The Hive-Engine contract layer, faked. `post` answers _find queries;
    `fake_lp.swap_tokens` moves balances the way the chain would."""

    def __init__(self, balances=None, pools=None, positions=None):
        self.balances = dict(balances or {})      # (acct, sym) -> "float str"
        self.pools = dict(pools or {})            # pair -> {base/quoteQuantity}
        self.positions = dict(positions or {})    # (acct, pair) -> "shares"
        self.swaps = []                           # recorded swap_tokens calls
        self.fail_symbol = None                   # symbol to refuse on swap

    # -- the RPC surface execute_idle_deploy reads through ----------------
    def post(self, url, json=None, timeout=None):
        params = (json or {}).get("params", {})
        contract, table = params.get("contract"), params.get("table")
        query = params.get("query", {})
        result = []
        if contract == "tokens" and table == "balances":
            bal = self.balances.get((query.get("account"), query.get("symbol")))
            if bal is not None:
                result = [{"balance": bal, "symbol": query.get("symbol")}]
        elif contract == "marketpools" and table == "pools":
            pair = query.get("tokenPair")
            if pair in self.pools:
                result = [dict(self.pools[pair], tokenPair=pair)]
        elif contract == "marketpools" and table == "liquidityPositions":
            key = (query.get("account"), query.get("tokenPair"))
            if key in self.positions:
                result = [{"shares": self.positions[key],
                           "tokenPair": query.get("tokenPair")}]
        return _Resp({"result": result})

    # -- the signing surface (LiquidityPool.swap_tokens), faked -----------
    def swap_tokens(self, account, token_pair, token_symbol, token_amount,
                    trade_type, min_amount_out=None):
        # a refused swap is NOT a broadcast — record only completed swaps
        # (the ledger must distinguish an attempt from a landed transaction).
        # token_symbol is ALWAYS the input (SWAP.HIVE); fail_symbol matches
        # the OUTPUT token of the leg.
        parts = token_pair.split(":")
        out_sym = parts[1] if token_symbol == parts[0] else parts[0]
        if self.fail_symbol and out_sym == self.fail_symbol:
            raise RuntimeError("simulated on-chain refusal")
        self.swaps.append({"pair": token_pair, "symbol": token_symbol,
                           "amount": float(token_amount),
                           "min_out": (str(min_amount_out)
                                       if min_amount_out is not None else None)})
        # move the money the way the contract would: per_hive from the pair
        row = self.pools.get(token_pair)
        assert row is not None, f"fake chain has no pool {token_pair}"
        b = float(row["baseQuantity"])
        q = float(row["quoteQuantity"])
        in_sym, out_sym = ((token_pair.split(":")[0], token_pair.split(":")[1])
                           if token_symbol == token_pair.split(":")[0]
                           else (token_pair.split(":")[1], token_pair.split(":")[0]))
        per_hive = (q / b) if token_pair.split(":")[0] == in_sym and in_sym == "SWAP.HIVE" \
            else (b / q)
        leg = float(token_amount)
        out_amount = leg * abs(per_hive)
        in_key = (account, in_sym)
        out_key = (account, out_sym)
        self.balances[in_key] = repr(float(self.balances.get(in_key, "0")) - leg)
        self.balances[out_key] = repr(float(self.balances.get(out_key, "0"))
                                      + out_amount)
        return {"trx_id": f"fake{len(self.swaps)}"}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeLP:
    """Patched in for nectarengine.pool.LiquidityPool; delegates to the chain."""

    chain = None

    def __init__(self, blockchain_instance=None):
        self.blockchain_instance = blockchain_instance

    def swap_tokens(self, account, token_pair, token_symbol, token_amount,
                    trade_type, min_amount_out=None):
        return FakeLP.chain.swap_tokens(account, token_pair, token_symbol,
                                        token_amount, trade_type,
                                        min_amount_out=min_amount_out)


def _sized_plan(pool="GLD:SLV", budget=0.05):
    return {"action": "deploy_idle", "pool": pool, "budget_usd": budget,
            "live": True, "cap_usd": 0.05,
            "sizing": {"status": "sized",
                       "base_token": pool.split(":")[0],
                       "quote_token": pool.split(":")[1]}}


@contextmanager
def deploy_env(chain, tmpdir):
    """Wire every seam execute_idle_deploy touches onto the fake chain."""
    throttle = os.path.join(tmpdir, "throttle")
    vault = os.path.join(tmpdir, "vault.json")
    with open(vault, "w") as fh:
        json.dump({"account": "testacct", "active": "fakekey"}, fh)
    FakeLP.chain = chain
    with patch.object(X, "LIVE_MODE", True), \
         patch.object(X, "IDLE_DEPLOY_THROTTLE", throttle), \
         patch.object(X, "VERIFY_POLL_SECONDS", 1.0), \
         patch.object(X, "_log"), \
         patch.object(X, "_enforce", return_value=(True, None, None)), \
         patch.object(X, "_build_guard_context", return_value={}), \
         patch.object(LAV2, "VAULT_PATH", vault), \
         patch.object(LAV2, "live_armed", return_value=(True, "armed")), \
         patch.object(LBG, "broadcast_once_hive", lambda **kw: object()), \
         patch("nectarengine.pool.LiquidityPool", FakeLP), \
         patch("requests.post", chain.post), \
         patch("time.sleep", lambda s: None):
        yield throttle


# ─── plan-stage pre-flight ───────────────────────────────────────────
class TestPlanPreFlight(unittest.TestCase):

    def _plan(self, dst, pe=None):
        agent = FakeAgent(pe=pe or FakePE())
        decision = {"expand_candidate": dst,
                    "verdicts": {dst: {"action": "expand"}}}
        with patch.object(X, "IDLE_DEPLOY_THROTTLE",
                          os.path.join(tempfile.mkdtemp(), "t")), \
             patch.object(X, "_size_deposit_stage",
                          return_value={"status": "sized",
                                        "base_token": dst.split(":")[0],
                                        "quote_token": dst.split(":")[1]}):
            return X.plan_idle_deploy(agent, decision)

    def test_swap_hive_on_one_side_deploys_single_leg(self):
        # PEPE:SWAP.HIVE (the ledger's 11-01 failure) is a DESIGNED ladder
        # destination: the identity side needs no swap. The plan must deploy.
        out = self._plan("PEPE:SWAP.HIVE")
        self.assertEqual(out["action"], "deploy_idle")

    def test_unknown_precision_blocks(self):
        pe = FakePE()
        del pe.token_precisions["SLV"]
        out = self._plan("GLD:SLV", pe=pe)
        self.assertEqual(out["action"], "blocked")
        self.assertIn("unknown token precision", out["reason"])

    def test_healthy_pool_still_deploys(self):
        out = self._plan("GLD:SLV")
        self.assertEqual(out["action"], "deploy_idle")


# ─── execute-stage pre-flight + throttle ordering ────────────────────
class TestExecutePreFlightAndThrottle(unittest.TestCase):

    def test_missing_leg_pool_refuses_before_throttle_mark(self):
        chain = FakeChain(
            balances={("testacct", "SWAP.HIVE"): "1.0"},
            pools={"SWAP.HIVE:GLD": {"baseQuantity": "1000",
                                     "quoteQuantity": "5000"}},
            # NO pool for SWAP.HIVE:SLV → the quote leg could never route
            positions={("testacct", "GLD:SLV"): "0.0"},
        )
        with tempfile.TemporaryDirectory() as td:
            with deploy_env(chain, td):
                plan = X.execute_idle_deploy(FakeAgent(), _sized_plan(),
                                             allow_execute=True)
            self.assertFalse(plan.get("executed"))
            self.assertIn("pre-flight", plan.get("reason", ""))
            self.assertFalse(os.path.exists(os.path.join(td, "throttle")),
                             "a pre-broadcast refusal must NOT mark the "
                             "throttle — the next tick may retry")

    def test_throttle_marked_before_first_broadcast(self):
        # the N1 pin: the PEPE loop happened because the mark was written only
        # on fully-verified success. The mark must precede leg 1.
        chain = FakeChain(
            balances={("testacct", "SWAP.HIVE"): "1.0"},
            pools={"SWAP.HIVE:GLD": {"baseQuantity": "1000",
                                     "quoteQuantity": "5000"},
                   "SWAP.HIVE:SLV": {"baseQuantity": "1000",
                                     "quoteQuantity": "2500"},
                   "GLD:SLV": {"baseQuantity": "1000",
                               "quoteQuantity": "500"}},
            positions={("testacct", "GLD:SLV"): "0.0"},
        )
        seen = {}

        real_swap = chain.swap_tokens

        def spying_swap(*a, **kw):
            result = real_swap(*a, **kw)
            seen["swapped_before_check"] = True
            return result

        chain.swap_tokens = spying_swap
        with tempfile.TemporaryDirectory() as td:
            with deploy_env(chain, td):
                # stub the final add: it must also move the chain state so the
                # final verify sees shares grow
                class _StubExec:
                    def __init__(self, agent):
                        pass

                    def add_liquidity(self, pool, amt_a, amt_b, est_usd,
                                      reason=""):
                        # a real add consumes both tokens and mints shares
                        base, quote = pool.split(":")
                        for sym, amt in ((base, float(amt_a)),
                                         (quote, float(amt_b))):
                            key = ("testacct", sym)
                            chain.balances[key] = repr(
                                float(chain.balances.get(key, "0")) - amt)
                        chain.positions[("testacct", pool)] = "1.0"
                        return {"status": "broadcast"}

                with patch.object(LE, "TestExecutor", _StubExec):
                    plan = X.execute_idle_deploy(FakeAgent(), _sized_plan(),
                                                 allow_execute=True)
            self.assertTrue(seen.get("swapped_before_check"),
                            "leg 1 should have broadcast")
            self.assertTrue(plan.get("executed"),
                            f"happy path should verify: {plan.get('reason')}")
            self.assertTrue(os.path.exists(os.path.join(td, "throttle")),
                            "throttle must be marked before the first "
                            "broadcast")

    def test_swap_hive_side_executes_with_exactly_one_swap(self):
        # the execute-stage pin for the ledger's PEPE failure: no identity
        # swap, no "no pool SWAP.HIVE/SWAP.HIVE" — one real swap, deployed.
        chain = FakeChain(
            balances={("testacct", "SWAP.HIVE"): "1.0"},
            pools={"SWAP.HIVE:PEPE": {"baseQuantity": "1000",
                                      "quoteQuantity": "5000"},
                   "PEPE:SWAP.HIVE": {"baseQuantity": "5000",
                                      "quoteQuantity": "1000"}},
            positions={("testacct", "PEPE:SWAP.HIVE"): "0.0"},
        )
        with tempfile.TemporaryDirectory() as td:
            with deploy_env(chain, td):

                class _StubExec:
                    def __init__(self, agent):
                        pass

                    def add_liquidity(self, pool, amt_a, amt_b, est_usd,
                                      reason=""):
                        base, quote = pool.split(":")
                        for sym, amt in ((base, float(amt_a)),
                                         (quote, float(amt_b))):
                            key = ("testacct", sym)
                            chain.balances[key] = repr(
                                float(chain.balances.get(key, "0")) - amt)
                        chain.positions[("testacct", pool)] = "1.0"
                        return {"status": "broadcast"}

                with patch.object(LE, "TestExecutor", _StubExec):
                    plan = X.execute_idle_deploy(FakeAgent(),
                                                 _sized_plan("PEPE:SWAP.HIVE"),
                                                 allow_execute=True)
            self.assertTrue(plan.get("executed"),
                            f"single-leg deploy should verify: "
                            f"{plan.get('reason')}")
            self.assertEqual(len(chain.swaps), 1,
                             "the identity side must not be swapped")
            self.assertTrue(os.path.exists(os.path.join(td, "throttle")))

    def test_leg2_failure_keeps_throttle(self):
        # leg 1 broadcasts, leg 2 refuses — the next tick must NOT re-swap
        # (the exact PEPE loop), so the throttle set before leg 1 stays.
        chain = FakeChain(
            balances={("testacct", "SWAP.HIVE"): "1.0"},
            pools={"SWAP.HIVE:GLD": {"baseQuantity": "1000",
                                     "quoteQuantity": "5000"},
                   "SWAP.HIVE:SLV": {"baseQuantity": "1000",
                                     "quoteQuantity": "2500"},
                   "GLD:SLV": {"baseQuantity": "1000",
                               "quoteQuantity": "500"}},
            positions={("testacct", "GLD:SLV"): "0.0"},
        )
        chain.fail_symbol = "SLV"
        with tempfile.TemporaryDirectory() as td:
            with deploy_env(chain, td):
                plan = X.execute_idle_deploy(FakeAgent(), _sized_plan(),
                                             allow_execute=True)
            self.assertFalse(plan.get("executed"))
            self.assertEqual(len(chain.swaps), 1,
                             "leg 1 broadcast, leg 2 refused")
            self.assertTrue(os.path.exists(os.path.join(td, "throttle")),
                            "a partial failure MUST keep the throttle — "
                            "re-running would swap again")


# ─── verify-poll semantics ───────────────────────────────────────────
class TestVerifyPoll(unittest.TestCase):

    def test_late_arrival_is_not_no_effect(self):
        # the 11-02 17:04 misread: the swap landed but the read came back
        # before the chain settled. The poll must retry and find it.
        chain = FakeChain(
            balances={("testacct", "SWAP.HIVE"): "1.0"},
            pools={"SWAP.HIVE:GLD": {"baseQuantity": "1000",
                                     "quoteQuantity": "5000"},
                   "SWAP.HIVE:SLV": {"baseQuantity": "1000",
                                     "quoteQuantity": "2500"},
                   "GLD:SLV": {"baseQuantity": "1000",
                               "quoteQuantity": "500"}},
            positions={("testacct", "GLD:SLV"): "0.0"},
        )
        real_swap = chain.swap_tokens
        state = {"reads": 0}

        # make the FIRST balance read after the swap return the old balance,
        # the second one the truth — the poll must survive it.
        original_post = chain.post
        real_balances = dict(chain.balances)

        def laggy_post(url, json=None, timeout=None):
            params = (json or {}).get("params", {})
            if (params.get("contract") == "tokens"
                    and params.get("table") == "balances"
                    and params.get("query", {}).get("symbol") == "GLD"):
                state["reads"] += 1
                # read #1 is the pre-state baseline (truth); read #2 is the
                # FIRST verify poll — make it arrive before the chain settles
                if state["reads"] == 2:
                    return _Resp({"result": []})   # lag: nothing visible yet
            return original_post(url, json=json, timeout=timeout)

        chain.post = laggy_post
        with tempfile.TemporaryDirectory() as td:
            with deploy_env(chain, td):
                chain.balances.update(real_balances)

                class _StubExec:
                    def __init__(self, agent):
                        pass

                    def add_liquidity(self, pool, amt_a, amt_b, est_usd,
                                      reason=""):
                        base, quote = pool.split(":")
                        for sym, amt in ((base, float(amt_a)),
                                         (quote, float(amt_b))):
                            key = ("testacct", sym)
                            chain.balances[key] = repr(
                                float(chain.balances.get(key, "0")) - amt)
                        chain.positions[("testacct", pool)] = "1.0"
                        return {"status": "broadcast"}

                with patch.object(LE, "TestExecutor", _StubExec):
                    plan = X.execute_idle_deploy(FakeAgent(), _sized_plan(),
                                                 allow_execute=True)
            self.assertTrue(plan.get("executed"),
                            f"a laggy first read must not kill the deploy: "
                            f"{plan.get('reason')}")


# ─── precision + recomputed cap on the add (Opus A2/A4) ──────────────
class TestAddLiquidityPrecisionAndCap(unittest.TestCase):

    def _run_add(self, pe, amt_a, amt_b, cap="0.05"):
        """Drive the REAL TestExecutor.add_liquidity live path with a fake
        LiquidityPool that records exactly what nectarengine would stringify."""
        recorded = {}

        class RecordingLP:
            def __init__(self, blockchain_instance=None):
                pass

            def add_liquidity(self, account, pool, base_qty, quote_qty,
                              max_deviation=None):
                recorded["base"] = str(base_qty)
                recorded["quote"] = str(quote_qty)
                return {"trx_id": "fake"}

        agent = FakeAgent(pe=pe)
        with tempfile.TemporaryDirectory() as td:
            vault = os.path.join(td, "vault.json")
            with open(vault, "w") as fh:
                json.dump({"account": "testacct", "active": "fakekey"}, fh)
            with patch.object(LE, "LIVE_MODE", True), \
                 patch.object(LAV2, "live_armed",
                              return_value=(True, "armed")), \
                 patch.object(LAV2, "VAULT_PATH", vault), \
                 patch.object(LE, "MAX_USD_PER_ACTION", float(cap)), \
                 patch.object(LBG, "broadcast_once_hive",
                              lambda **kw: object()), \
                 patch("nectarengine.pool.LiquidityPool", RecordingLP):
                ex = LE.TestExecutor(agent)
                entry = ex.add_liquidity("ALIVE:SWAP.HIVE", amt_a, amt_b,
                                         0.01, reason="precision test")
        return entry, recorded

    def test_sub_eight_precision_token_is_quantized_not_scientific(self):
        # ALIVE has precision 4. 0.00012 must floor to 0.0001 and reach the
        # contract as '0.0001' — never '1.2e-04' (contract rejects) and never
        # an unquantized float.
        pe = FakePE()
        entry, rec = self._run_add(pe, 0.00012, 0.01)
        self.assertEqual(entry["status"], "broadcast", entry.get("note"))
        self.assertEqual(rec["base"], "0.0001")
        self.assertNotIn("e-", rec["base"])
        self.assertNotIn("E-", rec["base"])

    def test_recomputed_cap_refuses_oversized_actuals(self):
        # the plan said $0.01 but the amounts are worth $0.30 — the add is
        # refused even though the caller-supplied est_usd was under the cap.
        pe = FakePE()
        entry, rec = self._run_add(pe, 0.5, 0.3)   # 0.5*0.01 + 0.3*0.3 ≈ 0.095
        self.assertEqual(entry["status"], "skipped", entry.get("note"))
        self.assertIn("recomputed value", entry["note"])
        self.assertNotIn("base", rec)

    def test_unknown_price_refuses(self):
        pe = FakePE()
        del pe.token_prices["ALIVE"]
        entry, rec = self._run_add(pe, 0.0001, 0.01)
        self.assertEqual(entry["status"], "skipped", entry.get("note"))
        self.assertIn("price unknown", entry["note"])


if __name__ == "__main__":
    unittest.main()
