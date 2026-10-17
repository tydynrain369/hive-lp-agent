"""Dividend knowledge — the part the chain cannot tell us.

WHY THIS FILE EXISTS
  Tydyn: "There are various tokens that have benefits like this, but I don't
  know of any clean, simple way to parse that information. At the moment it all
  comes from my own experience and knowledge."

  He is right, and that is verifiable. The Hive-Engine `distribution` batches
  expose exactly these fields:

      _id, active, bonusCurve, creator, excludeAccount,
      lastTickTime, numTicks, numTicksLeft, strategy, tokenBalances, tokenPair

  There is NO staked-threshold field — nothing resembling minTotalStaked,
  target, min, or cap. So a pool's activation condition cannot be derived from
  chain data, and no amount of parsing will produce it. It has to be supplied.

  Two examples Tydyn supplied from experience:
    * CENT pools — once 20k staked CENT is reached, SWAP.HBD is paid out weekly
    * PIMP pools — once ~5k staked PIMP is reached, HIVE is paid out, usually daily

HONEST STATUS
  This is NOT parsed from the chain and it is NOT verified against it. It is a
  user-supplied annotation, stored with provenance so that a future session can
  tell the difference between "the chain says this" and "Tydyn says this".

  Thresholds are STATED as approximate. They must be re-confirmed before any
  capital decision depends on them, because a wrong threshold would send money
  to a pool that does not actually pay.

SCOPE LIMIT (deliberate)
  This describes dividend PROGRAMS. It is NOT a yield calculation. A program
  that has not activated pays nothing, so activation state must come from the
  chain (does an active batch exist with this token?) — never from this file.
"""
from typing import Optional

# program_key -> {pool_pattern, reward_token, activation, threshold, period, source}
# pool_pattern uses fnmatch so "CENT:*" covers CENT:POB and CENT:BEE.
# threshold is a NUMBER IN THE STAKED TOKEN, with threshold_approx flagging
# that it came from experience rather than from a contract read.
PROGRAMS = {
    "CENT_SWAP.HBD": {
        "pool_pattern": "CENT:*",
        "reward_token": "SWAP.HBD",
        "activation": "once staked CENT reaches a threshold",
        "threshold": 20000,
        "threshold_token": "CENT",
        "threshold_approx": False,
        "period": "weekly",
        "period_hours": 168,
        "source": "Tydyn, 2026-10-02, from experience",
        "verified_on_chain": False,
    },
    "PIMP_HIVE": {
        "pool_pattern": "PIMP:*",
        "reward_token": "HIVE",
        "activation": "once staked PIMP reaches a threshold",
        "threshold": 5000,
        "threshold_token": "PIMP",
        "threshold_approx": True,
        "period": "daily (usually)",
        "period_hours": 24,
        "source": "Tydyn, 2026-10-02, from experience — threshold approximate",
        "verified_on_chain": False,
    },
    # Tydyn 2026-10-02: HSBIDAO and LBI both pay holding dividends in wallet
    # AND in their pools; HSBIDAO pays more HSBIDAO, LBI pays SWAP.HIVE daily.
    # Recorded as context so the decision layer can prefer them for that reason,
    # but with no threshold claimed because none was stated.
    "HSBIDAO_HOLDS": {
        "pool_pattern": "HSBIDAO:*",
        "reward_token": "HSBIDAO",
        "activation": "wallet and pool holding dividend",
        "distributor_account": None,
        "mechanism": "contract-based (no distributor account)",
        "threshold": None,
        "threshold_token": "HSBIDAO",
        "threshold_approx": True,
        "period": "unspecified",
        "period_hours": None,
        "source": "Tydyn, 2026-10-02",
        "verified_on_chain": False,
    },
    # Tydyn 2026-10-02: LBI's SWAP.HBD is delivered by TWO named distribution
    # accounts, and the quantities depend on amounts held:
    #   @lbi-divs  -> our SWAP.HBD once per DAY   (wallet-holding dividend)
    #   @lbi-pool  -> our SWAP.HBD once per WEEK  (pool-distribution dividend)
    # HSBIDAO, by contrast, uses a contract-based mechanism with no distributor
    # account, so there is nothing to look up and nothing to hold.
    "LBI_DIVS_SWAP.HBD": {
        # A WALLET dividend, not a pool dividend: it is keyed to the @lbi-divs
        # account balance, so it must NOT appear in any pool lookup.
        "pool_pattern": None,
        "reward_token": "SWAP.HBD",
        "activation": "amount held in the @lbi-divs distribution account",
        "distributor_account": "@lbi-divs",
        "mechanism": "account-based",
        "threshold": None,
        "threshold_token": None,
        "threshold_approx": True,
        "period": "daily",
        "period_hours": 24,
        "source": "Tydyn, 2026-10-02",
        "verified_on_chain": False,
    },
    "LBI_POOL_SWAP.HBD": {
        "pool_pattern": "LBI:*",
        "reward_token": "SWAP.HBD",
        "activation": "LP position in LBI pools",
        "distributor_account": "@lbi-pool",
        "mechanism": "account-based",
        "threshold": None,
        "threshold_token": None,
        "threshold_approx": True,
        "period": "weekly",
        "period_hours": 168,
        "source": "Tydyn, 2026-10-02",
        "verified_on_chain": False,
    },
    "LBI_SWAP.HIVE": {
        "pool_pattern": "LBI:*",
        "reward_token": "SWAP.HIVE",
        "activation": "wallet and pool holding dividend",
        "distributor_account": None,
        "mechanism": "contract-based (no distributor account)",
        "threshold": None,
        "threshold_token": "LBI",
        "threshold_approx": True,
        "period": "daily",
        "period_hours": 24,
        "source": "Tydyn, 2026-10-02",
        "verified_on_chain": False,
    },
}


def programs_for_pool(pool_id: str):
    """All known programs that could apply to a pool id, in declared order."""
    from fnmatch import fnmatch
    pid = (pool_id or "").strip()
    out = []
    for key, spec in PROGRAMS.items():
        pattern = spec.get("pool_pattern")
        if pattern is None:
            continue          # account-level, not pool-level
        if fnmatch(pid, pattern):
            out.append({"program": key, **spec})
    return out


def wallet_programs():
    """Programs that pay on account balance rather than on an LP position."""
    return [{"program": k, **v} for k, v in PROGRAMS.items()
            if v.get("pool_pattern") is None]


def is_program_active(pool_id: str, reward_token: str, active_reward_pairs=None):
    """Activation must come from the chain, not from this file.

    active_reward_pairs is the set of (pool_id, mined_token) tuples observed in
    currently-active distribution batches. A program is only paying if an active
    batch actually names that token for that pool.
    """
    if not active_reward_pairs:
        return None, "activation unknown — no active-batch data supplied"
    hit = (pool_id, reward_token) in set(active_reward_pairs)
    return hit, ("active batch pays this token" if hit
                 else "no active batch currently pays this token")


# Tydyn 2026-10-02: "regarding reward ticks distribution contracts, each tick
# happens once per day, so they are equivalent to a day." Declared, not derived:
# numTicks (365/777) is the number of ticks IN THE BATCH, not the tick length.
DEFAULT_TICK_HOURS = 24.0


def bonus_multiplier(age_periods: Optional[float], curve: Optional[dict]) -> Optional[float]:
    """Deployed age-bonus multiplier for a position, from the batch's bonusCurve.

    Mirrors lp_reward_math.estimate_batch's verified curve math:
        mult = 1 + periodBonusPct/100 * min(elapsed_periods, numPeriods)
    Two deployed-contract quirks are reproduced EXACTLY:
      - the curve only applies when numPeriods is a STRING (upstream only
        enables it for string numPeriods; a numeric numPeriods means no bonus);
      - the bonus starts at age > 1 tick (the first tick earns nothing).
    Returns None when the curve is absent/non-string — the caller must treat
    that as 'no bonus modelled', never as multiplier 1 by assumption in the
    data; pools without a curve get an explicit 1.0 from the caller.
    Deployed contract semantics (estimate_batch, verified):
      age < 1 tick  -> position EXCLUDED from the distribution entirely
      age == 1 tick -> multiplier exactly 1 (bonus needs age > tick, strict)
      age > 1 tick  -> elapsed = floor(age/tick), mult = 1 + pct/100*min(elapsed, numPeriods)
    """
    if not isinstance(curve, dict):
        return None
    if not isinstance(curve.get("numPeriods"), str):
        return None
    try:
        periods = float(curve["numPeriods"])
        pct = float(curve["periodBonusPct"])
    except (KeyError, TypeError, ValueError):
        return None
    if periods <= 0 or pct < 0 or age_periods is None or age_periods <= 1:
        # age <= 1 tick: no bonus under the deployed curve (strictly age > tick);
        # age < 1 tick is EXCLUSION, which the caller must handle — see
        # effective_share_fraction's maturity gate.
        return None
    elapsed = int(age_periods)  # whole ticks only, matches estimate_batch's ROUND_DOWN
    return 1.0 + (pct / 100.0) * min(elapsed, periods)


def effective_share_fraction(own_shares: Optional[float],
                             age_periods: Optional[float],
                             curve: Optional[dict],
                             total_effective_shares: Optional[float]):
    """Our bonus-weighted share of a pool's distribution.

    PsyopDaily's per-position display revealed the two mechanics our decision
    layer was missing (2026-10-07, browser-verified on ALIVE:HEARTBEAT):
      1. matured positions earn MORE than their raw share (advertised 87.9%
         vs 'yours weighted' 34.1% vs newcomer 18.9% — the curve is the gap);
      2. top-ups DILUTE the accumulated bonus by rebasing timeFactor on the
         entire enlarged position (2.30x -> 2.20x on a +10% top-up).

    This helper computes (effective_share, raw_share, multiplier) so every
    artifact can show both fractions and the multiplier that separates them.
    total_effective_shares must be the pool's SUM of share*mult over all
    eligible participants (lp_reward_math computes this from the complete
    participant list); None means unknown — the fraction is then NOT
    computable and must not be invented (unknown ≠ 0, unknown ≠ raw).
    """
    out = {"computable": False, "effective_share": None,
           "raw_share": None, "bonus_multiplier": None,
           "reason": None}
    if own_shares is None or own_shares <= 0:
        out["reason"] = "no own shares"
        return out
    if age_periods is not None and age_periods < 1:
        out["reason"] = ("position younger than one tick — deployed contract "
                         "excludes it from the distribution entirely")
        return out
    mult = bonus_multiplier(age_periods, curve)
    if total_effective_shares is None or total_effective_shares <= 0:
        out["reason"] = "pool effective-share total unknown (needs complete participant snapshot)"
        return out
    effective_own = own_shares * (mult if mult is not None else 1.0)
    out.update({
        "computable": True,
        "effective_share": effective_own / total_effective_shares,
        "raw_share": own_shares / total_effective_shares,
        "bonus_multiplier": mult,
        "effective_own_shares": effective_own,
        "total_effective_shares": total_effective_shares,
        "note": ("effective share is bonus-weighted (share × age multiplier); "
                 "raw_share is the unweighted fraction for comparison"),
    })
    return out


def topup_dilution(age_periods: Optional[float],
                   curve: Optional[dict], topup_fraction: float):
    """Multiplier change caused by adding `topup_fraction` of current shares.

    PsyopDaily states the dilution directly: 'a 10% top-up: time bonus drops
    2.30x → 2.20x (−4% of effective weight)'. The deployed contract rebases
    timeFactor on the whole enlarged position, so the age PERIOD falls by the
    same factor the shares rise: new_age_periods = age_periods / (1 + f).
    The dilution depends only on age and curve — share count cancels, which
    is also why the share gain from a top-up cancels across pools in ranking
    and only this pool-specific multiplier loss remains as a ranking signal.
    Returns dict(new_mult, old_mult, ratio) where ratio = new/old is the
    dilution factor to apply to a top-up score; None-multipliers mean no
    active curve — ratio 1.0, no penalty, no behaviour change.
    """
    out = {"old_mult": None, "new_mult": None, "ratio": 1.0,
           "active_curve": False, "reason": None}
    if topup_fraction <= 0:
        out["reason"] = "no top-up"
        return out
    old = bonus_multiplier(age_periods, curve)
    if old is None:
        out["reason"] = "no active bonus curve — top-up carries no dilution"
        return out
    new_age = age_periods / (1.0 + topup_fraction)
    new = bonus_multiplier(new_age, curve)
    if new is None:
        out["reason"] = "diluted age fell below the bonus threshold — curve stops applying"
        return out
    out.update({"old_mult": old, "new_mult": new, "ratio": new / old,
                "active_curve": True})
    return out


def expected_dividend_usd(reward_token: str, per_tick_usd: float,
                          period_hours: Optional[float],
                          our_share_fraction: Optional[float],
                          *, threshold_reached: Optional[bool] = None,
                          tick_hours: Optional[float] = None):
    """USD per period for OUR share — never the whole pool.

    Guards, in order:
      - threshold_reached must be explicitly True. Unknown or False yields None,
        because an unactivated program pays nothing and assuming activation
        would invent yield that does not exist.
      - our_share_fraction is required. Without pool total_shares we cannot
        state our dividend, and a whole-pool figure substituted here would
        overstate our income by orders of magnitude.
      - period_hours converts per-tick to per-period; None leaves it per-tick.

    per_tick_usd must ALREADY be the WHOLE-POOL per-tick value; this multiplies
    by our share fraction. Passing a whole-pool number where an our-share number
    is expected is the classic error this signature exists to prevent.
    """
    if threshold_reached is not True:
        return {
            "usd_per_tick_our_share": None,
            "usd_per_period_our_share": None,
            "computable": False,
            "reason": ("threshold activation not confirmed True — an unactivated "
                       "program pays nothing and must not be modelled as yield"),
        }
    if our_share_fraction is None or our_share_fraction <= 0:
        return {"usd_per_tick_our_share": None,
                "usd_per_period_our_share": None,
                "computable": False,
                "reason": ("our share fraction unknown (needs pool total_shares) — "
                           "refusing to quote a whole-pool dividend as ours")}

    usd = per_tick_usd * our_share_fraction
    out = {
        "usd_per_tick_our_share": usd,
        "computable": True,
        "threshold_reached": True,
        "share_fraction": our_share_fraction,
        "per_tick_basis": "whole pool, as supplied",
    }
    # 2026-10-02 BUG: this was usd * (period_hours / 24.0), which silently
    # assumed one tick per day. numTicks is 365 or 777 depending on the batch,
    # so a tick is NOT a day and that divisor fabricated a per-period figure.
    # The conversion needs the tick LENGTH as well as the period:
    #     usd_per_period = usd_per_tick * (period_hours / tick_hours)
    # Without tick_hours the period figure is not computable and must say so.
    if period_hours:
        out["period_hours"] = period_hours
        # Prefer the batch's own tick length; fall back to the declared
        # one-tick-per-day equivalence, and SAY WHICH was used.
        th = tick_hours if (tick_hours and tick_hours > 0) else DEFAULT_TICK_HOURS
        out["tick_hours_source"] = ("batch" if (tick_hours and tick_hours > 0)
                                   else "declared one-tick-per-day (Tydyn 2026-10-02)")
        if th and th > 0:
            tick_hours = th
            out["ticks_per_period"] = period_hours / tick_hours
            out["usd_per_period_our_share"] = usd * (period_hours / tick_hours)
        else:
            out["tick_hours"] = None
            out["usd_per_period_our_share"] = None
            out["period_note"] = (
                "period is known but tick length is not — a tick is not a day "
                "(numTicks is 365 or 777 by batch), so the per-period figure is "
                "not computable without tickHours from the batch")
    else:
        out["usd_per_period_our_share"] = None
        out["period_hours"] = None
        out["period_note"] = "period unspecified — per-tick only"
    return out


def annotate_pool(pool_id: str, reward_entries, price_map):
    """Attach program knowledge to a pool's observed reward entries.

    reward_entries are the dicts discover_rewards() produces. This only ADDS
    declared context; it never invents a reward that the chain did not report,
    and it never asserts activation.
    """
    from lp_sizing import reward_value_per_tick
    programs = programs_for_pool(pool_id)
    valued = reward_value_per_tick(reward_entries, price_map)
    known = {p["reward_token"] for p in programs}
    return {
        "pool": pool_id,
        "declared_programs": programs,
        "observed_reward_value": valued,
        "declared_but_not_observed": sorted(known - set(valued["distinct_tokens"])),
        "observed_but_undeclared": sorted(
            set(valued["distinct_tokens"]) - known),
        "note": ("program thresholds are user-supplied and NOT chain-verified; "
                 "activation must be confirmed from an active distribution batch"),
    }