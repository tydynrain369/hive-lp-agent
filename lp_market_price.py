"""Market-price fallback for tokens that have no liquidity pool.

WHY THIS EXISTS
  2026-10-02: PriceEngine derives every token price from POOL RESERVES
  (base_reserve x price_base + ...). That method cannot price a token that has a
  real market order book but no pool at all.

  Ten reward tokens were in exactly that state — BBHO, FRESA, HOP, IDD, WRD,
  POKEMON, BEATCZ, PEPECASH, SWAP.PEPE, SWAP.PEPET — and so read as unpriced,
  which made ALIVE:HEARTBEAT and ALIVE:SWAP.HIVE look dividend-free when their
  distributions are in fact priced.

  Tydyn's correction: "there are markets for all layer-2 Hive-Engine tokens,
  regardless of whether there are any buy or sell orders, but not all tokens
  have pools." Exactly right.

THE DATA
  market.metrics rows put the price at the TOP LEVEL:
      {"_id":1, "symbol":"BEE", "volume":"2387.57", "lastPrice":"0.5547355"}
  NOT under a nested "metrics" key. Reading the nested path returns None for
  every token and looks like "no market" — that mistake was made and reported
  as fact before this module existed.

HONEST LIMITS — REVISED 2026-10-02 AFTER TYDDYN'S SPREAD INSIGHT
  - lastPrice alone is a STALE PRINT and is the wrong price source. Tydyn:
    "Pool prices are usually derived from the highest buy order, and lowest
    sell order, and are usually right between them. Healthy tokens have a
    narrow difference between buy and sell orders, while those that are not
    doing well tend to have very divergent buy and sell orders."
    So: price from the BID when one exists (that is what you can realise on a
    sale), and carry the SPREAD as a liquidity measure.
  - A missing bid is not a neutral state. FRESA has an ask of 0.08 and NO bid,
    so it can be bought but not sold — its lastPrice is a historical print, not
    an exit price.
  - A ~200% spread means the book is an artefact of token-fraction bids against
    near-unit asks, i.e. no real market. lastPrice there is noise.
  - The spread therefore gates SIZING, not just reporting: a wide spread or an
    absent bid must stop capital from being sized on the assumption of exit.
  - A price of exactly "0" is treated as no price, not as free.
  - This is read-only and caches per process.
"""
import json
from typing import Dict, Optional

HE_RPC = "https://api.hive-engine.com/rpc/contracts"


def _find(symbol: str):
    """Find a market.metrics row for one symbol.

    NOTE the params shape: this node takes the query object DIRECTLY, not
    wrapped in a list. Passing [{"contract": ...}] returns zero rows silently —
    no error, just an empty result, which is indistinguishable from "no market".
    liquidity_agent_v2.he() has the correct shape and is the reference.
    """
    import urllib.request
    body = json.dumps({"jsonrpc": "2.0", "method": "find", "id": 1,
                       "params": {"contract": "market", "table": "metrics",
                                  "query": {"symbol": symbol}, "limit": 1}}).encode()
    req = urllib.request.Request(HE_RPC, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    if "error" in resp:
        raise RuntimeError(f"RPC error: {resp['error']}")
    return resp.get("result") or []


# Spread bands (Tydyn 2026-10-02). These gate whether capital may be SIZED on
# an assumed exit, not merely whether a price can be quoted.
SPREAD_HEALTHY = 0.10        # <=10%  : priceable and sizeable
SPREAD_WIDE = 0.50           # 10-50% : priceable, size down
# >50%, or no bid at all     : illiquid — value it, but do not size on it

# Exit-liquidity floor (2026-10-07, PsyopDaily-verified). The spread says what
# a sale realises; the DEPTH says whether a sale of any size exists at all.
# A tight spread on a near-empty book is still a trap, so expansion requires
# total exit liquidity (order-book bid depth + Diesel-Pool depth) above this
# floor in BOTH pair tokens. Math verified against the site's own published
# figures: WIT bid depth = sum(price x quantity over buyBook rows) = 0.2432
# HIVE, matching their safety_reasons text exactly.
EXIT_LIQUIDITY_FLOOR_HIVE = 0.5


def bid_depth_hive(symbol: str, limit: int = 1000):
    """Total HIVE value of a token's order-book bids: sum(price*qty over buyBook).

    None on transport/parse failure — the caller must treat that as UNKNOWN,
    never zero (a failed probe is not evidence of an empty book).
    """
    import urllib.request
    try:
        body = json.dumps({"jsonrpc": "2.0", "method": "find", "id": 1,
                           "params": {"contract": "market", "table": "buyBook",
                                      "query": {"symbol": symbol}, "limit": limit}}).encode()
        req = urllib.request.Request(HE_RPC, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        if "error" in resp:
            return None
        rows = resp.get("result") or []
        return sum(float(r.get("price", 0)) * float(r.get("quantity", 0))
                   for r in rows if isinstance(r, dict))
    except Exception:
        return None


def _f(r, key):
    try:
        v = float(r.get(key))
        return v
    except (TypeError, ValueError):
        return None


# 2026-10-17 UNITS FIX: every HE market figure is SWAP.HIVE-DENOMINATED
# (all order books quote in SWAP.HIVE), but consumers downstream priced in
# USD. The result was a silent 1/HIVE-price inflation (~19x at $0.0524):
# the deploy sizing quoted CENT at "$0.0011" when the executable truth was
# 0.0011 HIVE = $0.0000577. The book and the pool AGREED all along; the
# consumer was unit-blind. market_price() now returns USD in "price" and
# keeps the raw book figure as "price_hive". If the HIVE anchor cannot be
# fetched, NO USD price is returned (price=None, price_hive set) — never
# fabricate a USD figure from a HIVE-denominated print.
_HIVE_ANCHOR_CACHE = {"value": None, "fetched": False}


def _hive_anchor_usd():
    """HIVE/USD from the SWAP.HIVE:SWAP.HBD pool (quote/base), cached per
    process. None on failure — callers must treat that as unknown."""
    if _HIVE_ANCHOR_CACHE["fetched"]:
        return _HIVE_ANCHOR_CACHE["value"]
    _HIVE_ANCHOR_CACHE["fetched"] = True
    try:
        import urllib.request
        body = json.dumps({"jsonrpc": "2.0", "method": "find", "id": 1,
                           "params": {"contract": "marketpools",
                                      "table": "pools",
                                      "query": {"tokenPair": "SWAP.HIVE:SWAP.HBD"},
                                      "limit": 1}}).encode()
        req = urllib.request.Request(HE_RPC, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        rows = resp.get("result") or []
        if rows:
            b = float(rows[0].get("baseQuantity") or 0)
            q = float(rows[0].get("quoteQuantity") or 0)
            if b > 0 and q > 0:
                _HIVE_ANCHOR_CACHE["value"] = q / b
    except Exception:
        _HIVE_ANCHOR_CACHE["value"] = None
    return _HIVE_ANCHOR_CACHE["value"]


def market_price(symbol: str) -> Optional[dict]:
    """Executable USD price plus a liquidity verdict for a symbol.

    Price basis is the HIGHEST BID when one exists, because that is what a
    sale would realise; lastPrice is the fallback and is flagged as a stale
    print. ALL book figures are SWAP.HIVE-denominated: "price" is the USD
    conversion via the live HIVE anchor; "price_hive" is the raw book figure.
    The bid/ask spread is returned as a liquidity measure (a ratio — units do
    not affect it).
    """
    if not symbol:
        return None
    try:
        rows = _find(symbol)
    except Exception:
        return None
    if not rows:
        return None
    r = rows[0]
    # NOTE: top level, NOT r["metrics"]["lastPrice"].
    lp_f = _f(r, "lastPrice")
    if lp_f is None or lp_f <= 0:
        return None
    vol_f = _f(r, "volume") or 0.0
    bid = _f(r, "highestBid")
    ask = _f(r, "lowestAsk")

    has_bid = bid is not None and bid > 0
    has_ask = ask is not None and ask > 0
    spread = None
    if has_bid and has_ask and bid > 0:
        mid = (bid + ask) / 2.0
        if mid > 0:
            spread = (ask - bid) / mid

    # A sale realises the bid, so price on the bid when one exists.
    if has_bid:
        price, basis = bid, "highestBid (exit price for a sale)"
    else:
        price, basis = lp_f, "lastPrice (NO BID — stale print, not an exit price)"

    if not has_bid:
        liquidity = "no-bid"
    elif spread is None:
        liquidity = "unknown"
    elif spread <= SPREAD_HEALTHY:
        liquidity = "tight"
    elif spread <= SPREAD_WIDE:
        liquidity = "wide"
    else:
        liquidity = "illiquid"

    anchor = _hive_anchor_usd()
    price_usd = round(price * anchor, 12) if (anchor is not None and price) else None
    return {"symbol": symbol,
            "price": price_usd,
            "price_hive": price,
            "hive_anchor": anchor,
            "units": "USD" if price_usd is not None else "HIVE (anchor unavailable)",
            "lastPrice": lp_f,
            "highest_bid": bid,
            "lowest_ask": ask,
            "spread_pct": (round(spread * 100, 2) if spread is not None else None),
            "volume": vol_f,
            "liquidity": liquidity,
            "sizeable": liquidity in ("tight", "wide"),
            "source": "market.metrics",
            "basis": basis}


def market_prices(symbols) -> Dict[str, dict]:
    out = {}
    for s in symbols or []:
        m = market_price(s)
        if m:
            out[s] = m
    return out


def enrich_price_map(price_map: dict, symbols=None, overwrite_zero_only: bool = True):
    """Return a copy of price_map with pool-less tokens filled from the market.

    IMPORTANT: when `symbols` is None this iterates ONLY the keys already in
    price_map. A token with no liquidity pool never became a key there at all,
    so the fallback found nothing to fill and silently did nothing. Callers MUST
    pass the candidate list — the reward tokens actually observed, plus anything
    held in the wallet — not just the existing map.

    overwrite_zero_only leaves existing non-zero pool-derived prices alone: the
    pool method is reserve-based and remains the better source where it works.
    """
    result = dict(price_map or {})
    added, skipped = {}, []
    for s in (symbols if symbols is not None else list(result.keys())):
        if overwrite_zero_only and result.get(s):
            continue
        m = market_price(s)
        if m:
            result[s] = m["price"]
            added[s] = m
        else:
            skipped.append(s)
    return result, added, skipped