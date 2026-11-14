"""Test LP executor — tiny, heavily-guarded real-money execution surface.

Design rules:
  - HARD CAP per transaction (USD-equivalent, default $0.05, env LP_TEST_MAX_USD)
  - MAX 1 action per invocation (rate-limiting)
  - Dry-run is the DEFAULT; real broadcast requires LP_TEST_LIVE=1 in env
  - Every action (dry or live) appended to test_executions.jsonl with
    pre-state, action, and result for forensic replay
  - 2026-11-13 (Claude review P0-12): liquidity_agent_v2.execute_reallocation
    and the whole legacy ReallocationPlanner were DELETED (ungated, and it
    passed absolute shares where the contract wants a percentage). Nothing
    may resurrect them; any new broadcast path must go through live_armed()
    + the cap + broadcast_once_hive.

Actions supported:
  * remove_liquidity(pool, shares)   — partial or full exit
  * add_liquidity(pool, amt_a, amt_b)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

# 2026-10-22: expanduser, NOT hardcoded (test-sandbox leak class — see lp_decision.DECISIONS_DIR)
EXECUTIONS_LOG = os.path.expanduser('~/.hermes/lp_tests/test_executions.jsonl')

MAX_USD_PER_ACTION = float(os.environ.get("LP_TEST_MAX_USD", "0.05"))
LIVE_MODE = os.environ.get("LP_TEST_LIVE", "") == "1"

_HE_RPC = "https://api.hive-engine.com/rpc/contracts"


def _he_find(contract, table, query, limit=1):
    """Read-only Hive-Engine contract query for post-broadcast verification.

    Raises on an RPC error and returns [] for an empty table — the two are
    DIFFERENT outcomes, and reading an error as 0 is how a successful swap was
    once misreported as "NO EFFECT" (Opus review N1, ledger 11-02 17:04).
    """
    import requests
    r = requests.post(_HE_RPC, json={"jsonrpc": "2.0", "id": 1,
                                     "method": "find",
                                     "params": {"contract": contract,
                                                "table": table,
                                                "query": query,
                                                "limit": limit}},
                      timeout=30).json()
    if r.get("error") is not None:
        raise RuntimeError(f"RPC error on {contract}.{table}: "
                           f"{str(r.get('error'))[:200]}")
    return r.get("result") or []


def _he_balances(account, symbols):
    """{symbol: balance} for the symbols actually readable; a symbol with no
    row is absent — never a fabricated 0."""
    out = {}
    for sym in symbols:
        rows = _he_find("tokens", "balances",
                        {"account": account, "symbol": sym}, 1)
        if rows:
            out[sym] = float(rows[0]["balance"])
    return out


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
        if value <= 0:
            # 2026-11-13 (Claude review P0-11): an unknown (None→0.0) value made
            # req_value 0, which never exceeds the cap — so the shares sailed
            # through UNCAPPED. None is not 0: with an unbounded value the cap
            # cannot be computed and the only safe verdict is skip.
            return self._record(pool, "remove_liquidity", 0, "skipped",
                                "position value unknown/zero — cap not "
                                "computable, refusing (None is not 0)", reason)
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

        # HARD CHOKEPOINT (2026-11-13, Claude review P0-1): env alone is not
        # consent. Every live broadcast requires the same two-key arming as
        # ExecutionEngine (LP_TEST_LIVE=1 AND the AUTONOMY_ON flag file) —
        # fa272aa gated the executor-subprocess path; this closes the direct
        # `LP_TEST_LIVE=1 python cron_lp_agent.py` bypass.
        from liquidity_agent_v2 import live_armed
        _armed, _why = live_armed()
        if not _armed:
            return self._record(pool, "remove_liquidity", shares, "skipped",
                                f"live not armed: {_why}", reason, pre)

        # BALANCE DELTA BASELINE (Opus round 2, prior review P1-8): a truthy
        # library return is NOT evidence (08:20 lesson). Read the pool-token
        # balances fresh from the chain BEFORE broadcasting, so the post-read
        # delta proves what the removal actually released.
        _base, _quote = pool.split(":")
        try:
            from liquidity_agent_v2 import VAULT_PATH as _VP
            _acct_name = json.load(open(_VP))["account"]
            pre["pre_balances"] = _he_balances(_acct_name, (_base, _quote))
        except Exception as _e:  # verification is best-effort pre-read
            pre["pre_balances"] = None
            pre["pre_balance_error"] = f"{type(_e).__name__}: {str(_e)[:120]}"

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

            from nectarengine.pool import LiquidityPool
            from liquidity_agent_v2 import HIVE_RPC, VAULT_PATH
            from lp_broadcast_guard import broadcast_once_hive
            acct = json.load(open(VAULT_PATH))
            # BROADCAST-ONCE (Claude review P1-1): this instance's ONLY traffic
            # is the signed broadcast — never auto-retry and re-send the same
            # transaction on an RPC hiccup (ops #222/#223 class).
            hive = broadcast_once_hive(node=[HIVE_RPC],
                                       keys={acct["account"]: acct["active"]})
            lp = LiquidityPool(blockchain_instance=hive)

            result = lp.remove_liquidity(acct["account"], pool, pct)
            status = "broadcast" if result else "rejected"
            note = f"remove_liquidity pct={pct} returned: {result}"
            # BALANCE DELTA VERIFY (Opus round 2, P1-8): the chain is ground
            # truth — record what the removal actually released. Unavailable
            # verification is recorded as unavailable, never as failure and
            # never as a fabricated zero.
            if status == "broadcast" and pre.get("pre_balances"):
                time.sleep(6)   # HE virtual-layer settle
                try:
                    _post = _he_balances(acct["account"], (_base, _quote))
                    _delta = {s: round(_post.get(s, 0.0)
                                       - pre["pre_balances"].get(s, 0.0), 8)
                              for s in (_base, _quote)}
                    pre["balance_deltas"] = _delta
                    note += f"; balance delta {_delta}"
                except Exception as _e:  # noqa: BLE001
                    note += (f"; post-balance verify unavailable "
                             f"({type(_e).__name__})")
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
        # HARD CHOKEPOINT (2026-11-13, Claude review P0-1): same two-key arming
        # as remove_liquidity — env AND the AUTONOMY_ON flag file.
        from liquidity_agent_v2 import live_armed
        _armed, _why = live_armed()
        if not _armed:
            return self._record(pool, "add_liquidity", est_usd, "skipped",
                                f"live not armed: {_why}", reason, pre)

        # RECOMPUTED CAP + PRECISION (Opus review A2/A4, round 2): est_usd is
        # the PLANNED budget; amt_a/amt_b are what ACTUALLY arrived. A doubled
        # swap leg or a reward landing in a pool token between the pre and
        # post reads can make the add BIGGER than the plan while the ledger
        # still records the planned cap. Recompute from the amounts, refuse on
        # an unknown price, and quantize to token precision — nectarengine
        # sends str(quantity) UNQUANTIZED here, so any sub-8-decimal token
        # (ALIVE=4, HSBIDAO=3, BBH=2) was rejected on-chain every time, and
        # str(float) below 1e-4 renders as scientific notation ('4e-05').
        _pe = getattr(self.agent, "pe", None)
        if _pe is None:
            return self._record(pool, "add_liquidity", est_usd, "skipped",
                                "no price engine — cannot recompute the cap "
                                "from the actual amounts", reason, pre)
        _px = getattr(_pe, "token_prices", {}) or {}
        _pb, _pq = _px.get(base), _px.get(quote)
        if _pb is None or _pq is None:
            _missing = [t for t, p in ((base, _pb), (quote, _pq))
                        if p is None]
            return self._record(pool, "add_liquidity", est_usd, "skipped",
                                f"price unknown for {_missing} — refusing an "
                                f"uncapable cap computation (None is not 0)",
                                reason, pre)
        _val = float(amt_a) * float(_pb) + float(amt_b) * float(_pq)
        if _val > MAX_USD_PER_ACTION:
            return self._record(pool, "add_liquidity", est_usd, "skipped",
                                f"recomputed value ${_val:.4f} from the actual "
                                f"amounts exceeds the ${MAX_USD_PER_ACTION} "
                                f"cap", reason, pre)
        try:
            amt_a = _pe.round_amount(base, amt_a)
            amt_b = _pe.round_amount(quote, amt_b)
        except ValueError as _ve:
            return self._record(pool, "add_liquidity", est_usd, "skipped",
                                f"precision: {_ve}", reason, pre)
        from decimal import Decimal
        amt_a, amt_b = Decimal(str(amt_a)), Decimal(str(amt_b))
        pre["amt_a"], pre["amt_b"] = float(amt_a), float(amt_b)
        pre["recomputed_usd"] = round(_val, 6)
        try:
            from nectarengine.pool import LiquidityPool
            from liquidity_agent_v2 import HIVE_RPC, VAULT_PATH
            from lp_broadcast_guard import broadcast_once_hive
            acct = json.load(open(VAULT_PATH))
            # BROADCAST-ONCE (Claude review P1-1): this instance's ONLY traffic
            # is the signed broadcast — never auto-retry and re-send the same
            # transaction on an RPC hiccup (ops #222/#223 class).
            hive = broadcast_once_hive(node=[HIVE_RPC],
                                       keys={acct["account"]: acct["active"]})
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
