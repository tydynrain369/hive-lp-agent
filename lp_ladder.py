"""Priority ladder + marginal-rate water-filling for LP destinations.

WHY THIS EXISTS (2026-11-02, Tydyn's call)
  The decision layer already computes a *ranking* of expand candidates
  (``expand_score``), but ``READING_B_SINGLE_EXPAND`` truncates it to rung #1 and
  the executor is hard-capped at one action per tick. Tydyn asked for a priority
  *ladder*: work the bullseye pool, and — as capital allows — also feed the pools
  lower down, so existing assets are leveraged as fully as possible.

  The chosen policy is MARGINAL-RATE WATER-FILLING (not even spreading): deploy
  to the top rung until its marginal rate falls to the next rung's, then spill
  down. This is concentration *generalized* — it concentrates while one pool is
  clearly best and only spreads once the top pool saturates — and it never lowers
  the blended return the way naive spreading does.

THE ECONOMICS IT ENCODES
  Fee and reward accrual are linear in your LP *share*, but the per-share RATE
  falls as you add: a fixed reward budget and a fixed trade volume get divided
  across more shares. So the marginal return of a deposit of size x into a pool
  with TVL V is

      r(x) = r0 * V / (V + x)

  i.e. r0 at zero size, decaying toward 0 as x grows. ``r0`` is the pool's
  current per-dollar rate; the water-filling optimum equalizes r across rungs.

UNITS
  rate0 is USD-per-day captured per USD freshly deposited. It has two additive
  components, both expressible per USD deposited:

      fee    : fee_apr_pct / 100 / 365            (APR% is per year)
      reward : reward_per_hive / hive_usd         (reward_per_hive is
              whole-pool USD/day per HIVE of TVL; dividing by HIVE-USD makes it
              USD/day per USD deposited)

  A component whose input is UNKNOWN contributes nothing AND is flagged; a rung
  whose *entire* rate is unknown is EXCLUDED from the ladder, never scored as
  zero. (A missing measurement is not a small measurement — the standing rule.)

This module is pure: no network, no broadcast, no import of the agent. State
helpers exist only to persist cumulative deployed-per-rung across ticks.
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional

LADDER_STATE_PATH = os.path.expanduser("~/.hermes/lp_tests/ladder_state.json")

# A rung whose current marginal rate is at or below this (USD/day per USD) is
# treated as not worth new capital at all. Kept explicit and tunable rather than
# an inline literal.
MIN_MARGINAL_RATE = 0.0


# --------------------------------------------------------------------------
# marginal rate
# --------------------------------------------------------------------------
def marginal_rate_detail(fee_apr_pct, reward_per_hive, hive_usd):
    """Break out the two additive components of a rung's per-dollar daily rate.

    Returns dict with:
      rate       : total USD/day per USD deposited, or None if NOTHING is known
      fee        : fee component (USD/day/USD) or None if unmeasured
      reward     : reward component (USD/day/USD) or None if unmeasured
      complete   : True only when BOTH components are measured
    """
    fee = None
    if fee_apr_pct is not None:
        try:
            fee = float(fee_apr_pct) / 100.0 / 365.0
        except (TypeError, ValueError):
            fee = None

    reward = None
    if reward_per_hive is not None and hive_usd:
        try:
            reward = float(reward_per_hive) / float(hive_usd)
        except (TypeError, ValueError, ZeroDivisionError):
            reward = None

    comps = [c for c in (fee, reward) if c is not None]
    if not comps:
        return {"rate": None, "fee": fee, "reward": reward, "complete": False}
    return {
        "rate": sum(comps),
        "fee": fee,
        "reward": reward,
        "complete": (fee is not None and reward is not None),
    }


def marginal_rate(fee_apr_pct, reward_per_hive, hive_usd) -> Optional[float]:
    """USD/day captured per USD freshly deposited, or None if unmeasureable."""
    return marginal_rate_detail(fee_apr_pct, reward_per_hive, hive_usd)["rate"]


# --------------------------------------------------------------------------
# rung bookkeeping
# --------------------------------------------------------------------------
def _clean_rungs(rungs):
    """Normalize + filter rungs; return (usable, excluded).

    A rung is usable only with a measured, positive rate0 AND a positive TVL.
    Everything else is returned in ``excluded`` with a reason so it is visible,
    never silently zeroed.
    """
    usable, excluded = [], []
    for r in (rungs or []):
        pool = (r or {}).get("pool")
        if not pool:
            continue
        r0 = r.get("rate0")
        tvl = r.get("tvl_usd")
        if r0 is None:
            excluded.append({"pool": pool, "reason": "rate unmeasured"})
            continue
        try:
            r0 = float(r0)
        except (TypeError, ValueError):
            excluded.append({"pool": pool, "reason": "rate not numeric"})
            continue
        if tvl is None:
            excluded.append({"pool": pool, "reason": "TVL unmeasured"})
            continue
        try:
            tvl = float(tvl)
        except (TypeError, ValueError):
            excluded.append({"pool": pool, "reason": "TVL not numeric"})
            continue
        if tvl <= 0:
            excluded.append({"pool": pool, "reason": "TVL not positive"})
            continue
        if r0 <= MIN_MARGINAL_RATE:
            excluded.append({"pool": pool, "reason": "no positive marginal rate"})
            continue
        cap = r.get("cap_usd")
        usable.append({
            "pool": pool,
            "rate0": r0,
            "tvl_usd": tvl,
            "deployed_usd": float(r.get("deployed_usd") or 0.0),
            "cap_usd": (float(cap) if cap is not None else None),
        })
    return usable, excluded


def current_marginal_rate(rung) -> float:
    """r(x=deployed) = rate0 * V / (V + deployed). The rate for the NEXT dollar."""
    v = rung["tvl_usd"]
    d = max(0.0, rung.get("deployed_usd") or 0.0)
    return rung["rate0"] * v / (v + d)


def _rate_at(rung, x: float) -> float:
    v = rung["tvl_usd"]
    d = max(0.0, rung.get("deployed_usd") or 0.0) + max(0.0, x)
    return rung["rate0"] * v / (v + d)


def income_of(rung, x: float) -> float:
    """USD/day earned by depositing x into rung = integral of r(u)du = V*r0*ln(1+x/V)."""
    v = rung["tvl_usd"]
    d = max(0.0, rung.get("deployed_usd") or 0.0)
    x = max(0.0, x)
    if x <= 0:
        return 0.0
    # deployed shifts the base: adding x on top of d moves V_eff from V+d to V+d+x
    return v * rung["rate0"] * math.log((v + d + x) / (v + d))


# --------------------------------------------------------------------------
# water-filling
# --------------------------------------------------------------------------
def _solve_active_set(ordered, capital_usd):
    """Largest active set whose marginal rung is still strictly above the level.

    ordered: rungs sorted by rate0 desc. Returns (active, level).
    Standard capped-free water-filling: for the top-k rungs the level is
    lambda = (sum V_i*r0_i) / (C + sum V_i); rung k belongs only while
    r0_k > lambda.
    """
    active = []
    level = 0.0
    vsum = 0.0
    rsum = 0.0
    for rung in ordered:
        v = rung["tvl_usd"]
        # rung's cap headroom reduces effective capital it can absorb
        trial_active = active + [rung]
        vsum_t = vsum + v
        rsum_t = rsum + v * rung["rate0"]
        lam = rsum_t / (capital_usd + vsum_t) if (capital_usd + vsum_t) > 0 else 0.0
        if rung["rate0"] > lam:
            active = trial_active
            vsum, rsum = vsum_t, rsum_t
            level = lam
        else:
            break
    return active, level


def water_fill(rungs, capital_usd: float):
    """Allocate ``capital_usd`` across rungs to equalize marginal rate.

    Returns dict:
      allocation : {pool: usd} for every usable rung (0.0 when it gets nothing)
      level      : the equalized marginal rate lambda
      income_usd_per_day : total new USD/day from the whole allocation
      ladder     : ordered rows (rate0 desc) with per-rung detail
      excluded   : rungs dropped and why
    """
    usable, excluded = _clean_rungs(rungs)
    capital_usd = max(0.0, float(capital_usd or 0.0))
    ordered = sorted(usable, key=lambda r: (-r["rate0"], r["pool"]))

    # caps complicate the closed form; support an optional per-rung cap by
    # fixing capped rungs and re-solving the remainder. Caps are optional and
    # usually absent, so this stays a single pass in the common case.
    allocation = {r["pool"]: 0.0 for r in ordered}
    remaining = capital_usd
    pool_active = list(ordered)
    # iteratively fix any rung whose uncapped allocation would exceed its cap
    for _ in range(len(ordered) + 1):
        active, level = _solve_active_set(pool_active, remaining)
        if not active:
            break
        over = []
        for rung in active:
            x = rung["tvl_usd"] * (rung["rate0"] / level - 1.0) if level > 0 else 0.0
            x = max(0.0, x)
            if rung["cap_usd"] is not None and x > rung["cap_usd"]:
                over.append((rung, rung["cap_usd"]))
        if not over:
            for rung in active:
                x = rung["tvl_usd"] * (rung["rate0"] / level - 1.0) if level > 0 else 0.0
                allocation[rung["pool"]] = max(0.0, x)
            break
        for rung, cap in over:
            allocation[rung["pool"]] = cap
            remaining -= cap
            pool_active = [r for r in pool_active if r["pool"] != rung["pool"]]
        if remaining <= 0:
            break

    income = sum(income_of(r, allocation.get(r["pool"], 0.0)) for r in ordered)
    ladder = [{
        "pool": r["pool"],
        "rate0": r["rate0"],
        "rate_now": current_marginal_rate(r),
        "tvl_usd": r["tvl_usd"],
        "deployed_usd": r["deployed_usd"],
        "allocation_usd": round(allocation.get(r["pool"], 0.0), 10),
        "cap_usd": r["cap_usd"],
    } for r in ordered]
    return {
        "allocation": allocation,
        "level": level if ordered else 0.0,
        "income_usd_per_day": income,
        "capital_usd": capital_usd,
        "ladder": ladder,
        "excluded": excluded,
    }


def next_deploy(rungs, capital_usd: float):
    """The single next deployment (1 action/tick): argmax current marginal rate,
    sized to the crossover where it meets the runner-up.

    Returns dict: {pool, amount_usd, rate_now, runner_up_pool, runner_up_rate,
                   rate_after} or {pool: None, reason}.
    """
    usable, excluded = _clean_rungs(rungs)
    capital_usd = max(0.0, float(capital_usd or 0.0))
    if not usable:
        return {"pool": None, "reason": "no usable rungs",
                "excluded": excluded, "amount_usd": 0.0}
    if capital_usd <= 0:
        return {"pool": None, "reason": "no capital to deploy",
                "excluded": excluded, "amount_usd": 0.0}

    ranked = sorted(usable, key=lambda r: (-current_marginal_rate(r), -r["rate0"], r["pool"]))
    top = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    top_rate = current_marginal_rate(top)
    runner_rate = current_marginal_rate(runner) if runner else 0.0

    # crossover: r_top(x) == runner_rate  ->  V*r0/(V+d+x) = runner_rate
    if runner_rate > 0:
        x_star = top["tvl_usd"] * top["rate0"] / runner_rate - top["tvl_usd"] - top["deployed_usd"]
        x_star = max(0.0, x_star)
    else:
        x_star = capital_usd  # nothing below competes; fill to capital
    if top["cap_usd"] is not None:
        x_star = min(x_star, top["cap_usd"])

    amount = min(capital_usd, x_star)
    return {
        "pool": top["pool"],
        "amount_usd": amount,
        "rate_now": top_rate,
        "runner_up_pool": runner["pool"] if runner else None,
        "runner_up_rate": runner_rate,
        "rate_after": _rate_at(top, amount),
        "excluded": excluded,
    }


# --------------------------------------------------------------------------
# persisted fill state (cumulative deployed per rung, per epoch)
# --------------------------------------------------------------------------
def load_state(path: str = LADDER_STATE_PATH) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def deployed_for(state: dict, pool: str) -> float:
    try:
        return float((state or {}).get(pool, {}).get("deployed_usd") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def record_deploy(pool: str, amount_usd: float, path: str = LADDER_STATE_PATH) -> dict:
    """Add a verified deploy to the rung's cumulative fill. Caller persists only
    on success; this function itself writes the file."""
    state = load_state(path)
    row = dict(state.get(pool) or {})
    row["deployed_usd"] = float(row.get("deployed_usd") or 0.0) + float(amount_usd or 0.0)
    from datetime import datetime, timezone
    row["updated"] = datetime.now(timezone.utc).isoformat()
    state[pool] = row
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(state, fh, indent=2)
    return state


def reset_state(path: str = LADDER_STATE_PATH) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
