"""Deposit sizing math — Leg B.

Turns a chosen destination pool and a USD budget into the exact pair-token
amounts an add_liquidity call needs, and reports what the position would then
earn. Read-only and pure: it computes, it never broadcasts.

WHY REWARD VALUE, NOT REWARD COUNT
  2026-10-02: discover_rewards() emits 'mined_token'; lp_decision read
  'token'/'symbol', neither of which exists, so every entry became the literal
  "?" and reward_count was counting ENTRIES rather than distinct paying tokens.
  Worse, counts are not comparable across tokens — HSBIDAO:PIXYDUST distributes
  THREE at 1.35e9 per tick alongside GLITTER at 29.1. A count cannot express
  that; a USD value can. The same code already flags
  units_comparable_across_tokens: False, which is exactly this point.

WHY THIS MATTERS FOR DIVIDEND TOKENS
  Tydyn: HSBIDAO and LBI are solid, hold dividends in wallet AND in their
  pools, HSBIDAO paying more HSBIDAO and LBI paying SWAP.HIVE daily. A dividend
  target therefore has TWO yield sources — trading fees and the distribution —
  and sizing has to value both, or it will keep picking pools that look
  productive only because they list many reward entries.

HONEST LIMITS (stated rather than hidden)
  - Distribution figures are whole-pool per-tick amounts. Our share requires
    pool total_shares; without it, dividend is reported as not-computable
    rather than guessed.
  - numTicks is 365 or 777 depending on the batch, so "per tick" is not
    "per day" in a literal sense. The period is carried through unchanged.
  - A constant-product swap moves price; route quotes here are indicative only
    and are NOT an executable liquidation quote.
"""
from decimal import Decimal, getcontext

getcontext().prec = 34

HIVE_LIKE = "SWAP.HIVE"


class SizingError(ValueError):
    """Raised when a deposit cannot be sized from the evidence given."""


def _dec(x, default=None):
    if x is None:
        return default
    try:
        return Decimal(str(x))
    except Exception:
        return default


def distinct_reward_tokens(entries):
    """Distinct paying tokens. Fixes the mined_token / token key mismatch."""
    out = []
    for e in (entries or []):
        if not isinstance(e, dict):
            continue
        sym = e.get("mined_token") or e.get("token") or e.get("symbol")
        if sym and sym not in out:
            out.append(sym)
    return out


def reward_value_per_tick(entries, price_map):
    """USD value of ONE tick's distributions to the WHOLE pool.

    Returns a dict with per-token detail and, when prices are missing for some
    tokens, an explicit `comparable` False plus the unpriced list. Never folds
    an unpriced token into zero — that would understate the dividend and could
    flip a pool's verdict.
    """
    per_token, total, unpriced = {}, Decimal(0), []
    for e in (entries or []):
        if not isinstance(e, dict):
            continue
        sym = e.get("mined_token") or e.get("token") or e.get("symbol")
        qty = _dec(e.get("daily_reward"))
        if not sym or qty is None:
            continue
        price = _dec((price_map or {}).get(sym))
        if price is None:
            unpriced.append(sym)
            # 2026-10-08 FIX (multi-batch bug #3): a pool can pay the SAME
            # token through multiple concurrent batches (live case:
            # DVS:DHEDGE batches 179 and 329 both pay LGN). The old
            # `per_token[sym] = ...` kept only the last batch's figure,
            # dropping the other's value from the total. SUM per token.
            if sym in per_token and per_token[sym].get("quantity"):
                try:
                    prev = _dec(per_token[sym]["quantity"])
                    per_token[sym]["quantity"] = str(prev + qty)
                except Exception:
                    pass
            else:
                per_token[sym] = {"quantity": str(qty), "usd": None, "priced": False}
            continue
        usd = qty * price
        if sym in per_token and per_token[sym].get("priced"):
            # same token from another live batch: accumulate
            try:
                prev_qty = _dec(per_token[sym]["quantity"])
                per_token[sym]["quantity"] = str(prev_qty + qty)
                per_token[sym]["usd"] = float(_dec(str(per_token[sym]["usd"])) + usd)
            except Exception:
                pass
        elif sym in per_token and not per_token[sym].get("priced"):
            # previously unpriced (no USD), now priced: fill USD in
            per_token[sym]["quantity"] = str(_dec(per_token[sym]["quantity"]) + qty)
            per_token[sym]["usd"] = float(usd)
            per_token[sym]["priced"] = True
        else:
            per_token[sym] = {"quantity": str(qty), "usd": float(usd), "priced": True}
        total += usd
    # unpriced list: dedupe (multi-batch repeats would double-list)
    unpriced = sorted(set(unpriced))
    return {
        "usd_per_tick_whole_pool": float(total),
        "per_token": per_token,
        "distinct_tokens": list(per_token.keys()),
        "unpriced_tokens": unpriced,
        "comparable": not unpriced,
        "note": ("whole-pool per-tick value; our share needs pool total_shares. "
                 "numTicks is not necessarily 1 day."),
    }


def _pool_key(pool):
    """Accept either a pool id string or a PoolData-ish object.

    2026-10-02 BUG: reward_info is keyed by pool STRING ("CENT:POB") but this
    function was handed a pool OBJECT and did reward_info.get(pool), which
    silently missed, yielding entries=[] -> usd 0.0 with computable=True. A
    silent zero dividend is the worst possible failure here: it makes a
    dividend-bearing pool look barren and sends capital the wrong way.
    """
    if isinstance(pool, str):
        return pool
    for attr in ("pool_id", "tokenPair", "token_pair", "id", "pair"):
        v = getattr(pool, attr, None)
        if isinstance(v, str) and v:
            return v
    # Last resort: base:quote from the object.
    b, q = getattr(pool, "base", None), getattr(pool, "quote", None)
    if b and q:
        return f"{b}:{q}"
    return None


def our_dividend_usd_per_tick(reward_info, pool, position, price_map):
    """Our slice of a pool's distribution. Not-computable, never guessed."""
    key = _pool_key(pool)
    entries = (reward_info.get(key) or []) if key else []
    if key and not entries and key in (reward_info or {}):
        pass
    elif key and not entries:
        return {"computable": False,
                "reason": (f"no reward entries for pool {key} — treating as "
                           f"unknown rather than as a zero dividend"),
                "whole_pool": reward_value_per_tick([], price_map)}
    total_shares = getattr(pool, "total_shares", 0) or 0
    our_shares = getattr(position, "shares", 0) or 0
    base = reward_value_per_tick(entries, price_map)
    if not total_shares or total_shares <= 0:
        return {"computable": False,
                "reason": "pool.total_shares unknown — cannot compute our share",
                "whole_pool": base}
    if our_shares <= 0:
        return {"computable": False, "reason": "no position shares",
                "whole_pool": base}
    frac = Decimal(str(our_shares)) / Decimal(str(total_shares))
    if not base["comparable"]:
        return {"computable": False,
                "reason": ("no price for reward token(s): "
                           + ", ".join(base["unpriced_tokens"])),
                "whole_pool": base, "share_fraction": float(frac)}
    ours = Decimal(str(base["usd_per_tick_whole_pool"])) * frac
    return {"computable": True,
            "usd_per_tick": float(ours),
            "share_fraction": float(frac),
            "whole_pool": base}


def fee_run_rate_usd_per_tick(earned_usd, window_hours):
    """Linear per-tick fee accrual from a measured window. A proportionality."""
    e = _dec(earned_usd)
    w = _dec(window_hours)
    if e is None or w is None or w <= 0:
        return None
    return float(e / w)


def size_deposit(pool_id, budget_usd, price_map, fee_reserve_ratio=Decimal("0.5"),
                 min_usd=Decimal("0.000001")):
    """Split a USD budget into base/quote amounts for add_liquidity.

    fee_reserve_ratio is the share of the budget held back for the non-dominant
    side so the deposit does not skew the pool ratio.
    """
    pool_id = (pool_id or "").strip()
    if ":" not in pool_id:
        raise SizingError(f"pool id must be BASE:QUOTE, got {pool_id!r}")
    base, quote = [t.strip() for t in pool_id.split(":", 1)]
    if not base or not quote:
        raise SizingError(f"pool id {pool_id!r} has an empty side")

    # 2026-10-02 BUG: fee_reserve_ratio defaulted to a Decimal, so any caller
    # passing a plain float (e.g. 0.5) raised TypeError on Decimal - float.
    # Coerce both ratios defensively rather than assuming the caller's type.
    ratio = _dec(fee_reserve_ratio)
    if ratio is None:
        raise SizingError(f"fee_reserve_ratio {fee_reserve_ratio!r} is not numeric")
    if ratio < 0 or ratio > 1:
        raise SizingError(f"fee_reserve_ratio {ratio} must be between 0 and 1")
    fee_reserve_ratio = ratio

    min_amt = _dec(min_usd)
    if min_amt is None:
        min_amt = Decimal("0.000001")

    budget = _dec(budget_usd)
    if budget is None:
        raise SizingError(f"budget {budget_usd!r} is not numeric")
    if budget < min_amt:
        raise SizingError(
            f"budget ${budget} is below the ${min_amt} management floor — "
            f"the transaction would cost more than it manages")

    pb = _dec((price_map or {}).get(base))
    pq = _dec((price_map or {}).get(quote))
    missing = [s for s, p in ((base, pb), (quote, pq)) if p is None or p <= 0]
    if missing:
        raise SizingError(
            f"no validated USD price for {', '.join(missing)} — refusing to "
            f"guess a split (pool rates are exchange ratios, not USD)")

    # Split budget proportionally to each side's USD weight for a balanced pool.
    keep = Decimal("1") - fee_reserve_ratio
    deploy = budget * keep
    reserve = budget - deploy

    # A balanced deposit has equal USD value on both sides: x + y = deploy.
    # With y priced in USD we split deploy by price ratio.
    half = deploy / Decimal(2)
    amt_base = half / pb
    amt_quote = half / pq

    return {
        "pool": pool_id,
        "base": base, "quote": quote,
        "budget_usd": float(budget),
        "deploy_usd": float(deploy),
        "fee_reserve_usd": float(reserve),
        "fee_reserve_ratio": float(fee_reserve_ratio),
        "base_amount": float(amt_base),
        "quote_amount": float(amt_quote),
        "prices_used": {base: float(pb), quote: float(pq)},
        "split_is_balanced_usd": True,
        "notes": [
            "amounts are for add_liquidity as (base_amount, quote_amount)",
            "pool basePrice/quotePrice are exchange ratios and are NOT used here",
            "this is a sizing calculation, not an executable quote — a "
            "constant-product swap moves price between sizing and broadcast",
        ],
    }

def realisable_dividend(per_token: dict, size_map=None,
                        tight=0.10, wide=0.50):
    """Split a pool's dividend USD into REALISABLE and ILLIQUID portions.

    Tydyn 2026-10-02: "Healthy tokens have a narrow difference between buy and
    sell orders, while those that are not doing well tend to have very divergent
    buy and sell orders." A dividend paid in tokens you cannot sell is a GROSS
    figure, not income. This keeps the two apart so sizing can act on it.

    size_map maps symbol -> spread fraction (0.10 = 10%). A token absent from
    it is treated as UNKNOWN and reported separately rather than assumed
    sizeable — an absent liquidity reading is not evidence of a tight book.

    Returns a dict with gross, realisable, illiquid and unknown USD totals and
    the realisable fraction, plus the per-token breakdown that produced them.
    """
    gross = realisable = unknown = Decimal(0)
    illiquid = Decimal(0)
    detail = {}
    for sym, d in (per_token or {}).items():
        usd = _dec((d or {}).get("usd"))
        if usd is None:
            detail[sym] = {"usd": None, "class": "unpriced"}
            continue
        sp = _dec((size_map or {}).get(sym))
        if sp is None:
            cls = "unknown"
            unknown += usd
        elif sp <= tight:
            cls = "tight"
            realisable += usd
        elif sp <= wide:
            cls = "wide"
            realisable += usd
        else:
            cls = "illiquid"
            illiquid += usd
        gross += usd
        detail[sym] = {"usd": float(usd), "class": cls,
                       "spread": float(sp) if sp is not None else None}
    # None means "no basis to judge", which is different from 0.0 ("we know
    # none of it is sellable"). A pool paying entirely tokens whose spread we
    # have not read must not report 0% realisable as if that were measured.
    if gross and gross > 0:
        frac = (float(realisable / gross) if (realisable > 0 or illiquid > 0)
                else None)
    else:
        frac = None
    return {
        "gross_usd": float(gross),
        "realisable_usd": float(realisable),
        "illiquid_usd": float(illiquid),
        "unknown_usd": float(unknown),
        "realisable_fraction": frac,
        "per_token": detail,
        "note": ("gross dividend includes tokens whose bid/ask spread says they "
                 "cannot be sold; only realisable_usd is income we could keep"),
    }


MIN_REALISABLE_DIVIDEND_FRACTION = 0.5   # at least half the dividend must be sellable


def dividend_is_income(pool, reward_info, price_map, size_map,
                       min_fraction=MIN_REALISABLE_DIVIDEND_FRACTION):
    """Whether a pool's dividend counts as income for sizing purposes."""
    entries = reward_info.get(_pool_key_for(pool) or pool) or []
    val = reward_value_per_tick(entries, price_map)
    if not val["comparable"]:
        return {"counts_as_income": False,
                "realisable_fraction": None,
                "min_fraction": min_fraction,
                "reason": ("dividend not comparable — unpriced token(s): "
                           + ", ".join(val["unpriced_tokens"][:4])),
                "whole_pool": val,
                "split": None}
    split = realisable_dividend(val["per_token"], size_map)
    frac = split["realisable_fraction"]
    ok = (frac is not None and frac >= min_fraction)
    return {"counts_as_income": ok,
            "realisable_fraction": frac,
            "min_fraction": min_fraction,
            "reason": (f"only {frac:.1%} of the dividend is sellable; "
                       f"{split['illiquid_usd']:.6f} USD/tick is paid in "
                       f"illiquid tokens" if not ok and frac is not None
                       else "dividend is sellable"),
            "split": split}


def _pool_key_for(pool):
    if isinstance(pool, str):
        return pool
    for a in ("pool_id", "tokenPair", "base", "id"):
        v = getattr(pool, a, None)
        if isinstance(v, str) and v:
            return v
    b, q = getattr(pool, "base", None), getattr(pool, "quote", None)
    return f"{b}:{q}" if b and q else None
