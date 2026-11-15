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


# --- fee-evidence aggregation (2026-11-05) --------------------------------
# The authoritative fee APR used to be the FIRST-to-LAST cumulative volume delta
# over a pool's whole retained history. That is the anti-pattern
# hive-lp-decision-preferences names: one anomalous interval (a whale swap, a
# re-funding) flips the entire window, and because it divided by the window SPAN
# it also meant trimming history for retention silently re-scoped the figure it
# reported. It now aggregates CONSECUTIVE-snapshot intervals with outlier
# suppression, so an outlier is confined to the interval containing it and only
# the intervals still present contribute.
#
# lp_decision.load_fee_evidence() already aggregated consecutive deltas for its
# own figure, which is explicitly marked NOT authoritative (it divides base-token
# volume by a sum of two reserves in two different tokens). The two now share an
# aggregation shape instead of drifting further apart.
FEE_APR_WINSOR_PCT = 0.90     # clamp the top decile of the ACTIVE intervals
FEE_APR_MIN_INTERVALS = 5     # below this there is nothing worth trimming
# 2026-11-15 (Fable round 6, triage item 8): a fee measurement is only a
# measurement while its evidence is FRESH. A newest snapshot older than this
# means the volume since then is unobserved — the APR would be a figure about
# a pool we are no longer watching. Past the horizon the returned APR is None
# (unmeasurable, the hold-unknown verdict) with the age surfaced.
FEE_APR_MAX_SNAPSHOT_AGE_H = 48.0
_SNAPSHOT_CACHE = {}


def _snapshots_by_pool():
    """{pool: [rows, time-ordered]} for the whole store, read once per file.

    Cached against (mtime, size): the hourly cron's fresh process reads the
    store once, while a long-lived caller cannot be served stale rows. Reading
    the store inside a per-pool function made this O(pools x rows) -- the same
    mistake lp_fee_evidence.record_fee_snapshots() had.
    """
    path = FEE_SNAPSHOTS_PATH
    if not os.path.exists(path):
        return {}
    st = os.stat(path)
    key = (st.st_mtime_ns, st.st_size)
    cached = _SNAPSHOT_CACHE.get(path)
    if cached is not None and cached[0] == key:
        return cached[1]
    per_pool = defaultdict(list)
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
            except Exception:
                continue
            if s.get("pool"):
                per_pool[s["pool"]].append(s)
    for arr in per_pool.values():
        arr.sort(key=lambda s: s.get("at_ms") or 0)
    _SNAPSHOT_CACHE[path] = (key, per_pool)
    return per_pool


def _interval_fee_aprs(snaps, price_map=None):
    """Per-interval observations over consecutive snapshots, in order.

    Each observation is (apr_pct, fee_base, fee_quote, dt_h), where the two fee
    figures are OUR fee entitlement in each token (volume x rate x our share),
    matching what earned_base/earned_quote have always meant -- not the raw
    volume delta. Only intervals whose BOTH endpoints are 'ok' and whose counters
    did not go backwards contribute: a negative delta is a counter reset
    (re-created or re-funded pool), which is not a fee measurement and must never
    be read as one.

    2026-11-13 (Opus P0-14, round 4): the APR itself is USD-VALUED through the
    price map. The old math summed raw token quantities on both sides —
    (d_base + d_quote) fees over (r0 + r1) TVL — which mixes two units and
    skews the figure by the tokens' price ratio (CENT:SWAP.HIVE read ~price-
    ratio-fold off). An interval whose token prices are missing is UNPRICED:
    it is skipped and counted, never valued at a fabricated rate. Returns
    (obs, unpriced_count).
    """
    price_map = price_map or {}
    obs = []
    unpriced = 0
    for a, b in zip(snaps, snaps[1:]):
        sa, sb = a.get("snapshot") or {}, b.get("snapshot") or {}
        if sa.get("status") != "ok" or sb.get("status") != "ok":
            continue
        try:
            dt_h = (int(b["at_ms"]) - int(a["at_ms"])) / 3_600_000.0
            if dt_h <= 0:
                continue
            # 2026-11-15 (Fable round 6, triage item 8): a missing share block
            # used to DISCARD the interval. For the APR the share cancels
            # (fees and TVL both scale by it) — the interval is still a valid
            # APR observation with share=1; only the EARNED attribution needs
            # the real share, so those rows contribute APR but never earnings
            # (has_share=False).
            _sh = sa.get("share") or {}
            try:
                share = float(_sh["num"]) / float(_sh["den"])
                has_share = True
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                share, has_share = 1.0, False
            va, vb = sa["volumes"], sb["volumes"]
            d_base = float(vb["baseVolume"]) - float(va["baseVolume"])
            d_quote = float(vb["quoteVolume"]) - float(va["quoteVolume"])
            r = sa["reserves"]
            toks = list(r.keys())
            t0, t1 = toks[0], toks[1]
        except Exception:
            continue
        if d_base < 0 or d_quote < 0:
            continue
        p0 = price_map.get(t0)
        p1 = price_map.get(t1)
        if not p0 or not p1:
            # an unpriced interval is UNKNOWN, not zero — skip and count
            unpriced += 1
            continue
        # 2026-11-15 (Fable round 6): the volume counters double-count. The
        # deployed contract's updatePoolStats(..., swap=true) increments BOTH
        # baseVolume and quoteVolume by |delta| on every swap (one swap:
        # +amountIn on one side, +|amountOut| on the other), so the summed
        # notional (d_base*p0 + d_quote*p1) is ~2x the traded value, while the
        # fee is charged on ONE side per swap. Halve it: fee = rate x notional.
        fees_usd = (d_base * float(p0) + d_quote * float(p1)) / 2.0 \
            * FEE_RATE * share
        tvl_usd = (float(r[t0]) * float(p0)
                   + float(r[t1]) * float(p1)) * share
        if tvl_usd <= 0:
            unpriced += 1
            continue
        apr_pct = (fees_usd / dt_h) * SECONDS_PER_YEAR_H / tvl_usd * 100.0
        # The two fee figures are per-side entitlements: the fee for an interval
        # IF all its swaps had settled in that one token. The pair's ACTUAL fee
        # income (either summed in USD or as a total) is HALF their sum — the
        # both-sides volume counters double-count a one-side fee (round 6). The
        # APR above carries the /2; consumers that aggregate these must too.
        obs.append((apr_pct,
                    d_base * FEE_RATE * share, d_quote * FEE_RATE * share,
                    dt_h, has_share))
    return obs, unpriced


def _winsorised_mean(xs, pct=FEE_APR_WINSOR_PCT, dts=None):
    """Mean of xs with the top (1-pct) of the ACTIVE values clamped.

    The cap is taken over NON-ZERO entries deliberately. A percentile over every
    entry collapses to zero as soon as most intervals are quiet: 90 zero-volume
    hours put the p90 at zero, so every burst is clamped away and a pool that
    genuinely earns reads as a zombie. Capping by the typical ACTIVE interval
    leaves quiet intervals at zero -- the honest reading, since the mean is
    spread over every hour we held the position -- while stopping one whale hour
    from dominating. Returns (mean, cap); cap is None when nothing was clamped.

    2026-11-13 (Opus P1-14 companion, round 4): pass dts and the mean is
    TIME-WEIGHTED — each interval counts by its LENGTH, so a 40-hour gap
    weighs as 40 quiet hours instead of as one. Equal-weighting let one long
    unmeasured stretch read the same as an hour of it.
    """
    n = len(xs)
    if n == 0:
        return None, None
    active = sorted(x for x in xs if x > 0)
    if len(active) < FEE_APR_MIN_INTERVALS:
        if dts is not None and len(dts) == n and sum(dts) > 0:
            return (sum(x * dt for x, dt in zip(xs, dts)) / sum(dts), None)
        return sum(xs) / n, None
    cap = active[max(0, min(int(len(active) * pct) - 1, len(active) - 1))]
    if dts is not None and len(dts) == n and sum(dts) > 0:
        return (sum(min(x, cap) * dt for x, dt in zip(xs, dts)) / sum(dts),
                cap)
    return sum(min(x, cap) for x in xs) / n, cap


def measure_fee_apr(pool, price_map=None):
    """Winsorised, TIME-WEIGHTED mean of per-interval fee APR, USD-valued.

    This is THE authoritative fee APR — the one source (fee-yield
    consolidation, 2026-11-13): every verdict, the ladder and the reports
    consume this figure or nothing. PoolData.apr is diagnostic-only;
    lp_decision's local figure is metadata-only and never drives a verdict.

    Returns fee_apr_pct, earned_base, earned_quote and window_h, plus audit
    fields (intervals, capped_intervals, unpriced_intervals, apr_median,
    apr_max, shareless_intervals). None when no interval qualifies -- never a
    fabricated 0, so a caller cannot mistake "unmeasurable" for "measured
    zero". 2026-11-15 (Fable round 6, triage item 8): a newest snapshot older
    than FEE_APR_MAX_SNAPSHOT_AGE_H returns fee_apr_pct=None with stale=True
    and the age — an out-of-date fee measurement is not a measurement.

    price_map (token -> USD) is required for the USD valuation: without
    prices the intervals are unpriced and the APR is None — never a
    unit-mixed guess across two tokens' quantities (P0-14).
    """
    snaps = _snapshots_by_pool().get(pool, [])
    if not snaps:
        return None
    obs, unpriced = _interval_fee_aprs(snaps, price_map)
    if not obs:
        return None
    # 2026-11-15 (Fable round 6, triage item 8): FRESHNESS. A newest
    # snapshot older than the horizon means the pool's volume since then is
    # unobserved — the APR would describe a pool we stopped watching. Fail
    # closed: the APR is None (unmeasurable -> hold-unknown verdict) with
    # the age surfaced for the report.
    _now_ms = int(datetime.now().timestamp() * 1000)
    _age_h = (_now_ms - int(snaps[-1].get("at_ms") or 0)) / 3_600_000.0
    if _age_h > FEE_APR_MAX_SNAPSHOT_AGE_H:
        return {
            "fee_apr_pct": None,
            "stale": True,
            "last_snapshot_age_h": round(_age_h, 2),
            "earned_base": None,
            "earned_quote": None,
            "window_h": None,
            "intervals": len(obs),
            "note": (f"newest snapshot {_age_h:.1f}h old (> "
                     f"{FEE_APR_MAX_SNAPSHOT_AGE_H:.0f}h horizon) — fee "
                     f"evidence UNMEASURABLE, not zero"),
        }
    aprs = [o[0] for o in obs]
    dts = [o[3] for o in obs]
    apr_pct, cap = _winsorised_mean(aprs, dts=dts)
    srt = sorted(aprs)
    _shared = [o for o in obs if o[4]]
    return {
        "fee_apr_pct": round(apr_pct, 3),
        # share-less rows contribute the APR but never earnings: the earned
        # attribution is OUR share of the flow, and an unknown share is not
        # inventable (share-less count surfaced for audit)
        "shareless_intervals": len(obs) - len(_shared),
        "earned_base": sum(o[1] for o in _shared) if _shared else None,
        "earned_quote": sum(o[2] for o in _shared) if _shared else None,
        # Sum of the CONTRIBUTING intervals, not the first-to-last span: the
        # numerator sums those same intervals, so a gap between them must not
        # be charged as hours we earned nothing in.
        "window_h": round(sum(o[3] for o in obs), 2),
        "intervals": len(obs),
        "capped_intervals": (sum(1 for a in aprs if a > cap)
                             if cap is not None else 0),
        "unpriced_intervals": unpriced,
        "apr_median": round(srt[len(srt) // 2], 3),
        "apr_max": round(srt[-1], 3),
        "aggregation": "winsorised, time-weighted mean of per-interval "
                       "USD-valued APR (consecutive snapshots)",
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
        # N5 (Opus round 3): a MEASURED delta in a token we cannot price is
        # UNKNOWN fee income, not zero. Valuing it at 0 understated fee_usd,
        # inflating the growth multiple and driving "consolidate" — a
        # release instruction from a PRICING gap. None = not computable;
        # threshold_analysis already treats None that way.
        _unpriced_earned = [
            sym for sym, earned, px in
            ((getattr(position, "base", ""), earned_base,
              price_map.get(getattr(position, "base", ""))),
             (getattr(position, "quote", ""), earned_quote,
              price_map.get(getattr(position, "quote", ""))))
            if (earned or 0.0) > 0 and not px
        ]
        if _unpriced_earned:
            out["our_measured_fee_usd"] = None
            out["fee_evidence"] = "measured-but-unpriced"
            out["reason"] = (f"measured fee accrual in unpriced token(s) "
                             f"{_unpriced_earned} — fee USD UNKNOWN, not zero")
        else:
            # 2026-11-15 (Fable round 6): the earned_base/earned_quote pair
            # double-counts — the contract increments BOTH volume counters per
            # swap but charges the fee on ONE side. The actual fee income is
            # half the two-side sum.
            out["our_measured_fee_usd"] = (base_usd + quote_usd) / 2.0
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
        # 2026-11-13 (Claude review P0-6): None is the "unmeasurable" signal.
        # Coercing it to 0.0 defeated the cron's `is not None` filter and fed a
        # fabricated authoritative 0.0 into decide(), making the decision
        # layer's fail-closed branch dead on the live path. None is not zero.
        # ONE authoritative APR, now USD-valued through the price map the
        # caller already holds (fee-yield consolidation, P0-14)
        fee = measure_fee_apr(p, price_map=price_map)
        if fee is None:
            fee = {"fee_apr_pct": None, "window_h": None}
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
        # Focus on the most productive: highest fee_apr first, fall back to reward count.
        # 2026-11-13 (Opus review, A5): fee_apr_pct can now be None (UNKNOWN,
        # P0-6) for reward-productive pools — a raw tuple compare would raise
        # TypeError and blind the whole hour's decision. UNKNOWN ranks below
        # every measured figure in this RANKING, not in any gate.
        target = max(productive, key=lambda x: (
            x["fee_apr_pct"] if x["fee_apr_pct"] is not None else -1.0,
            x["reward_token_count"]))
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
