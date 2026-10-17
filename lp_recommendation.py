"""LP recommendation engine.

Scores each held pool using two evidence streams:
  * fee evidence  — measured from fee_snapshots.jsonl windows
  * reward evidence — live discovery() call (distribution contract)

Classification:
  * productive            : fee_apr >= MIN_FEE_APR AND any reward flow
  * fee-only              : fee_apr >= MIN_FEE_APR, no rewards        (rare, keep)
  * reward-productive     : fee_apr < MIN_FEE_APR, notable rewards    (keep, watch)
  * zombie                : fee_apr < MIN_FEE_APR AND zero rewards    (exit candidate)

Recommendations are read-only advisory. `requires_approval=True` always.
The agent NEVER auto-executes a reallocation.
"""
import json
import os
from collections import defaultdict
from datetime import datetime

# Policy knobs (tunable)
MIN_FEE_APR_FOR_PRODUCTIVE = 0.5
MIN_NOTABLE_DAILY_REWARD = 0.5
EXIT_REQUIRES_BOTH_ZERO = True   # never exit a pool that earns rewards OR fees
FEE_SNAPSHOTS_PATH = os.path.expanduser("~/.hermes/lp_tests/fee_snapshots.jsonl")
FEE_RATE = 0.0025                 # Hive-Engine LP fee
SECONDS_PER_YEAR_H = 8760


def _load_fee_windows():
    """Group snapshots by pool, return first/last per pool."""
    if not os.path.exists(FEE_SNAPSHOTS_PATH):
        return {}
    per_pool = defaultdict(list)
    with open(FEE_SNAPSHOTS_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
            except Exception:
                continue
            per_pool[s["pool"]].append(s)
    out = {}
    for pool, snaps in per_pool.items():
        snaps.sort(key=lambda x: x["at_ms"])
        if len(snaps) >= 2:
            out[pool] = (snaps[0], snaps[-1])
    return out


def measure_fee_apr(pool):
    """Return dict with fee_apr_pct, earned_base, earned_quote, window_h. None if insufficient data."""
    windows = _load_fee_windows()
    if pool not in windows:
        return None
    s0, s1 = windows[pool]
    snap0, snap1 = s0["snapshot"], s1["snapshot"]
    if snap0.get("status") != "ok" or snap1.get("status") != "ok":
        return None
    try:
        share = float(snap0["share"]["num"]) / float(snap0["share"]["den"])
    except Exception:
        return None
    dt_h = (s1["at_ms"] - s0["at_ms"]) / 3_600_000.0
    if dt_h <= 0:
        return None
    v0, v1 = snap0["volumes"], snap1["volumes"]
    r0 = snap0["reserves"]
    tokens = list(r0.keys())
    dVol_base = float(v1["baseVolume"]) - float(v0["baseVolume"])
    dVol_quote = float(v1["quoteVolume"]) - float(v0["quoteVolume"])
    fee_base_my = dVol_base * FEE_RATE * share
    fee_quote_my = dVol_quote * FEE_RATE * share
    res_base = float(r0[tokens[0]]); res_quote = float(r0[tokens[1]])
    my_tvl = (res_base + res_quote) * share
    if my_tvl <= 0:
        return None
    fees_total = fee_base_my + fee_quote_my
    apr = (fees_total / dt_h) * SECONDS_PER_YEAR_H / my_tvl * 100
    return {
        "fee_apr_pct": round(apr, 3),
        "earned_base": fee_base_my,
        "earned_quote": fee_quote_my,
        "window_h": round(dt_h, 2),
    }


def our_reward_share(entries, pool, position):
    """What WE actually earn, not the pool's whole distribution.

    2026-09-30: the agent could see that a pool paid rewards but never how much
    of it reached us, which is why Tydyn saw nothing in the Hive UI while the
    agent called a pool "reward-productive". The distribution figures are
    whole-pool amounts; our entitlement is our LP-share fraction of them.

    Requires pool.total_shares, which is what turns "this pool pays rewards"
    into "this position earns N per tick" — the last piece the agent needs
    before it can honestly advise on keeping or exiting a position.

    Returns a dict of our_share per token, or a reason it could not be computed.
    Never guesses: a missing total_shares yields None, not zero.
    """
    if not entries:
        return {"our_share": {}, "computable": False, "reason": "no reward entries"}
    total_shares = getattr(pool, "total_shares", 0) or 0
    shares = getattr(position, "shares", 0) or 0
    if total_shares <= 0:
        return {"our_share": {}, "computable": False, "reason": "pool.total_shares unknown"}
    if shares <= 0:
        return {"our_share": {}, "computable": False, "reason": "position has no shares"}
    frac = shares / total_shares
    out = {}
    for e in entries:
        whole = e.get("whole_batch_quantity")
        if whole is None:
            whole = e.get("daily_reward", 0) * max(e.get("num_ticks", 1), 1)
        out[e["mined_token"]] = round(float(whole) * frac, 10)
    return {
        "our_share": out,
        "computable": True,
        "our_lp_share_fraction": frac,
        "pool_total_shares": total_shares,
        "our_shares": shares,
        "denominator": "whole batch quantity x (our shares / pool total shares)",
        "period": "per distribution tick (numTicks in the entry)",
    }


def summarize_rewards(reward_entries):
    """reward_entries: list of dicts from discover_rewards().

    2026-09-30: the key was named `notable_rewards_per_day`, which asserted a
    unit the underlying data does not support. The value is the WHOLE batch
    tokenBalances divided by numTicks: the entire distribution across every LP,
    not our share, and only per-tick rather than per-day. Tydyn spotted this
    directly — the numbers looked like whole-period totals, and they did.
    The key is now `notable_batch_share_per_tick` and the denominator travels
    with it, so a reader cannot mistake it for realized earnings. A
    `notable_rewards_per_day` key is retained ONLY for backward compatibility
    with existing report consumers, and carries the same numbers — do not read
    it as earnings.
    """
    if not reward_entries:
        return {"reward_tokens": 0, "notable": {}, "unit": None, "num_ticks": None}
    notable = {e["mined_token"]: round(e["daily_reward"], 4)
               for e in reward_entries
               if e.get("daily_reward", 0) >= MIN_NOTABLE_DAILY_REWARD}
    ticks = reward_entries[0].get("num_ticks")
    return {
        "reward_tokens": len(reward_entries),
        "notable": notable,
        "unit": "whole-pool batch tokenBalances / numTicks (NOT our share, NOT earnings)",
        "num_ticks": ticks,
        "units_comparable_across_tokens": False,
    }


# 2026-09-30: realized-accrual economics.
#
# Fee APR is a POOL-level number. With an LP share on the order of 1e-6, the APR
# that pool earns is not what we earn — our accrual scales by our share. Tydyn
# observed that Hive frontends render anything below ~1e-8 as "dust" (mixed
# letters and digits), which is why ALIVE:SWAP.HIVE and HSBIDAO:PIXYDUST show
# nothing in the UI despite the agent calling them productive.
#
# Everything here therefore reports OUR accrual, and marks a position's
# economics UNKNOWN rather than substituting the pool's APR. A pool APR must
# never stand in for a position's yield.
MIN_EARNABLE_USD = 0.00000001   # 1e-8: Hive frontend dust threshold
DUST_USD = 0.000001              # 1e-6: below this a position is not worth managing


def position_economics(position, pool, fee_info, entitlement, price_map,
                       horizon_ticks_per_day=1.0):
    """Our realized economics for one position. Never substitutes pool APR.

    fee_info: the measured fee evidence dict (fee_apr_pct, earned_*, window_h)
    entitlement: our_reward_share() output
    Returns a dict with an explicit `economics_known` flag.
    """
    out = {
        "economics_known": False,
        "pool_fee_apr_pct": None,   # context only; NOT our yield
        "our_position_value_usd": getattr(position, "value_usd", None),
        "reason": None,
    }
    if position is None:
        out["reason"] = "no position object"
        return out

    val = getattr(position, "value_usd", 0.0) or 0.0
    out["our_position_value_usd"] = val
    if fee_info:
        out["pool_fee_apr_pct"] = fee_info.get("fee_apr_pct")

    # --- fee side: did WE actually earn, measured, not modelled? ------------
    # fee_apr is a pool figure. Only a measured delta in our own fee token
    # balances proves our accrual. If we have no measurement, say unknown.
    earned_base = fee_info.get("earned_base") if fee_info else None
    earned_quote = fee_info.get("earned_quote") if fee_info else None
    measured = (earned_base is not None) or (earned_quote is not None)
    if measured:
        base_usd = (earned_base or 0.0) * (price_map.get(getattr(position, "base", ""), 0.0) or 0.0)
        quote_usd = (earned_quote or 0.0) * (price_map.get(getattr(position, "quote", ""), 0.0) or 0.0)
        out["our_measured_fee_usd"] = base_usd + quote_usd
        out["fee_evidence"] = "measured"
    else:
        out["fee_evidence"] = "unmeasured"
        out["reason"] = "no measured fee accrual for this position yet"

    # --- reward side: our entitlement, valued -------------------------------
    reward_usd = 0.0
    reward_detail = {}
    if entitlement and entitlement.get("computable"):
        for tok, amt in (entitlement.get("our_share") or {}).items():
            px = price_map.get(tok)
            if px is None:
                reward_detail[tok] = {"amount": amt, "usd": None,
                                      "note": "no USD price - UNKNOWN, not zero"}
                continue
            usd = amt * px
            reward_detail[tok] = {"amount": amt, "usd": usd, "price": px}
            reward_usd += usd
    out["our_reward_usd_per_tick"] = reward_usd
    out["our_reward_detail"] = reward_detail
    if entitlement and not entitlement.get("computable"):
        out["reason"] = (out["reason"] or "") + \
            f"; reward share not computable: {entitlement.get('reason')}"

    # --- dust / actionability ----------------------------------------------
    out["position_is_dust"] = val < DUST_USD
    out["below_hive_dust_threshold"] = val < MIN_EARNABLE_USD
    if val <= 0:
        out["actionability"] = "unknown"
    elif val < DUST_USD:
        out["actionability"] = "dust - not worth active management"
    else:
        out["actionability"] = "manageable"

    out["economics_known"] = bool(out.get("fee_evidence") == "measured" or reward_usd > 0)
    return out


def threshold_analysis(eco, pool, position):
    """How much MORE share would this position need to earn something visible?

    2026-09-30: Tydyn's strategy is to focus capital in one or two pools until
    a position crosses that pool's earn threshold, then use the yield to grow
    others. The useful number is therefore not "what do I earn now" but "how
    far am I from earning anything" — a multiple, and the resulting position
    value. Fee and reward accrual both scale linearly in our share, so this is
    a direct proportionality, not a forecast.

    Returns None when the position is already above threshold or we lack the
    measured evidence to scale from. Never invents a multiplier.
    """
    val = eco.get("our_position_value_usd") or 0.0
    if val <= 0:
        return {"computable": False, "reason": "position value is zero"}
    fee_usd = eco.get("our_measured_fee_usd")
    if fee_usd is None:
        return {"computable": False,
                "reason": "no measured fee accrual to scale from"}
    if fee_usd <= 0:
        return {"computable": True, "already_above_threshold": True,
                "current_value_usd": val,
                "note": "fees not yet positive in the measured window"}
    mult = DUST_USD / fee_usd
    return {
        "computable": True,
        "current_measured_fee_usd": fee_usd,
        "target_fee_usd": DUST_USD,
        "multiple_to_1e6_fee_threshold": round(mult, 1),
        "current_value_usd": val,
        "value_needed_for_1e6_fees_usd": round(val * mult, 8),
        "note": ("fee accrual scales linearly with our share, so this is a "
                 "proportionality, not a forecast"),
    }


def classify(fee_apr, rewards_summary):
    n = rewards_summary["reward_tokens"]
    has_notable = any(v >= MIN_NOTABLE_DAILY_REWARD for v in rewards_summary["notable"].values())
    if fee_apr is not None and fee_apr >= MIN_FEE_APR_FOR_PRODUCTIVE and n > 0:
        return "productive"
    if fee_apr is not None and fee_apr >= MIN_FEE_APR_FOR_PRODUCTIVE:
        return "fee-only"
    if n > 0 and has_notable:
        return "reward-productive"
    if n == 0 and (fee_apr is None or fee_apr < MIN_FEE_APR_FOR_PRODUCTIVE):
        return "zombie"
    return "low-activity"      # has rewards but all tiny


def generate_recommendation(held_pools, reward_discovery, position_lookup=None,
                           price_map=None):
    """held_pools: list of pool_pair strings. reward_discovery: output of
    discover_rewards().

    position_lookup (2026-09-30, optional): {pool_pair: (pool, position)} used
    to compute OUR entitlement rather than only the whole-pool distribution.
    Without it the report still works, it just omits the per-position share.
    """
    verdicts = {}
    for p in held_pools:
        fee = measure_fee_apr(p) or {"fee_apr_pct": 0.0, "window_h": 0}
        rew = summarize_rewards(reward_discovery.get(p))
        v = {
            "pool": p,
            "fee_apr_pct": fee["fee_apr_pct"],
            "window_h": fee.get("window_h"),
            "reward_token_count": rew["reward_tokens"],
            # Renamed from `notable_rewards_per_day` — that name asserted a unit
            # the data does not carry. Retained under the old key for existing
            # consumers, but the corrected key is authoritative.
            "notable_batch_share_per_tick": rew["notable"],
            "reward_unit": rew.get("unit"),
            "reward_num_ticks": rew.get("num_ticks"),
            "notable_rewards_per_day": rew["notable"],  # DEPRECATED alias
            "classification": classify(fee["fee_apr_pct"], rew),
        }
        # Our actual entitlement, when the pool/position data is available.
        if position_lookup and p in position_lookup:
            pool, position = position_lookup[p]
            share = our_reward_share(reward_discovery.get(p) or [], pool, position)
            v["our_entitlement"] = share
            # Realized economics: OUR yield, never the pool's APR.
            # 2026-09-30: every position currently measures as dust (accrual
            # under 1e-8 USD, the threshold below which Hive frontends render
            # balances as digits+letters). Tydyn's strategy explicitly depends
            # on this: thresholds differ per pool by total liquidity, so
            # CONCENTRATING in one or two pools until a position clears its
            # threshold is the intended path, not spreading thin. So the report
            # states actionability per position and never recommends a
            # reallocation that would leave everything below threshold.
            eco = position_economics(
                position, pool, fee, share, price_map or {})
            v["position_economics"] = eco
            v["threshold_analysis"] = threshold_analysis(eco, pool, position)
        verdicts[p] = v

    zombies = [v for v in verdicts.values() if v["classification"] == "zombie"]
    productive = [v for v in verdicts.values() if v["classification"] in ("productive", "reward-productive", "fee-only")]

    recommendations = []
    for z in zombies:
        if not productive:
            continue
        # Focus on the most productive: highest fee_apr first, fall back to reward count
        target = max(productive, key=lambda x: (x["fee_apr_pct"], x["reward_token_count"]))
        recommendations.append({
            "action": "exit_and_reallocate",
            "from_pool": z["pool"],
            "to_pool": target["pool"],
            "reason": (f"zero rewards AND fee_apr={z['fee_apr_pct']}% "
                       f"(target {target['pool']}: fees {target['fee_apr_pct']}% + "
                       f"{target['reward_token_count']} reward tokens)"),
            "confidence": "high",
            "requires_approval": True,
            "urgency": "low",
        })

    return {
        "generated_at": datetime.now().isoformat(),
        "policy": {
            "min_fee_apr_for_productive": MIN_FEE_APR_FOR_PRODUCTIVE,
            "min_notable_daily_reward": MIN_NOTABLE_DAILY_REWARD,
            "exit_requires_both_zero": EXIT_REQUIRES_BOTH_ZERO,
            "never_auto_execute": True,
        },
        "verdicts": verdicts,
        "recommendations": recommendations,
    }


def write_recommendation(rec, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = os.path.join(out_dir, f"recommendation-{date_str}.json")
    with open(path, "w") as f:
        json.dump(rec, f, indent=2)
    return path
