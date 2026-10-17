#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""
Hive Liquidity Agent v2 — Autonomous DeFi agent for @hermes-sandbox
Full price discovery, pool health scoring, autonomous execution.
"""
import json
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
    value_usd: float = 0.0

@dataclass
class LPPosition:
    pool_id: str
    base: str
    quote: str
    shares: float
    base_deposited: float
    quote_deposited: float
    value_usd: float = 0.0
    apr: float = 0.0

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
    base_volume: float = 0.0
    quote_volume: float = 0.0
    total_shares: float = 0.0
    base_quantity: float = 0.0
    quote_quantity: float = 0.0

# ─── Price Engine ───────────────────────────────────────────────────
class PriceEngine:
    def __init__(self):
        self.pools: Dict[str, PoolData] = {}
        self.token_prices: Dict[str, float] = {}
        self.token_precisions: Dict[str, int] = {}  # symbol -> precision
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
        """Round amount to token's precision."""
        prec = self.token_precisions.get(symbol, 8)
        factor = 10 ** prec
        return round(amount * factor) / factor

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
                    base_volume=float(p.get("baseVolume", 0)),
                    quote_volume=float(p.get("quoteVolume", 0)),
                    total_shares=float(p.get("totalShares", 0)),
                    base_quantity=base_reserve,
                    quote_quantity=quote_reserve
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
            for pool in self.pools.values():
                base, quote = pool.base, pool.quote
                rB, rQ = pool.base_reserve, pool.quote_reserve
                if rB <= 0 or rQ <= 0:
                    continue
                b_known = self.token_prices.get(base, 0)
                q_known = self.token_prices.get(quote, 0)
                
                # derive quote from base
                if b_known > 0 and q_known == 0:
                    # base_price = quote_per_base (e.g., SWAP.HIVE per CENT)
                    # But safer to derive from reserves: quote_per_base = rQ / rB
                    implied_q_per_b = rQ / rB
                    # quote_usd = implied_q_per_b * base_usd  ... NO.
                    # Actually: rB * base_usd ≈ rQ * quote_usd (balanced pool)
                    # So quote_usd ≈ (rB * base_usd) / rQ
                    quote_usd = (rB * b_known) / rQ
                    if quote_usd > 0:
                        self.token_prices[quote] = quote_usd
                        changed_any = True
                
                # derive base from quote
                elif q_known > 0 and b_known == 0:
                    base_usd = (rQ * q_known) / rB
                    if base_usd > 0:
                        self.token_prices[base] = base_usd
                        changed_any = True
            
            return changed_any
        
        # Iterate until no more prices can be derived (max 10 rounds)
        for _ in range(10):
            if not _derive_prices():
                break
        
        # ===== TRUTH OVERRIDE: real traded prices from market.metrics =====
        # BFS-derived prices come from AMM reserve ratios, which are meaningless
        # for thin/unbalanced pools (a pool saying 1 CENT = 0.254 POB while real
        # trades clear at 0.001 HIVE). lastPrice is what trades actually clear at.
        # NOTE: queried via $in chunks over OUR token set — a full-table query
        # with limit 1000 silently misses symbols beyond the first 1000 rows.
        try:
            our_tokens = [s for s, p in self.token_prices.items()]
            overridden = 0
            self.token_volumes_24h: Dict[str, float] = {}
            for i in range(0, len(our_tokens), 100):
                chunk = our_tokens[i:i+100]
                metrics = he("find", {
                    "contract": "market", "table": "metrics",
                    "query": {"symbol": {"$in": chunk}}, "limit": 1000
                })
                for m in (metrics or []):
                    sym = m.get("symbol", "")
                    lp_hive = float(m.get("lastPrice", 0))
                    vol_hive = float(m.get("volume", 0))
                    if vol_hive > 0:
                        self.token_volumes_24h[sym] = vol_hive * hive_usd
                    if lp_hive > 0:
                        new_price = lp_hive * hive_usd
                        old_price = self.token_prices.get(sym, 0)
                        # lastPrice is the real traded price — trust it over
                        # reserve-ratio derivations. Do NOT ratchet with min():
                        # a one-way min() would keep stale low prices as markets
                        # recover upward. The anchor tokens keep their pool anchor.
                        if sym in ("SWAP.HIVE", "HIVE", "SWAP.HBD", "HBD"):
                            self.token_prices[sym] = new_price
                        else:
                            self.token_prices[sym] = new_price
                        if old_price > 0 and abs(old_price - new_price) / max(new_price, 1e-9) > 0.05:
                            overridden += 1
            if overridden:
                print(f"  PriceEngine: {overridden} prices corrected from market.metrics (reserve ratios were >5% off)")
        except Exception as e:
            print(f"  PriceEngine: market.metrics override skipped ({e})")
        
        # ===== UNIT-CORRECT USD RECOMPUTATION =====
        # base_reserve * base_price yields QUOTE UNITS (not USD) — the original
        # formula mixed units and inflated thin-pool liquidity ~5,000x. Revalue
        # every flow (reserves, volume, fees, APR) in its own USD price.
        # NOTE: poolVolume fields (baseVolume/quoteVolume) are CUMULATIVE
        # all-time, NOT 24h — so pool.volume_24h here is an all-time estimate,
        # and pool.apr from it is meaningless (verified 2026-09-20: produced
        # 158,065% APRs). Real 24h APR uses the min(token_volumes_24h)
        # estimator in score_pool; pool.volume_24h is kept for reference only.
        for pool in self.pools.values():
            b_usd = self.token_prices.get(pool.base, 0)
            q_usd = self.token_prices.get(pool.quote, 0)
            pool.liquidity_usd = pool.base_reserve * b_usd + pool.quote_reserve * q_usd
            # Cumulative all-time volume, unit-corrected (reference only)
            pool.volume_24h = pool.base_volume * b_usd + pool.quote_volume * q_usd
            pool.fees_24h = pool.volume_24h * 0.003
            pool.apr = 0.0  # computed in score_pool from real 24h volumes
        
        # Sanity clamp: reject prices that are clearly wrong
        # Only clamp if price is absurdly high (>$1M) or negative
        for sym in list(self.token_prices.keys()):
            p = self.token_prices[sym]
            if p > 1_000_000 or p < 0:
                print(f"  PriceEngine: clamped {sym} from ${p:.8f} to 0 (out of range)")
                self.token_prices[sym] = 0
        
        # Report how many stayed unknown
        unknown = [s for s, p in self.token_prices.items() if p == 0]
        if unknown:
            print(f"  PriceEngine: {len(self.pools)} pools, {len(self.token_prices)} tokens priced, {len(unknown)} unknown")
        else:
            print(f"  PriceEngine: {len(self.pools)} pools, {len(self.token_prices)} tokens priced")
        
        self._loaded = True

    def get_pool(self, base: str, quote: str) -> Optional[PoolData]:
        return self.pools.get(f"{base}:{quote}") or self.pools.get(f"{quote}:{base}")

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

    def load_balances(self):
        raw = he("find", {
            "contract": "tokens", "table": "balances", 
            "query": {"account": "hermes-sandbox"}, "limit": 200
        })
        for b in raw:
            sym = b.get("symbol", "")
            bal = float(b.get("balance", 0))
            staked = float(b.get("stake", 0))
            price = self.pe.token_prices.get(sym, 0)
            self.balances[sym] = TokenBalance(sym, bal, staked, price, (bal + staked) * price)

    def load_positions(self):
        """Load LP positions using nectarengine's get_liquidity_positions."""
        from nectarengine.pool import LiquidityPool
        from nectar import Hive
        import json
        
        acct = json.load(open(VAULT_PATH))
        hive = Hive(node=[HIVE_RPC], keys={acct["account"]: acct["active"]})
        lp = LiquidityPool(blockchain_instance=hive)
        
        try:
            positions = lp.get_liquidity_positions(acct["account"])
            for p in positions:
                try:
                    token_pair = p.get("tokenPair", "")
                    if ":" not in token_pair:
                        continue
                    base, quote = token_pair.split(":", 1)
                    shares = float(p.get("shares", 0))
                    
                    # Get deposited amounts from pool data or estimate from shares
                    pool = self.pe.get_pool(base, quote)
                    base_dep = 0.0
                    quote_dep = 0.0
                    if pool and pool.total_shares > 0:
                        base_dep = shares / pool.total_shares * pool.base_quantity
                        quote_dep = shares / pool.total_shares * pool.quote_quantity
                    
                    val = 0.0
                    apr = 0.0
                    if pool:
                        # Unit-correct: value each side at its own USD price
                        b_usd = self.pe.token_prices.get(base, 0)
                        q_usd = self.pe.token_prices.get(quote, 0)
                        val = (base_dep * b_usd) + (quote_dep * q_usd)
                        apr = pool.apr
                    
                    pid = f"{base}:{quote}"
                    self.positions[pid] = LPPosition(pid, base, quote, shares, base_dep, quote_dep, val, apr)
                except Exception as e:
                    print(f"  Warning: Failed to parse position {p}: {e}")
                    continue
        except Exception as e:
            print(f"  Warning: Failed to load positions: {e}")

    def total_value(self) -> float:
        return sum(tb.value_usd for tb in self.balances.values()) + sum(p.value_usd for p in self.positions.values())

    def lp_value(self) -> float:
        return sum(p.value_usd for p in self.positions.values())

    def idle_value(self) -> float:
        return sum(tb.value_usd for tb in self.balances.values() if tb.value_usd > 0.0001)

# ─── Pool Analyzer ──────────────────────────────────────────────────
class PoolAnalyzer:
    def __init__(self, price_engine: PriceEngine, portfolio: PortfolioManager):
        self.pe = price_engine
        self.portfolio = portfolio

    def score_pool(self, pool: PoolData) -> dict:
        """Score pool 0-1 for LP attractiveness."""
        liq = pool.liquidity_usd
        # Conservative 24h volume estimate: pool turnover moves BOTH tokens,
        # so pool volume <= min(token volumes) — never overestimates.
        vol_a = getattr(self.pe, 'token_volumes_24h', {}).get(pool.base, 0)
        vol_b = getattr(self.pe, 'token_volumes_24h', {}).get(pool.quote, 0)
        vol_24h = min(vol_a, vol_b)
        vol_liq = vol_24h / liq if liq > 0 else 0
        # Real 24h APR: fees from conservative volume estimate / liquidity
        apr = (vol_24h * 0.003 / liq) * 365 * 100 if liq > 0 else 0.0
        
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
                from nectar import Hive
                acct = json.load(open(VAULT_PATH))
                hive = Hive(node=[HIVE_RPC], keys={acct["account"]: acct["active"]})
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
        from nectar import Hive
        acct = json.load(open(VAULT_PATH))
        hive = Hive(node=[HIVE_RPC], keys={acct["account"]: acct["active"]})
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
        from nectar import Hive
        self.hive = Hive(node=[HIVE_RPC], keys={self.acct["account"]: self.acct["active"]})

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
        self.executor = ExecutionEngine(self.pe, self.portfolio)
        self.strategy = StrategyEngine(self.pe, self.portfolio, self.analyzer, self.executor)
        self.reallocator = ReallocationPlanner(self.pe, self.portfolio, self.analyzer)

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