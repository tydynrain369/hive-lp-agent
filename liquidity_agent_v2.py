#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""
Hive Liquidity Agent v2 — Autonomous DeFi agent for @hermes-sandbox
Full price discovery, pool health scoring, autonomous execution.
"""
import json
import os
import time
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# ─── Config ──────────────────────────────────────────────────────────
HIVE_RPC = "https://api.deathwing.me"
HE_RPC = "https://api.hive-engine.com/rpc/contracts"
# 2026-10-22: the account NAME for public reads. It is already public in
# this codebase (docstring, load_balances query) and in our own artifacts
# (fee_snapshots rows); only the KEYS are secret and they stay in the vault.
# Public reads must never open the vault (pinned: test_position_valuation's
# open-forbidden patch). Env-overridable for the multi-account future.
ACCOUNT = os.environ.get("LP_ACCOUNT", "hermes-sandbox")

VAULT_PATH = "/mnt/vault/Hermes-Keys/hermes-sandbox.json"
SCAN_INTERVAL = 300  # 5 min

# Target pools we want to actively manage (all relevant token pairs for our wallet)
TARGET_POOLS = [
    "HSBIDAO:PIXYDUST",
    "ALIVE:HEARTBEAT",
    "SWAP.HIVE:ALIVE",
    "SWAP.HIVE:BBH",
    "SWAP.HIVE:PHOTO",
    "SWAP.HIVE:PIZZA",
    "SWAP.HIVE:CENT",
]

# Zombie pools - zero fees, zero/low rewards, should be exited
ZOMBIE_POOLS = {"SWAP.HIVE:BEE", "SWAP.HIVE:POB", "POB:SWAP.HIVE", "BEE:SWAP.HIVE",
                "ATF:SWAP.HIVE", "SWAP.HIVE:ATF"}

# Tokens we want to track for unstaking schedule
UNSTAKING_SCHEDULE = {
    "PIZZA": 28,
    "PHOTO": 91,
    "HEARTBEAT": 3,
    "CENT": 28,
    "BEE": 40,
}

# Token importance weights for reallocation decisions
# Tier 1=10 (Protocol/Infrastructure), Tier 2=8 (Tribe Core), Tier 3=5 (Allied), Tier 4=2, Tier 5=1
TOKEN_IMPORTANCE = {
    # Tier 1: Protocol/Infrastructure - Always useful
    "BEE": 10, "WORKERBEE": 10,
    # Tier 2: Tribe Core - Our pools, we control contracts
    "ALIVE": 10, "ALIVEM": 10, "HEARTBEAT": 10,
    # Tier 3: Allied Ecosystems - Supported projects
    "LBI": 8, "PIXYDUST": 8, "UNICOIN": 8, "GLITTER": 8,
    "CENT": 8, "CENTG": 8, "TRADE": 8, "TRIBE": 8, "SHORT": 8,
    "BBH": 8, "BBHM": 8, "BBHO": 8,
    "PEPE": 8, "PPDLR": 8, "PPBT": 8,
    "PIMP": 8, "AEGIS": 8,
    # ATF REMOVED - project ended
    # Tier 4: Other Hive Engine tokens
    "HSBIDAO": 5, "POB": 5, "SWAP.HIVE": 5,
    "PHOTO": 5, "PIZZA": 5, "BBH": 5,
}

# Dead/ended projects - should not be considered for new liquidity
DEAD_PROJECTS = {"ATF"}  # ATF ended

# Reallocation target pools with tier and notes
REALLOC_TARGETS = {
    "SWAP.HIVE:CENT": {"tier": 1, "notes": "CENT mining rewards (1000 CENT/hr = 24K/day) + daily LP rewards", "reward_tokens": ["CENT"], "compound_accel": True, "daily_reward_usd": 0},
    "ALIVE:SWAP.HIVE": {"tier": 2, "notes": "Core tribe liquidity, rewards: ALIVE, BBH, FRESA, PEPENTROPY, TOOFUCKEH, PEPE, GAMER, MEME, BBHO, etc. (~$0.13/day)", "reward_tokens": ["ALIVE", "BBH", "BBHO", "FRESA", "PEPENTROPY", "TOOFUCKEH", "PEPE", "GAMER", "MEME", "IDD", "HOP", "WRD", "BEER", "PEPECASH", "SWAP.PEPET", "SWAP.PEPE", "HUESO", "LOLZ", "ALIVEM", "POKEMON"], "compound_accel": False, "daily_reward_usd": 0.13},
    "ALIVE:HEARTBEAT": {"tier": 2, "notes": "Fees crossed + allied tokens (BBH, PIXYDUST)", "reward_tokens": ["BBH", "PIXYDUST"], "compound_accel": True, "daily_reward_usd": 0},
    "ALIVE:ALIVEM": {"tier": 2, "notes": "Governance stack (PEPE rewards)", "reward_tokens": ["PEPE"], "compound_accel": True, "daily_reward_usd": 0},
    "ALIVE:LBI": {"tier": 2, "notes": "Newest ally pool (rewards to be discovered)", "reward_tokens": [], "compound_accel": True, "daily_reward_usd": 0},
    "HSBIDAO:PIXYDUST": {"tier": 3, "notes": "1.35K/day rewards, PIXYDUST ecosystem", "reward_tokens": ["PIXYDUST"], "compound_accel": True, "daily_reward_usd": 1350},
    "CENT:POB": {"tier": 2, "notes": "Dual yield (fees + rewards), CENT ecosystem", "reward_tokens": ["CENT"], "compound_accel": True, "daily_reward_usd": 0},
}

# Our pool fees data (from Tribaldex - manual entry)
OUR_POOL_FEES = {
    "CENT:POB": {
        "token_a": "CENT", "token_a_fee": 0.00929195,
        "token_b": "POB", "token_b_fee": 0.00127011,
        "threshold_crossed": True
    },
    "SWAP.HIVE:POB": {
        "token_a": "SWAP.HIVE", "token_a_fee": 0.0,
        "token_b": "POB", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
    "SWAP.HIVE:BEE": {
        "token_a": "SWAP.HIVE", "token_a_fee": 0.0,
        "token_b": "BEE", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
    "ALIVE:HEARTBEAT": {
        "token_a": "ALIVE", "token_a_fee": 0.00000113,
        "token_b": "HEARTBEAT", "token_b_fee": 0.00003545,
        "threshold_crossed": True
    },
    "SWAP.HIVE:ALIVE": {
        "token_a": "SWAP.HIVE", "token_a_fee": 0.0,
        "token_b": "ALIVE", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
    "HSBIDAO:PIXYDUST": {
        "token_a": "HSBIDAO", "token_a_fee": 0.0,
        "token_b": "PIXYDUST", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
}

# ─── RPC Helper ──────────────────────────────────────────────────────
def he(method: str, params: dict) -> list:
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    req = Request(HE_RPC, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=30) as r:
        resp = json.load(r)
    if "error" in resp:
        raise RuntimeError(f"RPC error: {resp['error']}")
    return resp.get("result", [])

# ─── Data Classes ────────────────────────────────────────────────────
@dataclass
class TokenBalance:
    symbol: str
    balance: float = 0.0
    staked: float = 0.0
    price_usd: float = 0.0
    # 2026-10-22 spec restore: value_usd is the LIQUID value only
    # (balance x price); staked value is reported separately — folding
    # stake into value_usd overstated deployable capital (pinned:
    # test_liquid_excludes_stake).
    value_usd: float = 0.0
    staked_value_usd: float = 0.0

@dataclass
class LPPosition:
    pool_id: str
    base: str
    quote: str
    shares: float
    base_deposited: float
    quote_deposited: float
    # Optional[float]: None = value UNKNOWN (missing/invalid USD price) —
    # never silently 0 (pinned: test_missing_price_is_unknown).
    value_usd: Optional[float] = None
    apr: float = 0.0
    # 2026-10-07: chain timeFactor (ms) of our liquidity-position row — the
    # timestamp the position was last touched (deposit/top-up rebase it).
    # The distribution contract's age bonus is computed from (now - timeFactor),
    # so the decision layer needs it to score bonus dilution on top-ups.
    time_factor_ms: float = 0.0

@dataclass
class PoolData:
    pool_id: str
    base: str
    quote: str
    base_reserve: float
    quote_reserve: float
    base_price: float
    quote_price: float
    volume_24h: float
    fees_24h: float
    apr: float
    liquidity_usd: float
    total_shares: float = 0.0
    base_quantity: float = 0.0
    quote_quantity: float = 0.0
    # 2026-10-17: raw 24h volumes in each side's own token units (contract
    # fields baseVolume/quoteVolume). Kept raw so the USD pass can convert
    # honestly once prices exist (volume_24h itself is recomputed there).
    base_volume: float = 0.0
    quote_volume: float = 0.0

    @property
    def fee_observation_status(self) -> str:
        """2026-10-22 spec restore (pinned: test_unknown_fee_has_explicit_status).
        Pool-level data carries NO longitudinal fee observation — that
        evidence is account-scoped in the fee snapshot store. 'unknown' is
        the honest answer at this layer; a missing status must never read
        as 'measured'."""
        return "unknown"

# ─── Price Engine ───────────────────────────────────────────────────
class PriceEngine:
    def __init__(self):
        self.pools: Dict[str, PoolData] = {}
        # Symbols to attempt market pricing for even though they appear in no
        # pool: reward tokens from active distributions and wallet holdings.
        # Populated by the caller BEFORE load_all(); a pool-less token cannot
        # be discovered from pool ids, so without this it is invisible.
        self.extra_price_candidates: set = set()
        # What the caller ASKED us to price. Kept so a partial fill can be
        # named explicitly instead of being hidden behind a success-shaped count.
        self.requested_price_candidates: set = set()
        # Pool ids the portfolio currently holds — the ONLY pools whose
        # pair/reward tokens are worth pricing.
        self.held_pools: set = set()
        self.token_prices: Dict[str, float] = {}
        self.token_precisions: Dict[str, int] = {}  # symbol -> precision
        # 2026-10-02: liquidity verdicts for tokens priced from the market
        # order book rather than from pool reserves. Declared here so a caller
        # can read it even when load_all() has not run or market pricing failed.
        self.market_liquidity: Dict[str, dict] = {}
        self._loaded = False

    def _load_token_precisions(self):
        """Load token precision from Hive Engine tokens table."""
        tokens = [
            "ALIVE", "HEARTBEAT", "BBH", "POB", "CENT", "PHOTO", "PIZZA", 
            "BEE", "HSBIDAO", "PIXYDUST", "UNICOIN", "SWAP.HIVE", "HIVE",
            "PSX", "DEC", "WEED"
        ]
        for sym in tokens:
            try:
                raw = he("find", {
                    "contract": "tokens", "table": "tokens",
                    "query": {"symbol": sym}, "limit": 1
                })
                if raw:
                    self.token_precisions[sym] = raw[0].get("precision", 8)
            except Exception:
                pass
        # Defaults for tokens not in the list
        defaults = {
            "SWAP.HIVE": 8, "HIVE": 3, "HBD": 3,
            "BEE": 8, "POB": 8, "CENT": 8, "PIXYDUST": 8,
            "ALIVE": 4, "HEARTBEAT": 8, "BBH": 2, 
            "PHOTO": 2, "PIZZA": 2, "HSBIDAO": 3,
            "UNICOIN": 8, "PSX": 8, "DEC": 8, "WEED": 8
        }
        for sym, prec in defaults.items():
            self.token_precisions.setdefault(sym, prec)

    def round_amount(self, symbol: str, amount: float) -> float:
        """Floor-round to the token's precision.

        2026-10-22 spec restore (pinned: test_floor_never_overspends,
        test_unknown_precision_not_invented): a deploy sized by rounding UP
        can exceed the wallet balance — floor, never round-half. And an
        unknown symbol REFUSES rather than inventing precision 8: an
        unpriced token yields a refusal that NAMES the token, never a
        fabricated quantity.
        """
        prec = self.token_precisions.get(symbol)
        if prec is None:
            raise ValueError(
                f"unknown precision for {symbol!r}: refusing to size "
                f"capital against an invented precision")
        import math
        return math.floor(amount * 10 ** prec) / 10 ** prec

    def _get_live_hive_price(self) -> float:
        """Query live HIVE price from witness feed (HIVE:HBD ratio)."""
        try:
            from nectar import Hive
            h = Hive()
            hf = h.get_feed_history()
            current = hf.get('current_median_history', {})
            base = current.get('base', {})
            quote = current.get('quote', {})
            base_amt = int(base.get('amount', 0)) / (10 ** base.get('precision', 3))
            quote_amt = int(quote.get('amount', 0)) / (10 ** quote.get('precision', 3))
            # HIVE price = HBD_amount / HIVE_amount
            hive_price = quote_amt / base_amt if base_amt > 0 else 0.05
            return max(hive_price, 0.01)  # sanity floor
        except Exception:
            return 0.05  # fallback

    def load_all(self):
        """Load all pools and compute prices from reserves, anchored to SWAP.HIVE."""
        if self._loaded:
            return
        
        # Load token precisions first
        self._load_token_precisions()
        
        raw = he("find", {
            "contract": "marketpools", "table": "pools", "query": {}, "limit": 1000
        })
        
        # First pass: build pool map
        for p in (raw or []):
            try:
                token_pair = p.get("tokenPair", "")
                if ":" not in token_pair:
                    continue
                base, quote = token_pair.split(":", 1)
                
                base_reserve = float(p.get("baseQuantity", 0))
                quote_reserve = float(p.get("quoteQuantity", 0))
                if base_reserve == 0 or quote_reserve == 0:
                    continue
                
                pool_id = f"{base}:{quote}"
                # basePrice is quote-per-base (e.g., SWAP.HIVE per CENT)
                # quotePrice is base-per-quote (e.g., CENT per SWAP.HIVE)
                base_price_in_quote = float(p.get("basePrice", quote_reserve / base_reserve))
                quote_price_in_base = float(p.get("quotePrice", base_reserve / quote_reserve))
                
                volume = float(p.get("baseVolume", 0)) * base_price_in_quote + float(p.get("quoteVolume", 0)) * quote_price_in_base
                fees = volume * 0.003
                
                apr = 0.0
                if volume > 0:
                    liquidity_usd = base_reserve * base_price_in_quote + quote_reserve * quote_price_in_base
                    if liquidity_usd > 0:
                        apr = (fees / liquidity_usd) * 365 * 100
                
                self.pools[pool_id] = PoolData(
                    pool_id=pool_id, base=base, quote=quote,
                    base_reserve=base_reserve, quote_reserve=quote_reserve,
                    base_price=base_price_in_quote, quote_price=quote_price_in_base,
                    volume_24h=volume, fees_24h=fees, apr=apr,
                    liquidity_usd=base_reserve * base_price_in_quote + quote_reserve * quote_price_in_base,
                    total_shares=float(p.get("totalShares", 0)),
                    base_quantity=base_reserve,
                    quote_quantity=quote_reserve,
                    base_volume=float(p.get("baseVolume", 0)),
                    quote_volume=float(p.get("quoteVolume", 0))
                )
            except Exception:
                continue
        
        # ===== Build USD price map via BFS from anchor tokens =====
        # ANCHOR: Use the SWAP.HIVE:SWAP.HBD pool as the live price oracle
        # This gives us the market-derived HIVE price, not the internal witness rate
        swap_hive_hbd = self.pools.get("SWAP.HIVE:SWAP.HBD")
        if swap_hive_hbd and swap_hive_hbd.base_price > 0:
            hive_usd = swap_hive_hbd.base_price * 1.0  # SWAP.HBD ≈ $1.00
        else:
            hive_usd = 0.05  # fallback
        print(f"  Price anchor: HIVE = ${hive_usd:.6f} (from SWAP.HIVE:SWAP.HBD pool)")
        
        anchors = {
            "SWAP.HIVE": hive_usd,
            "HIVE": hive_usd,
            "SWAP.HBD": 1.0,
            "HBD": 1.0,
            "SWAP.BTC": 65000.0,
            "SWAP.ETH": 3500.0,
            "SWAP.LTC": 100.0,
            "SWAP.DOGE": 0.15,
        }
        # Initialize with anchors
        for tok, price in anchors.items():
            if tok not in self.token_prices or self.token_prices[tok] == 0:
                self.token_prices[tok] = price
        
        # ===== Derive token prices from the deepest-liquidity pool vs an already-priced token =====
        # Strategy: for each unknown token, find ALL pools containing it where the OTHER side
        # is already priced. Compute the token's USD price from reserve ratios. Take the
        # liquidity-weighted median across all such pools to resist outliers/junk pools.
        #
        # For a pool (A, B) with reserves (rA, rB):
        #   A's implied price = (rB / rA) * price(B)   [if B is known]
        #   B's implied price = (rA / rB) * price(A)   [if A is known]
        
        def _derive_prices():
            changed_any = False
            # 2026-10-22 determinism restore (pinned:
            # test_order_independent_deeper_known_side): the previous
            # last-write-wins loop was silently iteration-order dependent —
            # a token priced from two pools got whichever pool the dict
            # reached last, contradicting the deepest-liquidity comment.
            # Collect ALL candidates for this round, then commit the
            # deepest-known-side figure per symbol: order-independent and
            # matching the documented intent.
            candidates = {}
            for pool in self.pools.values():
                base, quote = pool.base, pool.quote
                rB, rQ = pool.base_reserve, pool.quote_reserve
                if rB <= 0 or rQ <= 0:
                    continue
                b_known = self.token_prices.get(base, 0)
                q_known = self.token_prices.get(quote, 0)

                # derive quote from base: quote_usd = (rB * base_usd) / rQ
                if b_known > 0 and q_known == 0:
                    price = (rB * b_known) / rQ
                    weight = rB * b_known   # USD depth of the known side
                    cur = candidates.get(quote)
                    if cur is None or weight > cur[0]:
                        candidates[quote] = (weight, price)

                # derive base from quote: base_usd = (rQ * quote_usd) / rB
                elif q_known > 0 and b_known == 0:
                    price = (rQ * q_known) / rB
                    weight = rQ * q_known
                    cur = candidates.get(base)
                    if cur is None or weight > cur[0]:
                        candidates[base] = (weight, price)

            for sym, (_weight, price) in candidates.items():
                if price > 0:
                    self.token_prices[sym] = price
                    changed_any = True
            return changed_any
        
        # Iterate until no more prices can be derived (max 10 rounds)
        for _ in range(10):
            if not _derive_prices():
                break
        
        # Sanity clamp: reject prices that are clearly wrong
        # Only clamp if price is absurdly high (>$1M) or negative
        for sym in list(self.token_prices.keys()):
            p = self.token_prices[sym]
            if p > 1_000_000 or p < 0:
                print(f"  PriceEngine: clamped {sym} from ${p:.8f} to 0 (out of range)")
                self.token_prices[sym] = 0
        
        # 2026-10-02: fill pool-less tokens from the market order book.
        #
        # Every price above is derived from POOL RESERVES, so a token that has a
        # real market but no pool at all is left at 0. That silently made ten
        # reward tokens (BBHO, FRESA, HOP, IDD, WRD, POKEMON, BEATCZ, PEPECASH,
        # SWAP.PEPE, SWAP.PEPET) look unpriced, which in turn made
        # ALIVE:HEARTBEAT and ALIVE:SWAP.HIVE look dividend-free.
        #
        # Tydyn: "there are markets for all layer-2 Hive-Engine tokens,
        # regardless of whether there are any buy or sell orders, but not all
        # tokens have pools."
        #
        # The market price is the HIGHEST BID (what a sale realises), not
        # lastPrice (a stale print), and the bid/ask spread is retained as a
        # liquidity measure. Pool-derived prices are never overwritten.
        # Candidate list: the fallback iterates only keys ALREADY in the map,
        # and a pool-less token is never a key there. So build the candidate set
        # explicitly — every symbol seen anywhere we know about.
        # BOUNDED, NOT GLOBAL (2026-10-02 second fix). The previous set was
        # token_prices keys + every pool id + extras = 213 symbols, i.e. 213
        # sequential HTTP round-trips inside a one-shot load_all(). The run was
        # cut short again, still missing the tokens that motivated the change.
        # Price only what a decision about THIS portfolio consumes:
        #   - pool pair tokens of held pools (what we could deposit into)
        #   - reward tokens of held pools (what we would earn)
        #   - wallet holdings (what we already own)
        # Everything else stays unpriced, which is correct and cheap.
        _candidates = set(self.extra_price_candidates or ())
        try:
            for _pid in (self.held_pools or ()):
                _candidates.update(t for t in str(_pid).split(":") if t)
        except Exception:
            pass
        try:
            from lp_market_price import enrich_price_map
            _before = len([s for s, p in self.token_prices.items() if p == 0])
            self.token_prices, _added, _still = enrich_price_map(
                self.token_prices, symbols=sorted(_candidates))
            self.market_liquidity = _added
            if _added:
                _tight = sum(1 for m in _added.values() if m["liquidity"] == "tight")
                _illiq = sum(1 for m in _added.values() if not m["sizeable"])
                print(f"  PriceEngine: market-filled {len(_added)} of "
                      f"{len(_candidates)} candidates "
                      f"({_tight} tight, {_illiq} not sizeable for exit)")
            # NAME any requested token we could not price. A quiet partial fill
            # behind a success-shaped summary is how BBHO/FRESA/HOP went missing
            # while the log still read "198 filled".
            _missed = [s for s in (self.requested_price_candidates or ())
                       if not self.token_prices.get(s)]
            if _missed:
                print(f"  PriceEngine: WARNING {len(_missed)} requested token(s) "
                      f"still unpriced: {', '.join(sorted(_missed)[:12])}")
        except Exception as e:  # noqa: BLE001
            self.market_liquidity = {}
            print(f"  PriceEngine: market pricing unavailable ({type(e).__name__})")

        # 2026-10-02: the realisable-dividend gate needs a bid/ask spread for
        # EVERY token a distribution is paid in, not only the pool-less ones we
        # just filled. Pool-priced tokens have a price but no spread reading, so
        # their yield was UNKNOWN and could never count as income — the gate
        # was inert for exactly the tokens it exists to judge.
        _gate_syms = set(self.extra_price_candidates or ())
        try:
            from lp_market_price import market_price as _mp
            for _s in sorted(_gate_syms):
                if _s in self.market_liquidity or self.token_prices.get(_s):
                    _m = _mp(_s)
                    if _m:
                        self.market_liquidity.setdefault(_s, _m)
        except Exception as e:  # noqa: BLE001
            print(f"  PriceEngine: spread lookup for gate failed ({type(e).__name__})")

        # Report how many stayed unknown
        unknown = [s for s, p in self.token_prices.items() if p == 0]
        if unknown:
            print(f"  PriceEngine: {len(self.pools)} pools, {len(self.token_prices)} tokens priced, {len(unknown)} unknown")
        else:
            print(f"  PriceEngine: {len(self.pools)} pools, {len(self.token_prices)} tokens priced")

        # ===== SECOND PASS (2026-10-17): honest USD liquidity/volume/APR =====
        # The first pass computed liquidity_usd and volume_24h BEFORE any USD
        # price existed, as base_reserve * base_price_in_quote + quote_reserve *
        # quote_price_in_base — a sum of MIXED TOKEN UNITS (CENT-units plus
        # TRIBE-units), never USD. Downstream consumers read garbage:
        # CENT:TRIBE carried "$6.4M" against a real ~$746 TVL; the
        # min-liquidity gate passed $84 pools as $1.4M; the reward-per-HIVE
        # scoring term (2026-10-10) was INERT because its pool_tvl_map input
        # was this unit-babble. Recompute everything now that the USD price
        # map is complete. Convention preserved: volume counts BOTH sides
        # (each swap adds its size to baseVolume and quoteVolume alike).
        # A pool with EITHER side unpriced gets TVL 0.0 — UNKNOWN, never a
        # half-honest sum — matching the standing rule that a missing TVL
        # is not a small TVL (rph then skips the term, liquidity gates then
        # exclude the pool: both conservative and honest).
        _fixed = _unknown_tvl = 0
        for _pool in self.pools.values():
            _b_usd = self.token_prices.get(_pool.base) or 0.0
            _q_usd = self.token_prices.get(_pool.quote) or 0.0
            if _b_usd > 0 and _q_usd > 0:
                _pool.liquidity_usd = (_pool.base_reserve * _b_usd
                                       + _pool.quote_reserve * _q_usd)
                _pool.volume_24h = (_pool.base_volume * _b_usd
                                    + _pool.quote_volume * _q_usd)
                _pool.fees_24h = _pool.volume_24h * 0.003
                _pool.apr = ((_pool.fees_24h / _pool.liquidity_usd)
                             * 365 * 100) if _pool.liquidity_usd > 0 else 0.0
                _fixed += 1
            else:
                _pool.liquidity_usd = 0.0
                _pool.volume_24h = 0.0
                _pool.fees_24h = 0.0
                _pool.apr = 0.0
                _unknown_tvl += 1
        if _unknown_tvl:
            print(f"  PriceEngine: USD pass priced {_fixed} pools' TVL; "
                  f"{_unknown_tvl} left at 0 (a side token unpriced)")

        self._loaded = True

    def get_pool(self, base: str, quote: str) -> Optional[PoolData]:
        return self.pools.get(f"{base}:{quote}") or self.pools.get(f"{quote}:{base}")

    def exit_liquidity(self, symbols):
        """Total exit liquidity per symbol, in HIVE (2026-10-07, PsyopDaily
        mechanics verified against two of the site's published anchors).

        exit_liquidity(T) = order-book bid depth  +  max counter-side pool
        reserve, where:
          - bid depth    = sum(price * quantity) over market.buyBook rows for
            T (site anchor: WIT = 0.2432 HIVE — exact match);
          - pool depth   = the SWAP.HIVE-side (or deepest counter-side)
            reserve of the largest pool containing T — what a sale into the
            pool would actually pay out (site anchor: REVX = 0.2656 HIVE,
            exactly the SWAP.HIVE baseQuantity of SWAP.HIVE:REVX — NOT the
            pool's TVL, which would overstate depth ~2x in the DANGEROUS
            direction: approving an unexitable pool).
        ANCHOR EXEMPTION (caught by the live gate, 2026-10-07): SWAP.HIVE and
        SWAP.HBD are the L1 wrappers — their exit is the CHAIN BRIDGE
        (SWAP.HIVE → native HIVE at 1:1), not a market. Both probes
        legitimately read ~0 for them (SWAP.HIVE is the universal quote, so
        nobody bids FOR it and pool sales pay the other side), which without
        this exemption would false-positive-block every SWAP.HIVE pair from
        expansion — half the ecosystem. Anchors are therefore exempt from
        the floor: they appear in the map as None with this documented
        meaning (market-unbounded), which the gate correctly does not gate.
        Returns {symbol: depth_in_HIVE or None}. None = UNKNOWN (probe
        failure) or MARKET-UNBOUNDED (anchor) — never zero-as-unknown; the
        caller must treat None as not-gated, not as below-floor.
        Scoped by the caller to exactly the symbols the decision consumes
        (held-pool pair tokens + reward tokens) — one buyBook query per
        symbol, bounded by that set.
        """
        from lp_market_price import bid_depth_hive
        ANCHOR_EXIT_TOKENS = {"SWAP.HIVE", "SWAP.HBD"}
        out: Dict[str, Optional[float]] = {}
        for s in sorted(set(symbols or [])):
            if s in ANCHOR_EXIT_TOKENS:
                out[s] = None  # chain-bridge exit, market-unbounded — see docstring
                continue
            book = bid_depth_hive(s)
            pool_depth = None
            best = None
            for p in self.pools.values():
                if s not in (p.base, p.quote):
                    continue
                # counter-side reserve: what the sale of T lands as.
                # Orientation: if T is base, you receive quote; and vice versa.
                counter = p.quote_quantity if s == p.base else p.base_quantity
                # depth must be HIVE-denominated: only counter == SWAP.HIVE
                # is a direct reading; a non-HIVE counter is not exit depth
                # without a conversion, so it does not count as pool depth.
                counter_sym = p.quote if s == p.base else p.base
                if counter_sym == "SWAP.HIVE":
                    if best is None or counter > best:
                        best = counter
            if best is not None:
                pool_depth = best
            if book is None and pool_depth is None:
                out[s] = None
            else:
                out[s] = (book or 0.0) + (pool_depth or 0.0)
        return out

    def find_path(self, from_sym: str, to_sym: str) -> List[PoolData]:
        """Find swap path (max 2 hops)."""
        if from_sym == to_sym:
            return []
        direct = self.get_pool(from_sym, to_sym)
        if direct:
            return [direct]
        # 2-hop via HIVE
        for mid in ["HIVE", "SWAP.HIVE"]:
            p1 = self.get_pool(from_sym, mid)
            p2 = self.get_pool(mid, to_sym)
            if p1 and p2:
                return [p1, p2]
        return []

    def estimate_swap_out(self, amount_in: float, path: List[PoolData]) -> float:
        amt = amount_in
        for pool in path:
            if pool.base == "HIVE" or pool.quote == "HIVE":
                # Simplified constant product
                if amt < pool.base_reserve * 0.1:  # <10% impact
                    amt = amt * pool.base_price * 0.997
                else:
                    return 0
            else:
                amt = amt * 0.997 * 0.9  # rough with fees
        return amt

# ─── Portfolio Manager ──────────────────────────────────────────────
class PortfolioManager:
    def __init__(self, price_engine: PriceEngine):
        self.pe = price_engine
        self.balances: Dict[str, TokenBalance] = {}
        self.positions: Dict[str, LPPosition] = {}
        # 2026-10-22: the position-assessment orchestration reads the
        # account name from the portfolio it is handed.
        self.account = ACCOUNT

    def load_balances(self):
        raw = he("find", {
            "contract": "tokens", "table": "balances",
            "query": {"account": ACCOUNT}, "limit": 200
        })
        for b in raw:
            sym = b.get("symbol", "")
            bal = float(b.get("balance", 0))
            staked = float(b.get("stake", 0))
            price = self.pe.token_prices.get(sym, 0)
            # 2026-10-22 spec restore: LIQUID value only in value_usd;
            # staked value reported separately (pinned: test_liquid_excludes_stake).
            self.balances[sym] = TokenBalance(sym, bal, staked, price,
                                              bal * price, staked * price)

    def load_positions(self):
        """Load LP positions via the PUBLIC RPC — reads never open the key
        vault (pinned: test_position_valuation's open-forbidden patch; the
        nectarengine rewrite loaded keys just to read public rows). Reload
        REPLACES: stale positions must not survive (pinned:
        test_reload_removes_stale_positions). Missing/invalid USD prices
        read as UNKNOWN (None), never 0."""
        import math
        self.positions = {}
        rows = he("find", {
            "contract": "marketpools", "table": "liquidityPositions",
            "query": {"account": ACCOUNT}, "limit": 200
        })
        for p in rows:
            try:
                token_pair = p.get("tokenPair", "")
                if ":" not in token_pair:
                    continue
                base, quote = token_pair.split(":", 1)
                shares = float(p.get("shares", 0))

                # Get deposited amounts from pool data
                pool = self.pe.get_pool(base, quote)
                base_dep = 0.0
                quote_dep = 0.0
                if pool and pool.total_shares > 0:
                    base_dep = shares / pool.total_shares * pool.base_quantity
                    quote_dep = shares / pool.total_shares * pool.quote_quantity

                val = None
                apr = 0.0
                if pool:
                    # 2026-09-30 (kept): pool.base_price/quote_price are
                    # exchange ratios, NOT USD — value from the validated
                    # USD price map, per token. A missing price is UNKNOWN
                    # (None), never 0 (which would silently understate).
                    p_base = self.pe.token_prices.get(base)
                    p_quote = self.pe.token_prices.get(quote)
                    valid_base = p_base is not None and p_base > 0 and math.isfinite(p_base)
                    valid_quote = p_quote is not None and p_quote > 0 and math.isfinite(p_quote)
                    if valid_base and valid_quote:
                        assert p_base is not None and p_quote is not None
                        val = (base_dep * p_base) + (quote_dep * p_quote)
                    else:
                        val = None
                        missing = [t for t, v, ok in
                                   ((base, p_base, valid_base),
                                    (quote, p_quote, valid_quote)) if not ok]
                        print(f"  Warning: no valid USD price for {missing} in "
                              f"{token_pair} — value UNKNOWN, reported as None")
                    apr = pool.apr

                pid = f"{base}:{quote}"
                self.positions[pid] = LPPosition(pid, base, quote, shares, base_dep, quote_dep, val, apr,
                                                  time_factor_ms=float(p.get("timeFactor", 0) or 0))
            except Exception as e:
                print(f"  Warning: Failed to parse position {p}: {e}")
                continue

    def total_value(self):
        """None when ANY position's value is unknown — an unknown is not a
        zero and must not be folded into a completed-looking total (pinned:
        test_unknown_total_is_not_complete_value)."""
        total = sum(tb.value_usd for tb in self.balances.values())
        for p in self.positions.values():
            if p.value_usd is None:
                return None
            total += p.value_usd
        return total

    def valuation_summary(self):
        """2026-10-22 spec restore (pinned by test_position_valuation): the
        report must say WHICH positions are unpriced instead of implying
        uniform coverage."""
        return {"total_usd": self.total_value(),
                "unpriced_positions": sorted(
                    pid for pid, p in self.positions.items()
                    if p.value_usd is None),
                "position_count": len(self.positions),
                "balance_count": len(self.balances)}

    def lp_value(self) -> float:
        known = [p.value_usd for p in self.positions.values()
                 if p.value_usd is not None]
        return sum(known)

    def idle_value(self) -> float:
        # 2026-10-22 spec restore: pure sum — dust is NOT filtered from the
        # valuation (pinned: test_dust_is_not_filtered_from_idle; the 1e-4
        # display filter belongs to printing, not to measurement).
        return sum(tb.value_usd for tb in self.balances.values())

# ─── Pool Analyzer ──────────────────────────────────────────────────
class PoolAnalyzer:
    def __init__(self, price_engine: PriceEngine, portfolio: PortfolioManager):
        self.pe = price_engine
        self.portfolio = portfolio

    def score_pool(self, pool: PoolData) -> dict:
        """Score pool 0-1 for LP attractiveness."""
        liq = pool.liquidity_usd
        apr = pool.apr
        vol_liq = pool.volume_24h / liq if liq > 0 else 0
        
        # Liquidity score (0-1): want deep pools
        liq_score = min(liq / 10000, 1.0)
        
        # APR score (0-1): diminishing returns
        apr_score = min(apr / 100, 1.0)
        
        # Volume/liquidity = turnover (0-1): healthy trading
        turn_score = min(vol_liq * 10, 1.0)
        
        # Impermanent loss risk: lower for stable-correlated pairs
        stable_pairs = {("HIVE", "SWAP.HIVE"), ("SWAP.HIVE", "HIVE")}
        il_risk = 0.1 if (pool.base, pool.quote) in stable_pairs or (pool.quote, pool.base) in stable_pairs else 0.5
        
        # Can we enter? Need both tokens
        has_base = self.portfolio.balances.get(pool.base, TokenBalance(pool.base)).balance > 0.0001
        has_quote = self.portfolio.balances.get(pool.quote, TokenBalance(pool.quote)).balance > 0.0001
        can_enter = has_base and has_quote
        
        total = 0.3 * liq_score + 0.3 * apr_score + 0.2 * turn_score + 0.2 * (1 - il_risk)
        
        return {
            "pool": pool.pool_id,
            "total_score": total,
            "liq_score": liq_score,
            "apr_score": apr_score,
            "turn_score": turn_score,
            "il_risk": il_risk,
            "est_apr": apr,
            "liquidity_usd": liq,
            "can_enter": can_enter
        }

    def rank_pools(self, min_liquidity: float = 100) -> List[dict]:
        ranked = []
        for pool in self.pe.pools.values():
            if pool.liquidity_usd < min_liquidity:
                continue
            # Skip dead projects
            if pool.base in DEAD_PROJECTS or pool.quote in DEAD_PROJECTS:
                continue
            ranked.append(self.score_pool(pool))
        ranked.sort(key=lambda x: x["total_score"], reverse=True)
        return ranked

# ─── Reallocation Planner ────────────────────────────────────────────
class ReallocationPlanner:
    """Autonomous reallocation decision engine - mirrors dashboard logic."""
    
    def __init__(self, price_engine: PriceEngine, portfolio: PortfolioManager, analyzer: PoolAnalyzer):
        self.pe = price_engine
        self.portfolio = portfolio
        self.analyzer = analyzer
    
    def get_zombie_positions(self) -> List[dict]:
        """Identify positions in zombie pools that should be exited."""
        zombies = []
        for pid, pos in self.portfolio.positions.items():
            if pid in ZOMBIE_POOLS:
                pool = self.pe.get_pool(pos.base, pos.quote)
                if pool:
                    pool_shares = pool.base_reserve * 1000  # approximate
                    zombies.append({
                        "pool": pid,
                        "our_shares": pos.shares,
                        "pool_shares": pool_shares,
                        "our_pct": (pos.shares / pool_shares * 100) if pool_shares > 0 else 0,
                        "token_a": pos.base,
                        "token_b": pos.quote,
                        "est_a": pos.base_deposited,
                        "est_b": pos.quote_deposited,
                        "value_usd": pos.value_usd
                    })
        return zombies
    
    def score_reallocation_targets(self) -> List[dict]:
        """Score all reallocation target pools using dashboard logic + compound acceleration."""
        ranked = []
        
        for pool_id, target_info in REALLOC_TARGETS.items():
            pool = self.pe.get_pool(*pool_id.split(":"))
            if not pool:
                continue
            
            tier = target_info["tier"]
            notes = target_info["notes"]
            reward_tokens = target_info.get("reward_tokens", [])
            compound_accel = target_info.get("compound_accel", False)
            daily_reward_usd = target_info.get("daily_reward_usd", 0)
            
            # Base score by tier (Tier 2 = 200, Tier 3 = 50 baseline)
            base_score = 200 if tier == 2 else 50
            
            # Add fee threshold bonus
            fee_data = OUR_POOL_FEES.get(pool_id, {})
            if fee_data.get("threshold_crossed", False):
                base_score += 100
            
            # Add ACTUAL daily rewards value (replaces hardcoded HSBIDAO:PIXYDUST)
            if daily_reward_usd > 0:
                base_score += daily_reward_usd  # direct dollar value of daily rewards
            
            # Token importance scoring
            base_imp = TOKEN_IMPORTANCE.get(pool.base, 1)
            quote_imp = TOKEN_IMPORTANCE.get(pool.quote, 1)
            base_score += base_imp + quote_imp
            
            # COMPOUND ACCELERATION: Boost pools whose rewards feed liquidity growth
            if compound_accel:
                # Value of reward tokens (how easily they convert to core liquidity)
                reward_value = sum(TOKEN_IMPORTANCE.get(rt, 1) for rt in reward_tokens)
                # SWAP.HIVE rewards = highest compound value (core liquidity token)
                swap_hive_rewards = sum(1 for rt in reward_tokens if rt in ["SWAP.HIVE", "HIVE"])
                # Allied tokens that can be swapped for SWAP.HIVE
                allied_swap_hive = sum(1 for rt in reward_tokens if rt in ["CENT", "PIXYDUST", "HSBIDAO", "ALIVE", "ALIVEM", "HEARTBEAT"])
                # Compound acceleration bonus
                base_score += reward_value * 2  # reward token diversity
                base_score += swap_hive_rewards * 100  # direct SWAP.HIVE rewards
                base_score += allied_swap_hive * 25  # tokens easily swapped to SWAP.HIVE
            else:
                # No compound acceleration - penalize low-value reward diversity
                # (many meme tokens with no liquidity path to core)
                reward_value = sum(TOKEN_IMPORTANCE.get(rt, 1) for rt in reward_tokens)
                if reward_value > 10 and daily_reward_usd < 1:  # many tokens but low value
                    base_score -= 20  # penalty for fragmented low-value rewards
            
            # Pool health metrics
            health_bonus = 0
            if pool.liquidity_usd > 10000:
                health_bonus += 10
            if pool.apr > 50:
                health_bonus += 10
            elif pool.apr > 20:
                health_bonus += 5
            if pool.volume_24h / pool.liquidity_usd > 0.1:  # turnover > 10%
                health_bonus += 10
            
            base_score += health_bonus
            
            ranked.append({
                "pool": pool_id,
                "score": base_score,
                "tier": tier,
                "notes": notes,
                "reward_tokens": reward_tokens,
                "compound_accel": compound_accel,
                "daily_reward_usd": daily_reward_usd,
                "liquidity_usd": pool.liquidity_usd,
                "apr": pool.apr,
                "fee_threshold_crossed": fee_data.get("threshold_crossed", False),
                "can_enter": self._can_enter_pool(pool)
            })
        
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked
    
    def _can_enter_pool(self, pool: PoolData) -> bool:
        """Check if we have both tokens to enter a pool."""
        has_base = self.portfolio.balances.get(pool.base, TokenBalance(pool.base)).balance > 0.0001
        has_quote = self.portfolio.balances.get(pool.quote, TokenBalance(pool.quote)).balance > 0.0001
        return has_base and has_quote
    
    def generate_plan(self) -> dict:
        """Generate complete reallocation plan."""
        zombie_positions = self.get_zombie_positions()
        realloc_targets = self.score_reallocation_targets()
        
        # Calculate exit proceeds
        exit_tokens = {}
        total_exit_value = 0
        for z in zombie_positions:
            exit_tokens[z["token_a"]] = exit_tokens.get(z["token_a"], 0) + z["est_a"]
            exit_tokens[z["token_b"]] = exit_tokens.get(z["token_b"], 0) + z["est_b"]
            total_exit_value += z["value_usd"]
        
        # Calculate allocation percentages based on ALL target scores (we'll swap tokens after exit)
        total_score = sum(r["score"] for r in realloc_targets)
        allocations = []
        for r in realloc_targets:
            if total_score > 0:
                pct = (r["score"] / total_score) * 100
            else:
                pct = 0
            allocations.append({
                "pool": r["pool"],
                "allocation_pct": pct,
                "score": r["score"],
                "notes": r["notes"],
                "needs_swaps": not r["can_enter"]
            })
        
        return {
            "zombie_positions": zombie_positions,
            "exit_tokens": exit_tokens,
            "total_exit_value": total_exit_value,
            "realloc_targets": realloc_targets,
            "allocations": allocations
        }
    
    def should_exit_zombie(self) -> bool:
        """Decision: should we exit zombie pools now?"""
        zombies = self.get_zombie_positions()
        if not zombies:
            return False
        
        # Check if any zombie has meaningful position
        for z in zombies:
            if z["value_usd"] > 0.001:
                return True
        return False
    
    def execute_reallocation(self) -> bool:
        """Execute the reallocation plan."""
        plan = self.generate_plan()
        
        print(f"\n=== REALLOCATION PLAN ===")
        print(f"Zombie pools to exit: {len(plan['zombie_positions'])}")
        for z in plan['zombie_positions']:
            print(f"  {z['pool']}: {z['our_shares']:.6f} shares ({z['our_pct']:.4f}%) "
                  f"≈ {z['est_a']:.6f} {z['token_a']} + {z['est_b']:.6f} {z['token_b']} "
                  f"(${z['value_usd']:.4f})")
        
        print(f"\nExit tokens: {', '.join(f'{v:.6f} {k}' for k,v in plan['exit_tokens'].items())}")
        print(f"Total exit value: ${plan['total_exit_value']:.4f}")
        
        print(f"\nReallocation targets (ranked):")
        for a in plan['allocations']:
            print(f"  {a['pool']:25s}  {a['allocation_pct']:5.1f}%  Score: {a['score']:.0f}  {a['notes']}")
        
        if not plan['zombie_positions']:
            print("No zombie positions to exit - skipping reallocation")
            return False
        
        # Execute exits
        print(f"\n=== EXECUTING EXITS ===")
        for z in plan['zombie_positions']:
            pool = self.pe.get_pool(z['token_a'], z['token_b'])
            if pool and z['our_shares'] > 0:
                print(f"  Removing liquidity from {z['pool']}...")
                # Remove ALL shares from zombie pool
                self.analyzer.portfolio = self.portfolio  # ensure fresh state
                if self.analyzer.portfolio:
                    pass
                self.pe.load_all()  # refresh
                self.portfolio.load_positions()
                # Use executor to remove
                from nectarengine.pool import LiquidityPool
                from lp_broadcast_guard import broadcast_once_hive
                acct = json.load(open(VAULT_PATH))
                # BROADCAST-ONCE: remove_liquidity broadcasts; never auto-retry (lp_broadcast_guard.py)
                hive = broadcast_once_hive(node=[HIVE_RPC], keys={acct["account"]: acct["active"]})
                lp = LiquidityPool(blockchain_instance=hive)
                result = lp.remove_liquidity(acct["account"], z['pool'], z['our_shares'])
                if result:
                    print(f"    ✓ Exited {z['pool']}")
                else:
                    print(f"    ✗ Failed to exit {z['pool']}")
        
        # After exits, re-load balances and deploy to targets
        time.sleep(5)  # wait for blockchain
        self.portfolio.load_balances()
        self.portfolio.load_positions()
        
        print(f"\n=== DEPLOYING TO TARGETS ===")
        for a in plan['allocations']:
            if a['allocation_pct'] <= 0:
                continue
            pool = self.pe.get_pool(*a['pool'].split(":"))
            if pool:
                # Deploy proportional to allocation
                self._deploy_proportional(pool, a['allocation_pct'] / 100.0)
        
        return True
    
    def _deploy_proportional(self, pool: PoolData, allocation_fraction: float):
        """Deploy available capital proportionally to a target pool."""
        base_bal = self.portfolio.balances.get(pool.base, TokenBalance(pool.base)).balance
        quote_bal = self.portfolio.balances.get(pool.quote, TokenBalance(pool.quote)).balance
        
        pool_ratio = pool.base_reserve / pool.quote_reserve if pool.quote_reserve > 0 else 0
        if pool_ratio == 0:
            return
        
        # Use allocation fraction of available balances
        base_deploy = base_bal * allocation_fraction * 0.5
        quote_deploy = quote_bal * allocation_fraction * 0.5
        
        # Adjust to pool ratio
        if base_deploy > 0 and quote_deploy > 0:
            actual_ratio = base_deploy / quote_deploy
            if actual_ratio > pool_ratio:
                base_deploy = quote_deploy * pool_ratio
            else:
                quote_deploy = base_deploy / pool_ratio
        
        # Round to precision
        base_deploy = self.pe.round_amount(pool.base, base_deploy)
        quote_deploy = self.pe.round_amount(pool.quote, quote_deploy)
        
        if base_deploy < 0.0001 or quote_deploy < 0.0001:
            return
        
        print(f"  → Adding LP: {base_deploy:.6f} {pool.base} + {quote_deploy:.6f} {pool.quote} ({pool.pool_id})")
        
        from nectarengine.pool import LiquidityPool
        from lp_broadcast_guard import broadcast_once_hive
        acct = json.load(open(VAULT_PATH))
        # BROADCAST-ONCE: add_liquidity broadcasts; never auto-retry (lp_broadcast_guard.py)
        hive = broadcast_once_hive(node=[HIVE_RPC], keys={acct["account"]: acct["active"]})
        lp = LiquidityPool(blockchain_instance=hive)
        result = lp.add_liquidity(acct["account"], f"{pool.base}:{pool.quote}", base_deploy, quote_deploy, max_deviation=1)
        if result:
            print(f"    ✓ Deployed")
        else:
            print(f"    ✗ Failed")

# ─── Execution Engine ───────────────────────────────────────────────
class ExecutionEngine:
    def __init__(self, price_engine: PriceEngine, portfolio: PortfolioManager):
        self.pe = price_engine
        self.portfolio = portfolio
        self.acct = json.load(open(VAULT_PATH))
        from lp_broadcast_guard import broadcast_once_hive
        # BROADCAST-ONCE: self.hive feeds ONLY swap/add/remove broadcasts
        # (Market at :1030, LiquidityPool at :1047/:1066) — never reads —
        # so RPC failures must raise, never auto-retry (lp_broadcast_guard.py).
        self.hive = broadcast_once_hive(node=[HIVE_RPC], keys={self.acct["account"]: self.acct["active"]})

    def swap(self, from_sym: str, to_sym: str, amount: float, slippage: float = 0.02) -> bool:
        """Execute a swap via Hive Engine market."""
        path = self.pe.find_path(from_sym, to_sym)
        if not path:
            print(f"  ✗ No path {from_sym} -> {to_sym}")
            return False
        
        print(f"  Swap path: {' -> '.join(p.pool_id for p in path)}")
        
        try:
            from nectarengine.market import Market
            
            market = Market(blockchain_instance=self.hive)
            
            # Execute actual market order - signature: buy(account, amount, symbol, price)
            # price=0 means market order, buys `amount` of `symbol` paying with quote
            result = market.buy(self.acct["account"], amount, from_sym, 0)
            
            print(f"  ✓ Swapped {amount:.6f} {from_sym} -> {to_sym}")
            return True
        except Exception as e:
            print(f"  ✗ Swap failed: {e}")
            return False

    def add_liquidity(self, base: str, quote: str, base_amt: float, quote_amt: float) -> bool:
        """Add liquidity to a pool."""
        try:
            from nectarengine.pool import LiquidityPool
            
            lp = LiquidityPool(blockchain_instance=self.hive)
            # Include max_deviation=1% tolerance to ensure contract acceptance
            result = lp.add_liquidity(self.acct["account"], f"{base}:{quote}", base_amt, quote_amt, max_deviation=1)
            
            if not result:
                print(f"  ✗ Failed to add liquidity to {base}:{quote}")
                return False
            
            print(f"  ✓ Added liquidity: {base_amt:.6f} {base} + {quote_amt:.6f} {quote}")
            return True
        except Exception as e:
            print(f"  ✗ Add liquidity failed: {e}")
            return False

    def remove_liquidity(self, base: str, quote: str, lp_tokens: float) -> bool:
        """Remove liquidity from a pool."""
        try:
            from nectarengine.pool import LiquidityPool
            
            lp = LiquidityPool(blockchain_instance=self.hive)
            result = lp.remove_liquidity(self.acct["account"], f"{base}:{quote}", lp_tokens)
            
            if not result:
                print(f"  ✗ Failed to remove liquidity from {base}:{quote}")
                return False
            
            print(f"  ✓ Removed {lp_tokens:.6f} LP tokens from {base}:{quote}")
            return True
        except Exception as e:
            print(f"  ✗ Remove liquidity failed: {e}")
            return False

# ─── Strategy Engine ────────────────────────────────────────────────
class StrategyEngine:
    def __init__(self, price_engine: PriceEngine, portfolio: PortfolioManager, 
                 analyzer: PoolAnalyzer, executor: ExecutionEngine):
        self.pe = price_engine
        self.portfolio = portfolio
        self.analyzer = analyzer
        self.executor = executor
        self.last_swap_time = {}
        self.min_swap_interval = 3600  # 1 hour between swaps per pair

    def run_cycle(self):
        print(f"\n{'='*60}")
        print(f"LIQUIDITY AGENT CYCLE — {datetime.now().isoformat()}")
        print(f"{'='*60}")
        
        # Refresh state
        self.portfolio.balances.clear()
        self.portfolio.positions.clear()
        self.portfolio.load_balances()
        self.portfolio.load_positions()
        
        total_val = self.portfolio.total_value()
        # total_value is None when any position is UNPRICED — say so instead
        # of crashing the report (pinned: test_unknown_total_is_not_complete_value)
        if total_val is None:
            print("\nTotal Portfolio Value: UNKNOWN (unpriced positions: "
                  f"{self.portfolio.valuation_summary()['unpriced_positions']})")
        else:
            print(f"\nTotal Portfolio Value: ${total_val:.4f}")
        
        # Show unstaking schedule
        self._print_unstaking_schedule()
        
        # Rank opportunities
        ranked = self.analyzer.rank_pools(min_liquidity=500)
        
        print(f"\n=== TOP POOL OPPORTUNITIES ===")
        for i, r in enumerate(ranked[:10]):
            print(f"  {i+1:2d}. {r['pool']:30s}  Score: {r['total_score']:.3f}  "
                  f"EstAPR: {r['est_apr']:.1f}%  IL: {r['il_risk']:.2f}  "
                  f"Liq: ${r['liquidity_usd']:,.0f}  CanEnter: {r['can_enter']}")
        
        # Decision logic - manage ALL target pools
        self._execute_strategy(ranked[:5])
        
        print(f"\nCycle complete. Next scan in {SCAN_INTERVAL}s.\n")

    def _print_unstaking_schedule(self):
        print(f"\n=== UNSTAKING SCHEDULE ===")
        for token, days in UNSTAKING_SCHEDULE.items():
            bal = self.portfolio.balances.get(token)
            staked = bal.staked if bal else 0
            if staked > 0:
                print(f"  {token}: {staked:.4f} staked → available in ~{days} days")
            elif bal and bal.balance > 0:
                print(f"  {token}: {bal.balance:.4f} available (not staked)")

    def _execute_strategy(self, top_pools: List[dict]):
        """Core strategy: deploy idle capital into best pools AND manage target pools."""
        idle = {sym: tb for sym, tb in self.portfolio.balances.items() 
                if tb.value_usd > 0.0001}
        
        if not idle:
            print("  No idle capital to deploy.")
            return
        
        print(f"\n=== STRATEGY EXECUTION ===")
        print(f"  Idle tokens: {', '.join(f'{sym}({tb.value_usd:.2f})' for sym, tb in idle.items())}")
        
        # Track which pools we have positions in
        current_positions = set(self.portfolio.positions.keys())
        
        # NEW: First, swap idle tokens to SWAP.HIVE (or their paired token) to build core liquidity
        self._swap_idle_to_core()
        
        # First: ensure we have positions in ALL target pools we can enter
        # Calculate SWAP.HIVE budget to distribute across target pools needing it
        swap_hive_total = self.portfolio.balances.get("SWAP.HIVE", TokenBalance("SWAP.HIVE")).balance
        swap_hive_needed = []
        
        for target_pool in TARGET_POOLS:
            if target_pool in current_positions:
                continue
            
            pool = self.pe.get_pool(*target_pool.split(":"))
            if not pool:
                continue
            
            has_base = self.portfolio.balances.get(pool.base, TokenBalance(pool.base)).balance > 0.0001
            has_quote = self.portfolio.balances.get(pool.quote, TokenBalance(pool.quote)).balance > 0.0001
            
            needs_swap_hive = ("SWAP.HIVE" in (pool.base, pool.quote)) and (has_base != has_quote)
            if needs_swap_hive:
                swap_hive_needed.append((target_pool, pool, has_base, has_quote))
            elif has_base and has_quote:
                self._deploy_to_pool(pool)
            elif has_base or has_quote:
                self._try_swap_and_deploy(pool, has_base, has_quote)
        
        # Distribute SWAP.HIVE across pools that need it
        if swap_hive_needed and swap_hive_total > 0.001:
            per_pool = swap_hive_total / len(swap_hive_needed)
            print(f"  Distributing {swap_hive_total:.6f} SWAP.HIVE across {len(swap_hive_needed)} pools ({per_pool:.6f} each)")
            
            for target_pool, pool, has_base, has_quote in swap_hive_needed:
                # Temporarily cap SWAP.HIVE balance for this pool's deployment
                if pool.base == "SWAP.HIVE":
                    orig_bal = self.portfolio.balances["SWAP.HIVE"].balance
                    self.portfolio.balances["SWAP.HIVE"].balance = min(orig_bal, per_pool)
                    self._try_swap_and_deploy(pool, has_base, has_quote)
                    self.portfolio.balances["SWAP.HIVE"].balance = orig_bal
                elif pool.quote == "SWAP.HIVE":
                    orig_bal = self.portfolio.balances["SWAP.HIVE"].balance
                    self.portfolio.balances["SWAP.HIVE"].balance = min(orig_bal, per_pool)
                    self._try_swap_and_deploy(pool, has_base, has_quote)
                    self.portfolio.balances["SWAP.HIVE"].balance = orig_bal
        
        # Second: deploy remaining idle capital to best-scoring pools
        for opp in top_pools:
            if opp["total_score"] < 0.4:
                continue
            
            pool = self.pe.get_pool(*opp["pool"].split(":"))
            if not pool:
                continue
            
            self._deploy_to_pool(pool)
    
    def _swap_idle_to_core(self):
        """Swap idle non-core tokens to SWAP.HIVE (or their best paired token) to build deployable liquidity."""
        import time
        
        # Core tokens we want to accumulate
        core_tokens = {"SWAP.HIVE", "HIVE", "HBD"}
        # Tokens that easily swap to SWAP.HIVE
        swap_hive_pools = {
            "POB": "SWAP.HIVE",      # POB:SWAP.HIVE pool exists
            "PHOTO": "SWAP.HIVE",    # SWAP.HIVE:PHOTO pool exists
            "CENT": "SWAP.HIVE",     # SWAP.HIVE:CENT pool exists
            "HEARTBEAT": "ALIVE",    # ALIVE:HEARTBEAT pool exists (then ALIVE->SWAP.HIVE)
            "PIXYDUST": "SWAP.HIVE", # HSBIDAO:PIXYDUST, but SWAP.HIVE not direct
            "BEE": "SWAP.HIVE",      # SWAP.HIVE:BEE exists but zombie
        }
        
        for token, target in swap_hive_pools.items():
            bal = self.portfolio.balances.get(token)
            if not bal or bal.balance < 0.01:
                continue
            
            # Skip if already a core token
            if token in core_tokens:
                continue
            
            # Check if we already swapped this pair recently
            swap_key = f"{token}:{target}"
            last_swap = self.last_swap_time.get(swap_key, 0)
            if time.time() - last_swap < self.min_swap_interval:
                continue
            
            # Find the pool
            pool = None
            for p in self.pe.pools.values():
                if set([p.base, p.quote]) == set([token, target]):
                    pool = p
                    break
            
            if not pool:
                continue
            
            # Determine swap direction
            if pool.base == token:
                from_sym, to_sym = token, target
            else:
                from_sym, to_sym = target, token
            
            # Swap half our balance (keep some for existing positions)
            swap_amount = min(bal.balance * 0.5, 10.0)
            if swap_amount < 0.001:
                continue
            
            print(f"  → Swapping {swap_amount:.6f} {from_sym} → {to_sym} for core liquidity")
            if self.executor.swap(from_sym, to_sym, swap_amount):
                self.last_swap_time[swap_key] = time.time()
                self.portfolio.load_balances()
                print(f"    ✓ Swap successful")
        
        # Also swap ALIVE to SWAP.HIVE if we have ALIVE
        alive_bal = self.portfolio.balances.get("ALIVE")
        if alive_bal and alive_bal.balance > 0.01:
            swap_key = "ALIVE:SWAP.HIVE"
            last_swap = self.last_swap_time.get(swap_key, 0)
            if time.time() - last_swap >= self.min_swap_interval:
                pool = self.pe.get_pool("SWAP.HIVE", "ALIVE")
                if pool:
                    swap_amount = min(alive_bal.balance * 0.5, 10.0)
                    if swap_amount >= 0.001:
                        print(f"  → Swapping {swap_amount:.6f} ALIVE → SWAP.HIVE for core liquidity")
                        if self.executor.swap("ALIVE", "SWAP.HIVE", swap_amount):
                            self.last_swap_time[swap_key] = time.time()
                            self.portfolio.load_balances()
                            print(f"    ✓ Swap successful")

    def _try_swap_and_deploy(self, pool: PoolData, has_base: bool, has_quote: bool):
        """Try to swap idle token for the missing pair token, then deploy."""
        import time
        
        # Check swap cooldown
        swap_key = f"{pool.base}:{pool.quote}"
        last_swap = self.last_swap_time.get(swap_key, 0)
        if time.time() - last_swap < self.min_swap_interval:
            return
        
        if has_base:
            base_sym, quote_sym = pool.base, pool.quote
            base_bal = self.portfolio.balances[base_sym].balance
            if base_bal < 0.001:
                return
            # Try to swap half our base for quote
            swap_amount = min(base_bal * 0.5, 10.0)  # Cap at 10 tokens
            print(f"  → Swapping {swap_amount:.6f} {base_sym} → {quote_sym} for {pool.pool_id}")
            if self.executor.swap(base_sym, quote_sym, swap_amount):
                self.last_swap_time[swap_key] = time.time()
                # Re-check balances and deploy
                self.portfolio.load_balances()
                self._deploy_to_pool(pool)
        elif has_quote:
            base_sym, quote_sym = pool.quote, pool.base
            quote_bal = self.portfolio.balances[base_sym].balance
            if quote_bal < 0.001:
                return
            swap_amount = min(quote_bal * 0.5, 10.0)
            print(f"  → Swapping {swap_amount:.6f} {base_sym} → {quote_sym} for {pool.pool_id}")
            if self.executor.swap(base_sym, quote_sym, swap_amount):
                self.last_swap_time[swap_key] = time.time()
                self.portfolio.load_balances()
                self._deploy_to_pool(pool)
    
    def _deploy_to_pool(self, pool: PoolData):
        """Deploy proportional liquidity to a pool."""
        base_bal = self.portfolio.balances.get(pool.base, TokenBalance(pool.base)).balance
        quote_bal = self.portfolio.balances.get(pool.quote, TokenBalance(pool.quote)).balance
        
        # Calculate max deployable maintaining pool ratio
        pool_ratio = pool.base_reserve / pool.quote_reserve if pool.quote_reserve > 0 else 0
        if pool_ratio == 0:
            return
        
        # How much base we could deploy if using all quote (and vice versa)
        max_base_from_quote = quote_bal * 0.5 * pool_ratio
        max_quote_from_base = base_bal * 0.5 / pool_ratio
        
        # Take the limiting factor, capped at 1% of pool reserves
        base_deploy = min(base_bal * 0.5, max_base_from_quote, pool.base_reserve * 0.01)
        quote_deploy = min(quote_bal * 0.5, max_quote_from_base, pool.quote_reserve * 0.01)
        
        # Final ratio check - adjust to match pool ratio exactly
        if base_deploy > 0 and quote_deploy > 0:
            actual_ratio = base_deploy / quote_deploy
            if actual_ratio > pool_ratio:
                # Too much base, reduce base
                base_deploy = quote_deploy * pool_ratio
            else:
                # Too much quote, reduce quote
                quote_deploy = base_deploy / pool_ratio
        
        if base_deploy < 0.0001 or quote_deploy < 0.0001:
            return
        
        # Round to token precision to avoid contract rejection
        base_deploy = self.pe.round_amount(pool.base, base_deploy)
        quote_deploy = self.pe.round_amount(pool.quote, quote_deploy)
        
        # After rounding, re-verify ratio still within max_deviation=1%
        if base_deploy > 0 and quote_deploy > 0:
            actual_ratio = base_deploy / quote_deploy
            deviation = abs(actual_ratio - pool_ratio) / pool_ratio
            if deviation > 0.01:  # 1% max deviation
                # Re-adjust the larger token down to maintain ratio
                if actual_ratio > pool_ratio:
                    base_deploy = self.pe.round_amount(pool.base, quote_deploy * pool_ratio)
                else:
                    quote_deploy = self.pe.round_amount(pool.quote, base_deploy / pool_ratio)
        
        if base_deploy < 0.0001 or quote_deploy < 0.0001:
            return
        
        print(f"  → Adding LP: {base_deploy:.6f} {pool.base} + {quote_deploy:.6f} {pool.quote} "
              f"(pool={pool.pool_id})")
        
        # EXECUTE FOR REAL
        self.executor.add_liquidity(pool.base, pool.quote, base_deploy, quote_deploy)

# ─── Main Agent ─────────────────────────────────────────────────────
class LiquidityAgent:
    def __init__(self):
        self.pe = PriceEngine()
        self.portfolio = PortfolioManager(self.pe)
        self.analyzer = PoolAnalyzer(self.pe, self.portfolio)
        self.reallocator = ReallocationPlanner(self.pe, self.portfolio, self.analyzer)
        # 2026-10-22 spec restore (pinned: test_cron_dry_run): the executor
        # loads the KEY VAULT in its constructor, so a dry-run tick must
        # never construct it — lazy properties, constructed only on first
        # actual use by the live paths.
        self._executor = None
        self._strategy = None

    @property
    def executor(self):
        if self._executor is None:
            self._executor = ExecutionEngine(self.pe, self.portfolio)
        return self._executor

    @property
    def strategy(self):
        if self._strategy is None:
            self._strategy = StrategyEngine(self.pe, self.portfolio, self.analyzer, self.executor)
        return self._strategy

    def run(self):
        print(f"\n🛸 HIVE LIQUIDITY AGENT v2 — @hermes-sandbox")
        print(f"   Starting autonomous cycle (interval: {SCAN_INTERVAL}s)")
        print(f"   Press Ctrl+C to stop\n")

        try:
            while True:
                self.pe.load_all()
                self.strategy.run_cycle()
                
                # Check and execute reallocation
                if self.reallocator.should_exit_zombie():
                    print(f"\n{'='*60}")
                    print(f"ZOMBIE POOLS DETECTED — EXECUTING REALLOCATION")
                    print(f"{'='*60}")
                    self.reallocator.execute_reallocation()
                
                time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            print("\n👋 Agent stopped by user")

if __name__ == "__main__":
    agent = LiquidityAgent()
    agent.run()