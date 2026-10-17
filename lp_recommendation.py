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


def measure_fee_apr(pool, price_engine=None):
    """Return dict with fee_apr_pct, earned_base, earned_quote, window_h. None if insufficient data.

    Unit-correct (2026-09-20): fee flows and TVL are converted to USD via
    price_engine.token_prices (real traded prices from market.metrics).
    Raw token units of DIFFERENT tokens must never be summed — the original
    mixed-unit version inflated thin-pool APRs (CENT:POB showed 9.969%).
    """
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
    # Unit-correct: convert each side to USD before summing (different tokens!)
    prices = getattr(price_engine, "token_prices", {}) if price_engine else {}
    p_base = prices.get(tokens[0])
    p_quote = prices.get(tokens[1])
    if not p_base or not p_quote or p_base <= 0 or p_quote <= 0:
        # No trustworthy prices -> refuse to produce a mixed-unit APR
        return None
    fee_base_my = dVol_base * FEE_RATE * share * p_base
    fee_quote_my = dVol_quote * FEE_RATE * share * p_quote
    res_base = float(r0[tokens[0]]); res_quote = float(r0[tokens[1]])
    my_tvl = (res_base * p_base + res_quote * p_quote) * share
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


def summarize_rewards(reward_entries):
    """reward_entries: list of dicts from discover_rewards()."""
    if not reward_entries:
        return {"reward_tokens": 0, "notable": {}}
    notable = {e["mined_token"]: round(e["daily_reward"], 4)
               for e in reward_entries
               if e.get("daily_reward", 0) >= MIN_NOTABLE_DAILY_REWARD}
    return {"reward_tokens": len(reward_entries), "notable": notable}


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


def generate_recommendation(held_pools, reward_discovery, price_engine=None):
    """held_pools: list of pool_pair strings. reward_discovery: output of discover_rewards()."""
    verdicts = {}
    for p in held_pools:
        fee = measure_fee_apr(p, price_engine=price_engine) or {"fee_apr_pct": 0.0, "window_h": 0}
        rew = summarize_rewards(reward_discovery.get(p))
        verdicts[p] = {
            "pool": p,
            "fee_apr_pct": fee["fee_apr_pct"],
            "window_h": fee.get("window_h"),
            "reward_token_count": rew["reward_tokens"],
            "notable_rewards_per_day": rew["notable"],
            "classification": classify(fee["fee_apr_pct"], rew),
        }

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
