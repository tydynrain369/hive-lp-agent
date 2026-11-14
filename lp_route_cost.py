#!/usr/bin/env python3
"""Price a multi-hop swap route through each pool's own reserves.

2026-10-04. Tydyn raised the point this exists for: a route being REACHABLE says
nothing about whether crossing it is worth doing. PIXYDUST -> HSBIDAO -> LBI ->
CENT is three hops, and every hop is a constant-product swap that charges a fee
and moves price. At our position sizes that drag can exceed the benefit of the
reallocation it enables.

The router already proves reachability (lp_router). This module answers the next
question: what does it COST, and what arrives at the far end.

Model
-----
Constant product, x*y=k. Swapping dx of the input token into a pool holding
(x=base, y=quote) yields:

    dy = y * (1 - 1/(1 + dx*fee_factor/x))          [fee applied to input]
    k' = (x + dx) * (y - dy)

The realised rate therefore degrades as dx approaches the pool's reserves, which
is exactly the price impact we must not ignore: quoting the spot rate and calling
it an executable price is the reserve-ratio mistake in a new place.

Every intermediate amount is carried forward EXACTLY. Rounding each hop to a
display precision would compound quietly and is precisely how a 3-hop route
ends up materially short of its own quote.

No floats: Fraction arithmetic throughout, so a dust-sized amount stays exact.
"""
from __future__ import annotations

from fractions import Fraction

# Hive-Engine swap fee. Confirmed against deployed contract behaviour; the
# contract applies maxDeviation on the add side, and swapFee on this side.
# Named and documented because a fee nobody can name is one nobody can tune.
# 0.25% — the deployed contract sets tradeFeeMul=0.9975
# (references/deployed-marketpools.json, verified against the contract source;
# Opus review N3, round 2). The old 0.1% understated route cost 2.5x, which
# let a 5-hop route model 0.5% of fees where the chain takes ~1.25%.
SWAP_FEE = Fraction(25, 10000)
DEFAULT_MAX_HOPS = 5


class Pool:
    """Minimal reserve view of one pool, sufficient to price a swap through it."""

    __slots__ = ("pair", "base", "quote", "x", "y")

    def __init__(self, pair, base_qty, quote_qty):
        self.pair = pair
        self.base, self.quote = pair.split(":")[:2]
        self.x = Fraction(str(base_qty))
        self.y = Fraction(str(quote_qty))

    def swap(self, dx_in, fee=SWAP_FEE):
        """Swap dx_in of the BASE token in, returning the quote token out."""
        if dx_in <= 0:
            return Fraction(0)
        # Input after fee is what actually crosses the curve.
        effective = dx_in * (1 - fee)
        if effective >= self.x:
            return None  # would exhaust the pool; caller decides what to do
        return self.y * effective / (self.x + effective)


def _pool_map(pools):
    """Accept the price engine's pools, a dict, or an iterable of rows."""
    out = {}
    if isinstance(pools, dict):
        for k, v in pools.items():
            # PoolData spells these base_quantity / quote_quantity; the price
            # engine's own dict rows use baseQuantity / quoteQuantity. Read both
            # rather than assuming one shape, because getting it wrong returns 0
            # and silently prices every hop as free.
            bq = getattr(v, "base_quantity", None)
            qq = getattr(v, "quote_quantity", None)
            if bq is None:
                bq = getattr(v, "base_qty", None)
            if qq is None:
                qq = getattr(v, "quote_qty", None)
            if bq is None and isinstance(v, dict):
                bq = v.get("baseQuantity") or v.get("base_qty") or v.get("base_quantity")
                qq = v.get("quoteQuantity") or v.get("quote_qty") or v.get("quote_quantity")
            if bq is not None and qq is not None:
                out[k] = Pool(k, bq, qq)
        return out
    for row in pools or []:
        pair = row.get("tokenPair") or row.get("token_pair")
        if pair:
            out[pair] = Pool(pair, row.get("baseQuantity"), row.get("quoteQuantity"))
    return out


def price_route(path, amount_in, token_in, pools, fee=SWAP_FEE):
    """Walk `path` from token_in, returning per-hop detail and the final amount.

    Returns a dict with:
      ok            - False if any hop is impossible
      hops          - one entry per pool crossed
      out_token     - the token arriving at the end
      amount_out    - amount of out_token received (Fraction)
      spot_rate     - out_amount / in_amount at spot, for comparison
      realised_rate - out_amount / in_amount actually achieved
      cost_pct      - percentage lost to fees and impact vs spot
      failure       - why it failed, when ok is False
    """
    pm = _pool_map(pools)
    amount = Fraction(str(amount_in))
    start = amount
    hops = []
    cur = token_in

    for a, b in zip(path, path[1:]):
        # find a pool holding this exact pair, in either orientation
        pair = f"{a}:{b}"
        reverse = f"{b}:{a}"
        if pair in pm:
            pool, selling_base = pm[pair], True
        elif reverse in pm:
            pool, selling_base = pm[reverse], False
        else:
            return {"ok": False, "path": list(path), "token_in": token_in,
                    "amount_in": str(start), "hops": hops,
                    "failure": f"no pool for hop {a}->{b}"}

        # ORIENTATION (2026-10-04, found by independent hand derivation):
        # selling_base is True when we sell the pool's BASE token, so the INPUT
        # reserve is pool.x. It is False when we sell the QUOTE token, and then
        # the input reserve is pool.Y, not pool.x.
        #
        # Verified against HSBIDAO:PIXYDUST (base=HSBIDAO 5238.43723197,
        # quote=PIXYDUST 5336.74501647) selling 0.00402399 PIXYDUST:
        #   quote-side (correct)   0.004095404060
        #   base-side  (buggy)     0.003945911542
        # The earlier version mirrored the pool and returned the base-side
        # figure, so every PIXYDUST route was priced against the wrong reserves.
        # That understates the quote leg and overstates the cost.
        if selling_base:
            dy = pool.swap(amount, fee)
            reserve_in, reserve_out = pool.x, pool.y
        else:
            # Mirror so the token we are SELLING is always the "base" (input).
            mirrored = Pool(pool.pair, pool.y, pool.x)
            dy = mirrored.swap(amount, fee)
            reserve_in, reserve_out = pool.y, pool.x
        if dy is None:
            return {"ok": False, "path": list(path), "token_in": token_in,
                    "amount_in": str(start), "hops": hops,
                    "failure": f"hop {a}->{b} cannot absorb {float(amount):.8g}"}

        hops.append({"pool": pool.pair, "sold": a, "bought": b,
                     "amount_in": str(amount), "amount_out": str(dy),
                     "reserve_in": str(reserve_in),
                     "reserve_out": str(reserve_out),
                     "sold_side": "base" if selling_base else "quote"})
        amount = dy
        cur = b

    spot = _spot_rate(path, token_in, pm)
    realised = (amount / start) if start else Fraction(0)
    return {
        "ok": True,
        "path": list(path),
        "token_in": token_in,
        "amount_in": str(start),
        "out_token": cur,
        "amount_out": str(amount),
        "hops": hops,
        "spot_rate": str(spot) if spot else None,
        "realised_rate": str(realised),
        "cost_pct": float((1 - realised / spot) * 100) if spot and spot else None,
        "failure": None,
    }


def _spot_rate(path, token_in, pm):
    """Product of spot rates along the route, ignoring fees and impact."""
    rate = Fraction(1)
    for a, b in zip(path, path[1:]):
        pair, reverse = f"{a}:{b}", f"{b}:{a}"
        if pair in pm:
            p = pm[pair]
            rate *= p.y / p.x
        elif reverse in pm:
            p = pm[reverse]
            rate *= p.x / p.y
        else:
            return None
    return rate
