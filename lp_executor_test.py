"""Test LP executor — tiny, heavily-guarded real-money execution surface.

Design rules:
  - HARD CAP per transaction (USD-equivalent, default $0.05, env LP_TEST_MAX_USD)
  - MAX 1 action per invocation (rate-limiting)
  - Dry-run is the DEFAULT; real broadcast requires LP_TEST_LIVE=1 in env
  - Every action (dry or live) appended to test_executions.jsonl with
    pre-state, action, and result for forensic replay
  - NEVER calls liquidity_agent_v2.execute_reallocation (that legacy path
    is unbounded and stays untouched)

Actions supported:
  * remove_liquidity(pool, shares)   — partial or full exit
  * add_liquidity(pool, amt_a, amt_b)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

EXECUTIONS_LOG = "/home/tydynrain/.hermes/lp_tests/test_executions.jsonl"

MAX_USD_PER_ACTION = float(os.environ.get("LP_TEST_MAX_USD", "0.05"))
LIVE_MODE = os.environ.get("LP_TEST_LIVE", "") == "1"


def _log(entry: dict) -> None:
    os.makedirs(os.path.dirname(EXECUTIONS_LOG), exist_ok=True)
    entry["logged_at"] = datetime.now().isoformat(timespec="seconds")
    with open(EXECUTIONS_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


class TestExecutor:
    """Single-action guarded executor. Instantiate fresh per action."""

    def __init__(self, agent):
        # agent: a loaded liquidity_agent_v2.LiquidityAgent (pe + portfolio loaded)
        self.agent = agent

    # ---- helpers ------------------------------------------------------
    def position_value_usd(self, pool: str) -> float:
        pos = self.agent.portfolio.positions.get(pool)
        return float(getattr(pos, "value_usd", 0.0) or 0.0)

    def position_shares(self, pool: str) -> float:
        pos = self.agent.portfolio.positions.get(pool)
        return float(getattr(pos, "shares", 0.0) or 0.0)

    def pool_total_shares(self, pool: str) -> float:
        """Get total liquidity shares in the pool (from pre-loaded engine state)."""
        # The agent's pe (pools engine) should have pool data
        if hasattr(self.agent, 'pe') and self.agent.pe:
            pool_data = self.agent.pe.get_pool(pool)
            if pool_data and 'totalShares' in pool_data:
                return float(pool_data['totalShares'])
        # Fallback: estimate from position + value (very rough, may fail)
        return 0.0

    # ---- actions ------------------------------------------------------
    def remove_liquidity(self, pool: str, shares: float, reason: str = "") -> dict:
        """Remove `shares` LP tokens from pool, subject to caps.
        Caps to the lesser of: requested shares, or the shares whose USD
        value would exceed MAX_USD_PER_ACTION (proportional estimate)."""
        held = self.position_shares(pool)
        value = self.position_value_usd(pool)
        if held <= 0:
            return self._record(pool, "remove_liquidity", 0, "skipped",
                                "no position held", reason)
        # proportional cap: if full position is worth <= cap, we can exit all
        # requested shares (tests are on tiny positions anyway).
        req_value = value * min(1.0, shares / held)
        if req_value > MAX_USD_PER_ACTION:
            shares = held * (MAX_USD_PER_ACTION / value) if value > 0 else 0
            shares = round(shares, 8)
            req_value = MAX_USD_PER_ACTION
        if shares <= 0:
            return self._record(pool, "remove_liquidity", 0, "skipped",
                                "cap reduced shares to ~0", reason)

        pre = {"pool": pool, "shares_held": held, "value_usd": value}
        if not LIVE_MODE:
            return self._record(pool, "remove_liquidity", shares, "dry-run",
                                f"would remove {shares} shares (~${req_value:.4f})",
                                reason, pre)

        # LIVE broadcast via nectarengine.
        # IMPORTANT (Hive-Engine contract): marketpools.removeLiquidity expects
        # `sharesOut` as a PERCENTAGE of the position (0-100, max 3dp), NOT an
        # absolute share count. We therefore convert the capped absolute share
        # amount into the percentage of our position that it represents.
        try:
            if held <= 0:
                return self._record(pool, "remove_liquidity", 0, "skipped",
                                    "no position held (recheck)", reason, pre)

            pct = (shares / held) * 100.0
            # max 3 decimal places
            pct = round(pct, 3)
            if pct <= 0:
                return self._record(pool, "remove_liquidity", 0, "skipped",
                                    "computed pct<=0", reason, pre)

            from nectar import Hive
            from nectarengine.pool import LiquidityPool
            from liquidity_agent_v2 import HIVE_RPC, VAULT_PATH
            acct = json.load(open(VAULT_PATH))
            hive = Hive(node=[HIVE_RPC], keys={acct["account"]: acct["active"]})
            lp = LiquidityPool(blockchain_instance=hive)

            result = lp.remove_liquidity(acct["account"], pool, pct)
            status = "broadcast" if result else "rejected"
            note = f"remove_liquidity pct={pct} returned: {result}"
        except Exception as e:  # noqa: BLE001 - log everything, never raise past cron
            status, result, note = "error", None, f"{type(e).__name__}: {e}"
        return self._record(pool, "remove_liquidity", shares, status, note,
                            reason, pre, result)

    def add_liquidity(self, pool: str, amt_a: float, amt_b: float,
                      est_usd: float, reason: str = "") -> dict:
        if est_usd > MAX_USD_PER_ACTION:
            return self._record(pool, "add_liquidity", est_usd, "skipped",
                                f"est ${est_usd:.4f} exceeds cap ${MAX_USD_PER_ACTION}",
                                reason)
        base, quote = pool.split(":")
        pre = {"pool": pool, "amt_a": amt_a, "amt_b": amt_b, "est_usd": est_usd}
        if not LIVE_MODE:
            return self._record(pool, "add_liquidity", est_usd, "dry-run",
                                f"would add {amt_a} {base} + {amt_b} {quote}",
                                reason, pre)
        try:
            from nectar import Hive
            from nectarengine.pool import LiquidityPool
            from liquidity_agent_v2 import HIVE_RPC, VAULT_PATH
            acct = json.load(open(VAULT_PATH))
            hive = Hive(node=[HIVE_RPC], keys={acct["account"]: acct["active"]})
            lp = LiquidityPool(blockchain_instance=hive)
            result = lp.add_liquidity(acct["account"], pool, amt_a, amt_b,
                                      max_deviation=1)
            status = "broadcast" if result else "rejected"
            note = f"add_liquidity returned: {result}"
        except Exception as e:  # noqa: BLE001
            status, result, note = "error", None, f"{type(e).__name__}: {e}"
        return self._record(pool, "add_liquidity", est_usd, status, note,
                            reason, pre, result)

    # ---- bookkeeping --------------------------------------------------
    def _record(self, pool, action, magnitude, status, note, reason,
                pre=None, tx_result=None) -> dict:
        entry = {
            "pool": pool, "action": action, "magnitude": magnitude,
            "status": status, "note": note, "reason": reason,
            "live": LIVE_MODE, "cap_usd": MAX_USD_PER_ACTION,
            "pre_state": pre, "tx_result": tx_result,
        }
        _log(entry)
        return entry


def execute_from_decision(agent, decision_report) -> list:
    """Given a DecisionReport, take AT MOST ONE action (highest value):
    1. If exit_candidates exist and at least one has value > dust: remove it.
    2. Else if expand_candidate and reallocation recommended: (dry) add.
    Returns list of action entries logged."""
    ex = TestExecutor(agent)
    actions = []

    for pool in decision_report.exit_candidates:
        shares = ex.position_shares(pool)
        val = ex.position_value_usd(pool)
        if shares > 0 and val > 0.0005:  # skip true dust
            verdict = decision_report.verdicts[pool]
            entry = ex.remove_liquidity(pool, shares, reason=verdict.reason)
            actions.append(entry)
            return actions  # ONE action per run

    # nothing to exit — no deployment executed test-side yet; deployments
    # stay dry-run until removal path has proven itself live
    if decision_report.recommended_reallocation:
        rec = decision_report.recommended_reallocation
        _log({"action": "reallocation_preview", "status": "advisory-only",
              "detail": rec, "live": False})
    return actions
