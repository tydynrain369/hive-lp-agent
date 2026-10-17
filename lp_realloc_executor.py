"""Reallocation executor — LEG A ONLY (remove from source, stage the deposit).

WHY A NEW MODULE
  hive-lp-decision-preferences requires the guarded executor to be a NEW file
  alongside the legacy one, never an edit to it. lp_executor_test.py predates
  the 2026-10-02 decision-layer fix and cannot execute a reallocation at all:
  its loop only iterates `exit_candidates` (empty in our portfolio) and it
  only LOGS a recommended_reallocation. Editing it would also destroy the
  artifact we still need for comparison.

SCOPE — DELIBERATELY INCOMPLETE
  This module can REMOVE liquidity from a consolidate/exit source pool. It can
  plan and validate the destination deposit but CANNOT broadcast one, because:

    * the sizing math that decides how much goes where does not exist yet, and
    * LEG B (growing a position toward its threshold with new capital) needs
      that math plus a real position to test it against, not dust.

  A half-built deposit path that "looks runnable" is exactly the failure mode
  that produced run_live_once.py. So the deposit side stops at a plan.

GUARDS (unchanged in spirit from the legacy executor)
  - DRY-RUN is the default; broadcast requires LP_TEST_LIVE=1 AND --execute.
  - Hard USD cap per action from LP_TEST_MAX_USD (default $0.05).
  - AT MOST ONE removal per run.
  - Append-only audit log with pre-state.
  - Refuses any source pool that is not marked consolidate/exit.
  - Refuses if the advisory raised a CHALLENGE, unless explicitly overridden.

Run (dry):
  /home/tydynrain/hive-agent-test/.venv/bin/python lp_realloc_executor.py
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")

from lp_executor_test import TestExecutor, MAX_USD_PER_ACTION, LIVE_MODE, _log

DECISION_DIR = "/home/tydynrain/.hermes/lp_tests/decisions"
REALLOC_LOG = "/home/tydynrain/.hermes/lp_tests/reallocations.jsonl"

# A position below this is not worth an on-chain transaction (Hive renders
# sub-1e-8 as dust; 1e-6 is the management floor used elsewhere in this agent).
DUST_FLOOR_USD = 1e-6

# Hop bound for the general pool-graph route search. 3 finds the verified
# HSBIDAO -> LBI -> SWAP.HIVE chain; 5 reaches CENT/POB from community tokens.
ROUTE_MAX_HOPS = 5

SOURCE_ACTIONS = ("consolidate", "exit")


def latest_decision():
    import glob
    files = sorted(glob.glob(f"{DECISION_DIR}/decision-*.json"))
    files = [f for f in files if "advisory" not in f]
    if not files:
        return None, None
    path = files[-1]
    with open(path) as fh:
        return path, json.load(fh)


def latest_advisory(decision_path):
    """Return the advisory verdict for this decision, or None if absent."""
    if not decision_path:
        return None
    ap = decision_path.replace(".json", ".advisory.json")
    if not os.path.exists(ap):
        return None
    try:
        with open(ap) as fh:
            return json.load(fh)
    except Exception:
        return None


# Fraction of the source pool that must remain (held by OTHER LPs) for a route
# through it to count as performable after our exit. Named and tunable because it
# encodes a real policy choice: at what point is a leftover pool too thin to
# trust for a swap? Tydyn supplies this judgement; the default is deliberately
# conservative.
SOURCE_POOL_LIQUIDITY_FLOOR = 0.01

# All-in cost ceiling for a reallocation's conversion route, as a percentage.
# 2026-10-04, set by Tydyn. Measured at our position sizes a 3-4 hop route runs
# 0.30-0.40% (roughly 0.10-0.15% per hop, mostly swap fee), so 1% leaves room
# for a longer chain without ever permitting a route that eats the benefit.
#
# This is a REFUSAL, not a warning: a plan that cannot convert its released
# capital for less than this is not a reallocation, it is a donation. Above the
# line the agent reports why and stops, because that is the behaviour required of
# something intended to operate autonomously — an agent that plans moves it
# cannot justify is worse than one that plans nothing.
ROUTE_COST_REFUSAL_PCT = 1.0

_ADJ_CACHE = {}


def _adjacency(agent):
    """Build (and cache) the pool-graph adjacency once per agent instance.

    2026-10-04: the cache was keyed by id(pe), which is unsound. CPython reuses
    the memory address of a garbage-collected object, so a later object can land
    on an id an earlier one used and be handed THAT object's adjacency. Nothing
    evicts entries either, so the cache only grows.

    Observed directly: after TestRefusals the cache held six entries all with
    identical keys {DST, OTHER, POOL, SRC, X}, and a later test asking about a
    PIXYDUST graph was served one of those instead - turning a genuinely
    routable reallocation into a false 'blocked'. That is a wrong ANSWER about
    capital, produced by cache identity rather than by any evidence.

    Fix: key on the pool index itself (its identity AND size), and hold a
    reference to the engine so a live engine's cache entry cannot be collected
    and have its id recycled.
    """
    pe = getattr(agent, "pe", None)
    if pe is None:
        return {}
    pools = getattr(pe, "pools", None)
    if pools is None:
        return {}
    # (id(pools), len(pools)) plus a strong ref to both keeps the entry valid.
    key = (id(pools), len(pools))
    entry = _ADJ_CACHE.get(key)
    if entry is not None:
        return entry[1]
    try:
        from lp_router import build_adjacency
        adj = build_adjacency(pools)
    except Exception:
        adj = {}
    _ADJ_CACHE[key] = (pe, adj)   # strong ref => id(pools) stays unique
    return adj


def _source_pool_still_liquid(agent, src_pool, token_being_swapped):
    """Does the source pool retain enough liquidity AFTER our exit to swap?

    This is the real physical constraint behind "can this reallocation actually
    be carried out". Our exit removes only OUR shares; the pool and everyone
    else's liquidity remain. So a route through the source pool is performable
    unless we are effectively the whole pool.

    Conservative by design: any doubt (no pool row, no totalShares, no reserves)
    returns False, i.e. blocks, because an unverifiable claim of liquidity is the
    exact thing this guard exists to refuse.
    """
    try:
        pool_row = (getattr(agent, "pe", None).pools or {}).get(src_pool)
    except Exception:
        return False
    if pool_row is None:
        return False
    try:
        total = float(getattr(pool_row, "total_shares", None)
                      or getattr(pool_row, "totalShares", None) or 0)
    except Exception:
        total = 0.0
    try:
        our = _our_shares(agent, src_pool)
    except Exception:
        our = 0.0
    if total <= 0:
        return False
    # If others still hold a meaningful share of the pool, the route survives.
    return (total - our) / total >= SOURCE_POOL_LIQUIDITY_FLOOR


def _path_uses_source_pool(path, src_tokens):
    """True when any hop on the path converts THROUGH the pool being exited.

    A route [A, B, C] means A->B and B->C each happen inside some pool. If a
    consecutive pair is exactly the source pool's two tokens, the route depends on
    the position we are about to withdraw, and vanishes when we do.
    """
    if not path or len(path) < 2 or not src_tokens:
        return False
    for a, b in zip(path, path[1:]):
        if {a, b} == src_tokens:
            return True
    return False


def unroutable_tokens(agent, released_tokens, dest_pool, src_pool=None):
    """Which of the tokens a removal yields cannot reach the destination pair.

    2026-10-02: a reallocation can be RECOMMENDED and still be physically
    impossible. Removing HSBIDAO:PIXYDUST yields HSBIDAO and PIXYDUST; the
    destination CENT:POB needs CENT and POB. PIXYDUST exists in exactly one
    pool in the whole index, so once we exit that pool it has no route to
    anything at all.

    A token is routable if either:
      - it is already one of the destination pair's tokens, or
      - the price engine finds a path to one of them.
    """
    dest_tokens = [t for t in dest_pool.split(":") if t]
    try:
        pe = agent.pe
    except Exception:
        return list(released_tokens)          # cannot verify -> treat as unroutable

    # 2026-10-02: switched from pe.find_path to the general graph search.
    # find_path only tries an intermediate of HIVE or SWAP.HIVE, so it reported
    # NONE for PIXYDUST -> CENT even though the real chain is
    # PIXYDUST -> HSBIDAO -> LBI -> SWAP.HIVE -> CENT. A guard built on the
    # hub-only search would block performable reallocations — precisely the
    # failure the guard exists to prevent.
    adj = _adjacency(agent)
    if not adj:
        return list(released_tokens)          # cannot verify -> treat as blocked

    try:
        from lp_router import find_general_path
    except Exception:
        return list(released_tokens)

    # 2026-10-04, REVISED THE SAME DAY after Tydyn corrected me.
    #
    # My first version rejected ANY path that converted through the source pool,
    # reasoning that exiting "destroys" it. That was wrong in two ways:
    #   - exiting removes OUR SHARES, not the pool. Other LPs remain.
    #   - PIXYDUST's only pool is HSBIDAO:PIXYDUST, and after our 2026-10-02
    #     exit the chain still shows totalShares 5279.13, base 5238.44,
    #     quote 5336.75. PIXYDUST -> HSBIDAO remains perfectly performable.
    # Tydyn's own route is confirmed on-chain:
    #   PIXYDUST -> HSBIDAO -> LBI -> CENT
    #   PIXYDUST -> HSBIDAO -> LBI -> SWAP.HIVE -> POB
    # So a structural ban refused exactly the moves it exists to enable.
    #
    # The correct test is LIQUIDITY, not structure: the source pool only blocks a
    # route if the liquidity left in it cannot absorb what we would swap. When we
    # are the only LP it cannot, which is the genuine edge case worth keeping.
    blocked, paths = [], {}
    src_tokens = {t for t in (src_pool or "").split(":") if t}
    for tok in released_tokens:
        if tok in dest_tokens:
            paths[tok] = [tok]
            continue
        path = (find_general_path(adj, tok, dest_tokens[0], max_hops=ROUTE_MAX_HOPS)
                or find_general_path(adj, tok, dest_tokens[1], max_hops=ROUTE_MAX_HOPS))
        if path:
            paths[tok] = path
            if _path_uses_source_pool(path, src_tokens) and not _source_pool_still_liquid(
                    agent, src_pool, tok):
                blocked.append(tok)
        else:
            blocked.append(tok)
    unroutable_tokens.last_paths = paths
    return blocked


def _our_shares(agent, src_pool):
    """Our LP shares in a pool, from whichever object exposes them.

    LiquidityAgent itself has NO position_shares; that lives on the executor
    wrapper (lp_executor_test.TestExecutor) which reads
    agent.portfolio.positions[pool].shares. Guessing an attribute and getting
    None silently yields 0.0, which makes a cost gate price nothing and report
    "not-priced" — a gate that looks healthy and never fires.
    """
    fn = getattr(agent, "position_shares", None)
    if callable(fn):
        try:
            return float(fn(src_pool) or 0)
        except Exception:
            pass
    pos = (getattr(getattr(agent, "portfolio", None), "positions", None) or {}).get(src_pool)
    if pos is not None:
        return float(getattr(pos, "shares", 0) or 0)
    inner = getattr(agent, "agent", None)
    pos = (getattr(getattr(inner, "portfolio", None), "positions", None) or {}).get(src_pool)
    if pos is not None:
        return float(getattr(pos, "shares", 0) or 0)
    return 0.0


def _released_amounts(agent, src_pool, position_value_usd):
    """Token -> amount the removal yields, from the source pool's own reserves.

    Our share fraction of the pool applied to each side's reserves is exactly
    what removeLiquidity returns, so this is a measured quantity rather than an
    estimate from USD prices.
    """
    out = {}
    try:
        row = (getattr(agent, "pe", None).pools or {}).get(src_pool)
        if row is None:
            return out
        base, quote = src_pool.split(":")[:2]
        total = float(getattr(row, "total_shares", 0) or 0)
        ours = _our_shares(agent, src_pool)
        if total <= 0 or ours <= 0:
            return out
        frac = ours / total
        # PoolData spells these base_quantity / quote_quantity. Reading the wrong
        # attribute yields 0.0 silently, which is how the gate came to report
        # "not-priced" on every live run while appearing healthy.
        bq = getattr(row, "base_quantity", None)
        qq = getattr(row, "quote_quantity", None)
        if bq is None:
            bq = getattr(row, "base_qty", None)
        if qq is None:
            qq = getattr(row, "quote_qty", None)
        if bq is None or qq is None:
            return out
        out[base] = float(bq) * frac
        out[quote] = float(qq) * frac
    except Exception:
        return {}
    return out


def _route_cost_check(agent, src_pool, dst_pool, released_amounts):
    """Price the conversion route and refuse above ROUTE_COST_REFUSAL_PCT.

    released_amounts maps token -> amount the removal actually yields. We price
    the route for the largest single amount, because that leg dominates: a route
    cheap for dust and ruinous for the bulk is not a cheap route.

    Returns a dict that is always safe to put in an artifact:
      status   - 'priced' | 'not-priced' | 'refused'
      cost_pct - float or None
      verdict  - human-readable, present when refused
    """
    out = {"status": "not-priced", "cost_pct": None,
           "threshold_pct": ROUTE_COST_REFUSAL_PCT,
           "paths": None, "detail": None}
    if not released_amounts:
        out["detail"] = "no released amounts to price"
        return out
    try:
        from lp_router import build_adjacency, find_general_path
        import lp_route_cost as RC
        pe = getattr(agent, "pe", None)
        if pe is None:
            out["detail"] = "no price engine; cannot price the route"
            return out
        adj = build_adjacency(pe.pools or {})
    except Exception as e:
        out["detail"] = f"{type(e).__name__}: {e}"
        return out

    dest_tokens = [t for t in (dst_pool or "").split(":") if t]
    rows = []
    worst = None
    for tok, amt in released_amounts.items():
        if tok in dest_tokens or float(amt) <= 0:
            continue
        path = (find_general_path(adj, tok, dest_tokens[0], max_hops=ROUTE_MAX_HOPS)
                or find_general_path(adj, tok, dest_tokens[1], max_hops=ROUTE_MAX_HOPS)) \
               if dest_tokens else None
        if not path:
            continue
        try:
            r = RC.price_route(path, str(amt), tok, pe.pools or {})
        except Exception as e:
            out["detail"] = f"{type(e).__name__}: {e}"
            continue
        if not r.get("ok"):
            continue
        row = {"token": tok, "amount": str(amt), "path": r["path"],
               "cost_pct": r["cost_pct"], "out_amount": r["amount_out"],
               "out_token": r["out_token"]}
        rows.append(row)
        if worst is None or (r["cost_pct"] or 0) > (worst["cost_pct"] or 0):
            worst = row

    out["paths"] = rows
    if worst is None:
        out["detail"] = "no released token requires a conversion route"
        return out
    out["status"] = "priced"
    out["cost_pct"] = worst["cost_pct"]
    out["worst_leg"] = worst
    if worst["cost_pct"] is not None and worst["cost_pct"] > ROUTE_COST_REFUSAL_PCT:
        out["status"] = "refused"
        out["verdict"] = (
            f"refusing: converting {worst['token']} via "
            f"{' -> '.join(worst['path'])} costs {worst['cost_pct']:.4f}%, "
            f"above the {ROUTE_COST_REFUSAL_PCT}% ceiling. The released capital "
            f"would not survive the conversion.")
    return out


def _size_deposit_stage(agent, dest_pool, budget_usd):
    """Value the released capital against the destination pair and split it.

    Returns a dict that either carries exact add_liquidity quantities or says
    precisely why no sizing is possible. Never raises, never broadcasts, and
    never invents a price: an unpriced token yields status 'unpriced' rather
    than a fabricated quantity.
    """
    out = {"status": "not-sized", "pool": dest_pool,
           "budget_usd": str(budget_usd)}
    try:
        from decimal import Decimal
        import lp_sizing as S
        from lp_market_price import market_price
    except Exception as e:
        out["status"] = "sizing-unavailable"
        out["reason"] = f"{type(e).__name__}: {e}"
        return out

    parts = [t for t in (dest_pool or "").split(":") if t]
    if len(parts) != 2:
        # Note: unpacking straight into base, quote raised ValueError here for
        # any malformed destination, because "NOCOLON".split(":") yields ONE
        # element. Guard on the length BEFORE unpacking.
        out["status"] = "malformed-destination"
        out["reason"] = f"destination pool must be BASE:QUOTE, got {dest_pool!r}"
        return out
    base, quote = parts

    # Validated market prices. The pool's basePrice/quotePrice are EXCHANGE
    # RATIOS and are never used for USD valuation - that was a real 9500x bug.
    #
    # 2026-10-04: market_price() alone is NOT sufficient. It returned None for
    # SWAP.HIVE even though the price engine values it at 0.05698195, because
    # that helper reads the MARKET/order-book route and SWAP.HIVE has no market
    # row of its own - it is the L1 anchor derived from the SWAP.HIVE:SWAP.HBD
    # pool. Falling back to the price engine makes the deposit sizABLE for the
    # layer-2 assets it is actually used with, and the engine's own price is a
    # validated reserve-derived figure, not an invented one.
    engine_prices = getattr(getattr(agent, "pe", None), "token_prices", None) or {}
    pm, missing = {}, []
    for tok in (base, quote):
        px = None
        try:
            px = (market_price(tok) or {}).get("price")
        except Exception:
            px = None
        if px is None:
            px = engine_prices.get(tok)     # reserve-derived fallback
        if px is None:
            missing.append(tok)
        else:
            pm[tok] = str(px)
    if missing:
        out["status"] = "unpriced"
        out["unpriced_tokens"] = missing
        out["reason"] = (f"no validated market price for {missing}; refusing to "
                         f"invent a quantity")
        return out

    try:
        budget = Decimal(str(budget_usd))
        # Hold back the same fee reserve the standalone sizing helper uses.
        r = S.size_deposit(dest_pool, budget, pm)
    except Exception as e:
        out["status"] = "sizing-failed"
        out["reason"] = f"{type(e).__name__}: {e}"
        return out

    out.update({
        "status": "sized",
        "base_token": r.get("base"),
        "quote_token": r.get("quote"),
        "base_amount": str(r.get("base_amount")),
        "quote_amount": str(r.get("quote_amount")),
        "deploy_usd": str(r.get("deploy_usd")),
        "fee_reserve_usd": str(r.get("fee_reserve_usd")),
        "fee_reserve_ratio": str(r.get("fee_reserve_ratio")),
        "prices_used": {k: str(v) for k, v in (r.get("prices_used") or {}).items()},
        "split_is_balanced_usd": r.get("split_is_balanced_usd"),
        "executable": False,
        "authority": ("SIZING ONLY - add_liquidity quantities computed but NOT "
                      "broadcast; human approval required"),
        "caveats": list(r.get("notes") or []),
    })
    return out


def plan_reallocation(agent, decision, advisory=None, allow_challenge=False):
    """Pure planning. Touches no chain, broadcasts nothing.

    Returns a plan dict describing exactly what would happen and why, or a
    refusal dict explaining why nothing should happen.
    """
    rec = decision.get("recommended_reallocation")
    if not rec:
        return {"action": "none",
                "reason": "decision carries no recommended_reallocation"}

    src, dst = rec.get("from_pool"), rec.get("to_pool")
    if not src or not dst:
        return {"action": "none", "reason": "reallocation is missing a leg"}
    if src == dst:
        return {"action": "none", "reason": "source and destination are the same"}

    # --- ROUTE GUARD (2026-10-02) ---------------------------------------
    # A removal yields the SOURCE pool's two tokens. If those cannot reach the
    # destination pair, the reallocation cannot be performed no matter what the
    # scoring decided. Refuse rather than emit a plan that cannot execute.
    src_tokens = [t for t in src.split(":") if t]
    blocked = unroutable_tokens(agent, src_tokens, dst, src_pool=src)
    if blocked:
        return {"action": "blocked",
                "reason": (f"refusing: removing {src} yields {', '.join(blocked)}, "
                           f"which cannot reach {dst} ('{':'.join([t for t in dst.split(':') if t])}'). "
                           f"A reallocation is only performable if every released "
                           f"token is already a destination pair token or has a "
                           f"swap route to one."),
                "from_pool": src, "to_pool": dst,
                "unroutable_tokens": blocked,
                "released_tokens": src_tokens}

    verdicts = decision.get("verdicts") or {}
    sv = verdicts.get(src) or {}
    if sv.get("action") not in SOURCE_ACTIONS:
        return {"action": "none",
                "reason": f"refusing: source {src} is '{sv.get('action')}', "
                          f"not one of {SOURCE_ACTIONS}"}

    ex = TestExecutor(agent)
    try:
        shares = ex.position_shares(src)
        value = ex.position_value_usd(src)
    except Exception as e:
        return {"action": "none",
                "reason": f"could not measure the source position: "
                          f"{type(e).__name__}: {e}"}

    if not shares or shares <= 0:
        return {"action": "none",
                "reason": f"no position held in {src} (shares={shares})"}
    if value < DUST_FLOOR_USD:
        return {"action": "none",
                "reason": f"{src} position ${value:.8f} is below the "
                          f"${DUST_FLOOR_USD} management floor — a transaction "
                          f"here would cost more than it manages"}

    capped_value = value
    cap_note = None
    if value > MAX_USD_PER_ACTION:
        capped_value = MAX_USD_PER_ACTION
        cap_note = (f"value ${value:.6f} exceeds the ${MAX_USD_PER_ACTION} cap; "
                    f"the removal would be proportionally reduced")

    # Percentage form required by the Hive-Engine contract: sharesOut is a
    # PERCENTAGE of the position (0-100, max 3dp), not an absolute count.
    pct = round((capped_value / value) * 100.0, 3) if value else 0.0
    if pct <= 0:
        return {"action": "none", "reason": "computed removal percentage is zero"}

    if advisory and advisory.get("verdict") == "CHALLENGE" and not allow_challenge:
        return {"action": "blocked",
                "reason": "advisory raised a CHALLENGE on this decision; not "
                          "proceeding without an explicit override",
                "challenge": advisory.get("reason")}

    # ROUTE COST GATE (2026-10-04, ceiling set by Tydyn at 1%).
    # This must REFUSE, not annotate. A plan that would spend more converting the
    # released capital than the reallocation can earn is not a reallocation, and
    # an autonomous agent that plans such a move has a defect, not a preference.
    route_cost = _route_cost_check(agent, src, dst,
                                    _released_amounts(agent, src, value))
    if route_cost.get("status") == "refused":
        return {
            "action": "blocked",
            "reason": route_cost.get("verdict"),
            "route_cost": route_cost,
            "from_pool": src,
            "to_pool": dst,
            "position_value_usd": value,
        }

    return {
        "action": "remove_and_stage",
        "route_cost": route_cost,
        "from_pool": src,
        "to_pool": dst,
        "from_action": sv.get("action"),
        "from_reason": sv.get("reason"),
        "shares_total": shares,
        "position_value_usd": value,
        "remove_value_usd": capped_value,
        "remove_pct_of_position": pct,
        "cap_note": cap_note,
        "live": LIVE_MODE,
        "cap_usd": MAX_USD_PER_ACTION,
        # 2026-10-04 (LEG B sizing): lp_sizing.size_deposit() already existed and
        # was tested, but nothing called it - this string was the whole deposit
        # side, so the module removed and staged while the sizing math sat
        # unused. Wire it in for real: value the released pair with the validated
        # market price map, split it, and emit exact add_liquidity quantities.
        #
        # This is a PLAN only. It still cannot broadcast: the guard below keeps
        # the deposit unexecuted until a human promotes it.
        "deposit_sizing": _size_deposit_stage(agent, dst, value),
        "advisory_verdict": (advisory or {}).get("verdict"),
        "released_tokens": src_tokens,
        "routable": True,
        "route_check": ("every token yielded by the removal is either a "
                        "destination pair token or has a swap route to one"),
    }


def execute(agent, plan, allow_execute=False):
    """Carry out the REMOVAL leg only. Dry-run unless both gates are open."""
    if plan.get("action") != "remove_and_stage":
        _log({"action": "reallocation_skipped", "status": "no-action",
              "reason": plan.get("reason"), "plan": plan})
        return []

    live = LIVE_MODE and allow_execute
    if live:
        entry = TestExecutor(agent).remove_liquidity(
            plan["from_pool"],
            plan["shares_total"] * (plan["remove_pct_of_position"] / 100.0),
            reason=f"reallocation: release from {plan['from_action']} pool "
                   f"for {plan['to_pool']}")
    else:
        entry = {
            "pool": plan["from_pool"],
            "action": "remove_liquidity",
            "magnitude": plan["remove_value_usd"],
            "status": "dry-run",
            "note": (f"would remove {plan['remove_pct_of_position']}% "
                     f"(${plan['remove_value_usd']:.6f}) from "
                     f"{plan['from_pool']} for redeployment into "
                     f"{plan['to_pool']}"),
            "reason": plan["from_reason"],
            "live": False,
            "cap_usd": MAX_USD_PER_ACTION,
        }
        _log(entry)

    out = {"plan": plan, "removal": entry,
           "deposit": {"status": "not-implemented",
                       "detail": plan["deposit_plan"]},
           "timestamp": datetime.now().isoformat(timespec="seconds")}
    with open(REALLOC_LOG, "a") as fh:
        fh.write(json.dumps(out, default=str) + "\n")
    return [out]


def main():
    from liquidity_agent_v2 import LiquidityAgent

    allow_execute = "--execute" in sys.argv
    dpath, decision = latest_decision()
    if not decision:
        print("no decision artifact found")
        return 2
    advisory = latest_advisory(dpath)

    agent = LiquidityAgent()
    agent.pe.load_all()
    agent.portfolio.load_balances()
    agent.portfolio.load_positions()
    if "--deploy-idle" in sys.argv:
        # 2026-10-17: the from-idle expand — plan (always) and
        # execute (LIVE_MODE + --execute) the deploy into the
        # decision's expand_candidate.
        dplan = plan_idle_deploy(agent, decision, advisory=advisory)
        print("=== FROM-IDLE DEPLOY (the autonomy expand) ===")
        print(f"  decision : {dpath}")
        print(f"  advisory : {(advisory or {}).get('verdict', 'none')}")
        print(f"  live mode: {LIVE_MODE}   execute flag: {allow_execute}")
        print(f"  action   : {dplan.get('action')}")
        print(f"  reason   : {dplan.get('reason', '-')}")
        if dplan.get("action") == "deploy_idle":
            print(f"  pool     : {dplan.get('pool')}  "
                  f"budget ${dplan.get('budget_usd'):.4f} "
                  f"(cap ${MAX_USD_PER_ACTION})")
            sz = dplan.get("sizing") or {}
            print(f"  sizing   : {sz.get('base_token')} "
                  f"{sz.get('base_amount')} + {sz.get('quote_token')} "
                  f"{sz.get('quote_amount')} "
                  f"(deploy ${sz.get('deploy_usd')})")
            dplan = execute_idle_deploy(agent, dplan, allow_execute)
            print(f"  executed : {dplan.get('executed')}")
            for st in (dplan.get("steps") or []):
                print(f"    {st}")
            if dplan.get("reason"):
                print(f"  reason   : {dplan['reason']}")
        return 0 if dplan.get("executed") or dplan.get("action") in ("none",) else 1

    plan = plan_reallocation(agent, decision, advisory=advisory,
                             allow_challenge="--allow-challenge" in sys.argv)

    print("=== REALLOCATION PLAN (Leg A: remove + stage) ===")
    print(f"  decision : {dpath}")
    print(f"  advisory : {(advisory or {}).get('verdict', 'none')}")
    print(f"  live mode: {LIVE_MODE}   execute flag: {allow_execute}")
    print()
    for k, v in plan.items():
        if k == "deposit_plan":
            continue
        print(f"  {k:<28}: {v}")
    print()
    print(f"  deposit: {plan.get('deposit_plan')}")
    print()

    if plan.get("action") != "remove_and_stage":
        print("  NOTHING WILL BE DONE.")
        return 0


    if not (LIVE_MODE and allow_execute):
        print("  DRY-RUN — to actually remove liquidity you must set "
              "LP_TEST_LIVE=1 and pass --execute.")
        return 0

    print("  EXECUTING removal leg...")
    for out in execute(agent, plan, allow_execute=True):
        print(f"  removal: {out['removal']['status']} — "
              f"{out['removal'].get('note')}")
    return 0


# ─── 2026-10-17: THE FROM-IDLE DEPLOY (the autonomy expand) ─────────────
# No exit source required: deploy fresh idle SWAP.HIVE into the
# decision's expand_candidate. Removals proved themselves live
# (Leg A); the user's direction — capability earns autonomy —
# promotes the deposit half from human-gated to agent-gated, with
# every existing gate stacked: the $0.05 hard cap, per-leg route
# spread <= the 1% ceiling, validated-price sizing (never invents
# a quantity), LIVE_MODE + --execute to broadcast, and a
# once-per-day throttle so the agent cannot dust-add in a loop.

IDLE_DEPLOY_THROTTLE = os.path.expanduser(
    "~/.hermes/lp_tests/LAST_IDLE_DEPLOY")
IDLE_DEPLOY_MIN_USD = 0.03   # below this the TX cost manages nothing


def _today_str():
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d")


def _idle_deploy_throttled():
    try:
        return open(IDLE_DEPLOY_THROTTLE).read().strip() == _today_str()
    except Exception:
        return False


def _mark_idle_deploy():
    try:
        with open(IDLE_DEPLOY_THROTTLE, "w") as fh:
            fh.write(_today_str())
    except Exception:
        pass


def plan_idle_deploy(agent, decision, advisory=None):
    """Gate-stack a from-idle deployment; NEVER broadcasts."""
    out = {"action": "none", "mode": "deploy_idle"}
    dst = (decision.get("expand_candidate") or "").strip()
    if not dst:
        out["reason"] = "no expand candidate in the decision"
        return out
    verdict = (decision.get("verdicts") or {}).get(dst) or {}
    if verdict.get("action") != "expand":
        out["reason"] = (f"{dst} verdict is {verdict.get('action')!r}, "
                         "not 'expand'")
        return out
    if advisory and advisory.get("verdict") == "CHALLENGE":
        out["action"] = "blocked"
        out["reason"] = ("advisory CHALLENGE stands: "
                         + str(advisory.get("reason"))[:200])
        return out
    if _idle_deploy_throttled():
        out["reason"] = "already deployed from idle today (throttle)"
        return out

    # the deployable budget: the actual SWAP.HIVE idle, capped
    base, quote = dst.split(":", 1)
    hive_bal = 0.0
    try:
        tb = agent.portfolio.balances.get("SWAP.HIVE")
        hive_bal = float(getattr(tb, "balance", 0) or 0)
    except Exception:
        pass
    if hive_bal <= 0:
        out["reason"] = "no idle SWAP.HIVE to deploy"
        return out
    hive_usd = float(getattr(agent.pe, "token_prices", {}).get(
        "SWAP.HIVE", 0) or 0)
    budget = min(hive_bal * hive_usd, MAX_USD_PER_ACTION)
    if budget < IDLE_DEPLOY_MIN_USD:
        out["reason"] = (f"idle ${budget:.4f} below the "
                         f"${IDLE_DEPLOY_MIN_USD} deploy floor")
        return out

    sizing = _size_deposit_stage(agent, dst, budget)
    out["sizing"] = sizing
    if sizing.get("status") != "sized":
        out["action"] = "blocked"
        out["reason"] = f"sizing refused: {sizing.get('status')}"
        return out

    # per-leg route spread: buying base + quote with HIVE must not
    # cost more than the reallocation can earn (the 1% ceiling)
    ml = getattr(agent.pe, "market_liquidity", None) or {}
    for tok in (sizing.get("base_token"), sizing.get("quote_token")):
        spread = (ml.get(tok) or {}).get("spread_pct")
        if spread is not None and spread > 1.0:
            out["action"] = "blocked"
            out["reason"] = (f"route spread {tok} {spread:.3f}% "
                             "exceeds the 1% ceiling")
            return out

    out.update({
        "action": "deploy_idle",
        "pool": dst,
        "budget_usd": round(budget, 6),
        "live": LIVE_MODE,
        "cap_usd": MAX_USD_PER_ACTION,
        "advisory_verdict": (advisory or {}).get("verdict"),
        "throttle_file": IDLE_DEPLOY_THROTTLE,
    })
    return out


def execute_idle_deploy(agent, plan, allow_execute):
    """Swap HIVE into both legs, then add_liquidity with the
    ACTUAL received amounts (the marketpools contract balances
    the pair itself and refunds the excess)."""
    if not (LIVE_MODE and allow_execute):
        plan["executed"] = False
        plan["reason"] = ("dry-run: LP_TEST_LIVE=1 + --execute "
                          "required to broadcast")
        return plan
    if plan.get("action") != "deploy_idle":
        return plan
    ex = agent.executor
    base = plan["sizing"]["base_token"]
    quote = plan["sizing"]["quote_token"]

    def _bal(sym):
        try:
            tb = agent.portfolio.balances.get(sym)
            agent.portfolio.load_balances()
            tb = agent.portfolio.balances.get(sym)
            return float(getattr(tb, "balance", 0) or 0)
        except Exception:
            return 0.0

    before_hive = _bal("SWAP.HIVE")
    px = plan["sizing"].get("prices_used") or {}
    hive_px = float(getattr(agent.pe, "token_prices", {}).get(
        "SWAP.HIVE", 0) or 0)

    # the HIVE cost of each leg from the validated prices
    def _hive_cost(sym, amt):
        p = float(px.get(sym, 0) or 0)
        if p <= 0 or hive_px <= 0:
            return 0.0
        return (float(amt) * p) / hive_px

    cost_b = _hive_cost(base, plan["sizing"]["base_amount"])
    cost_q = _hive_cost(quote, plan["sizing"]["quote_amount"])
    if cost_b <= 0 or cost_q <= 0 or cost_b + cost_q > before_hive:
        plan["executed"] = False
        plan["reason"] = (f"leg costs {cost_b:.6f}+{cost_q:.6f} HIVE "
                          f"vs idle {before_hive:.6f}")
        _log({"action": "deploy_idle_legs", "status": "refused",
              "cost_base_hive": cost_b, "cost_quote_hive": cost_q,
              "idle_hive": before_hive, "live": True})
        return plan

    steps = []
    ok = ex.swap("SWAP.HIVE", base, cost_b)
    steps.append({"step": f"swap SWAP.HIVE->{base}",
                  "hive": round(cost_b, 6), "ok": bool(ok)})
    if not ok:
        plan["executed"] = False
        plan["reason"] = f"base leg swap failed ({base})"
        plan["steps"] = steps
        _log({"action": "deploy_idle", "status": "base-leg-failed",
              "steps": steps, "live": True})
        return plan
    ok = ex.swap("SWAP.HIVE", quote, cost_q)
    steps.append({"step": f"swap SWAP.HIVE->{quote}",
                  "hive": round(cost_q, 6), "ok": bool(ok)})
    if not ok:
        plan["executed"] = False
        plan["reason"] = f"quote leg swap failed ({quote})"
        plan["steps"] = steps
        _log({"action": "deploy_idle", "status": "quote-leg-failed",
              "steps": steps, "live": True})
        return plan

    got_b = _bal(base)
    got_q = _bal(quote)
    if got_b <= 0 or got_q <= 0:
        plan["executed"] = False
        plan["reason"] = "legs swapped but balances read empty"
        plan["steps"] = steps
        _log({"action": "deploy_idle", "status": "balance-read-failed",
              "steps": steps, "live": True})
        return plan

    ok = ex.add_liquidity(base, quote, got_b, got_q)
    steps.append({"step": f"add_liquidity {base}+{quote}",
                  "base_amt": round(got_b, 6),
                  "quote_amt": round(got_q, 6), "ok": bool(ok)})
    plan["steps"] = steps
    plan["executed"] = bool(ok)
    if ok:
        _mark_idle_deploy()
    _log({"action": "deploy_idle", "status": "executed" if ok
          else "add-liquidity-failed", "pool": plan.get("pool"),
          "steps": steps, "live": True,
          "cap_usd": MAX_USD_PER_ACTION})
    return plan


if __name__ == "__main__":
    sys.exit(main())
