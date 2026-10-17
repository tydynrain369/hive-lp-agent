#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""
Hive Liquidity Agent — Autonomous DeFi agent for @hermes-sandbox
Manages LP positions, swaps tokens, monitors pool health, grows assets.
"""
import json
import time
import urllib.request
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from nectar import Hive

# ── CONFIG ──────────────────────────────────────────────────────
ACCOUNT = "hermes-sandbox"
KEY_FILE = "/mnt/vault/Hermes-Keys/hermes-sandbox.json"
HE_API = "https://api.hive-engine.com/rpc/contracts"
HIVE_NODE = "https://api.deathwing.me"  # Reliable for HE ops
MIN_LIQUIDITY_USD = 10_000  # Skip pools below this estimated liquidity
MIN_VOLUME_24H = 1_000      # Skip pools below this volume
MAX_SLIPPAGE = "1.0"        # 1% max slippage for swaps/LP
REBALANCE_THRESHOLD = 0.15  # 15% deviation triggers rebalance
# ────────────────────────────────────────────────────────────────

def he(method: str, params: dict) -> list:
    """Call Hive Engine contract API."""
    req = urllib.request.Request(
        HE_API,
        data=json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode(),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp).get("result", [])

def get_balances(account: str) -> Dict[str, float]:
    """Fetch all token balances for account."""
    balances = he("find", {
        "contract": "tokens",
        "table": "balances",
        "query": {"account": account},
        "limit": 200
    })
    return {b["symbol"]: float(b["balance"]) for b in balances}

def get_pools() -> List[dict]:
    """Fetch all liquidity pools."""
    return he("find", {
        "contract": "marketpools",
        "table": "pools",
        "query": {},
        "limit": 1000
    })

def get_lp_positions(account: str) -> List[dict]:
    """Fetch current LP positions for account."""
    return he("find", {
        "contract": "marketpools",
        "table": "liquidityPositions",
        "query": {"account": account},
        "limit": 100
    })

def get_pool_details(token_pair: str) -> Optional[dict]:
    """Get detailed pool info."""
    pools = get_pools()
    for p in pools:
        if p["tokenPair"] == token_pair:
            return p
    return None

@dataclass
class PoolMetrics:
    pair: str
    base_token: str
    quote_token: str
    base_reserve: float
    quote_reserve: float
    base_price: float
    quote_price: float
    total_shares: float
    volume_24h: float = 0.0
    estimated_liquidity_usd: float = 0.0
    my_shares: float = 0.0
    my_share_pct: float = 0.0

    @property
    def ratio(self) -> float:
        return self.base_reserve / self.quote_reserve if self.quote_reserve > 0 else 0

    @property
    def health_score(self) -> float:
        """Composite score: liquidity * volume * (1 / my_exposure_risk)"""
        if self.estimated_liquidity_usd < MIN_LIQUIDITY_USD or self.volume_24h < MIN_VOLUME_24H:
            return 0.0
        exposure_penalty = 1.0 / (1.0 + self.my_share_pct * 10)  # Penalize over-concentration
        return (self.estimated_liquidity_usd / 1000) * (self.volume_24h / 1000) * exposure_penalty

@dataclass
class AgentState:
    balances: Dict[str, float] = field(default_factory=dict)
    pools: List[PoolMetrics] = field(default_factory=list)
    lp_positions: List[dict] = field(default_factory=list)
    hive_client: Hive = None

    def refresh(self):
        self.balances = get_balances(ACCOUNT)
        raw_pools = get_pools()
        self.lp_positions = get_lp_positions(ACCOUNT)

        # Build pool metrics
        my_shares_by_pair = {lp["tokenPair"]: float(lp["shares"]) for lp in self.lp_positions}
        self.pools = []
        for p in raw_pools:
            base, quote = p["tokenPair"].split(":")
            base_r = float(p["baseQuantity"])
            quote_r = float(p["quoteQuantity"])
            total_s = float(p["totalShares"])
            my_s = my_shares_by_pair.get(p["tokenPair"], 0.0)

            # Estimate USD liquidity (rough: assume SWAP.HIVE ≈ $0.30, HBD ≈ $1)
            base_usd = self._token_usd_est(base) * base_r
            quote_usd = self._token_usd_est(quote) * quote_r
            est_liq = base_usd + quote_usd

            # 24h volume from baseVolume
            vol = float(p.get("baseVolume", 0))

            self.pools.append(PoolMetrics(
                pair=p["tokenPair"],
                base_token=base,
                quote_token=quote,
                base_reserve=base_r,
                quote_reserve=quote_r,
                base_price=float(p["basePrice"]),
                quote_price=float(p["quotePrice"]),
                total_shares=total_s,
                volume_24h=vol,
                estimated_liquidity_usd=est_liq,
                my_shares=my_s,
                my_share_pct=(my_s / total_s * 100) if total_s > 0 else 0
            ))

    def _token_usd_est(self, symbol: str) -> float:
        """Rough USD estimates for major tokens."""
        estimates = {
            "SWAP.HIVE": 0.30, "SWAP.HBD": 1.0, "SWAP.BTC": 60000, "SWAP.ETH": 3000,
            "SWAP.USDT": 1.0, "SWAP.BUSD": 1.0, "SWAP.BNB": 500, "SWAP.STEEM": 0.25,
            "SWAP.LTC": 80, "SWAP.DOGE": 0.12, "SWAP.WAX": 0.05, "SWAP.EOS": 0.7,
            "SWAP.BCH": 300, "SWAP.MATIC": 0.8, "SWAP.SOL": 150, "SWAP.AXS": 6,
            "SWAP.SLP": 0.003, "SWAP.GALA": 0.02, "SWAP.STATE": 0.1, "SWAP.SAVVA": 0.01,
            "SWAP.BLURT": 0.01, "SWAP.WAX": 0.05, "HIVE": 0.30, "HBD": 1.0,
            "CENT": 0.01, "POB": 0.05, "BEE": 0.1, "DEC": 0.001, "SPS": 0.03,
            "LEO": 0.1, "PIZZA": 0.001, "GLITTER": 0.0001, "PSX": 0.001,
            "HH": 0.001, "ONEUP": 0.01, "LASSECASH": 0.01, "NEOXAG": 1.0,
            "PAL": 0.01, "SIM": 0.001, "WEED": 0.001, "REVX": 0.01,
            "SEED": 0.01, "WINE": 0.01, "LIST": 0.01, "VOUCHER": 0.01,
            "FARM": 0.01, "MYTHICAL": 0.01, "BXT": 0.01, "SPORTS": 0.01,
            "AFIT": 0.01, "EXOFUEL": 0.01, "DSPN": 0.01, "EWFEPACK": 0.01,
        }
        return estimates.get(symbol, 0.001)

    def print_summary(self):
        print(f"\n{'='*60}")
        print(f"  HERMES LIQUIDITY AGENT — {ACCOUNT}")
        print(f"{'='*60}")
        print(f"\n💰 BALANCES:")
        for sym, bal in sorted(self.balances.items(), key=lambda x: -x[1]):
            if bal > 0:
                usd = bal * self._token_usd_est(sym)
                print(f"   {sym:12s}: {bal:>15.8f}  (~${usd:.2f})")

        print(f"\n📊 LP POSITIONS ({len(self.lp_positions)}):")
        for lp in self.lp_positions:
            p = next((pm for pm in self.pools if pm.pair == lp["tokenPair"]), None)
            if p:
                val = p.my_shares / p.total_shares * (p.base_reserve * self._token_usd_est(p.base_token) + p.quote_reserve * self._token_usd_est(p.quote_token))
                print(f"   {lp['tokenPair']:20s}: {float(lp['shares']):>10.4f} shares ({p.my_share_pct:.4f}%) ~ ${val:.2f}")

        print(f"\n🏆 TOP POOLS BY HEALTH SCORE:")
        scored = [p for p in self.pools if p.health_score > 0]
        scored.sort(key=lambda x: -x.health_score)
        for p in scored[:15]:
            can_enter = self._can_enter_pool(p)
            marker = "✅" if can_enter else "❌"
            print(f"   {marker} {p.pair:20s}  health={p.health_score:.1f}  liq~${p.estimated_liquidity_usd:,.0f}  vol24h={p.volume_24h:,.0f}  my%={p.my_share_pct:.4f}")

    def _can_enter_pool(self, pool: PoolMetrics) -> bool:
        """Check if we have tokens to enter this pool proportionally."""
        have_base = self.balances.get(pool.base_token, 0)
        have_quote = self.balances.get(pool.quote_token, 0)
        need_base = pool.ratio * have_quote if have_quote > 0 else 0
        need_quote = have_base / pool.ratio if have_base > 0 and pool.ratio > 0 else 0
        return (have_base >= need_base * 0.9 and have_quote >= need_quote * 0.9) or \
               (have_base > 0 and have_quote > 0)

# ─── AGENT ACTIONS ──────────────────────────────────────────────

def broadcast_custom_json(hive: Hive, contract: str, action: str, payload: dict) -> dict:
    """Broadcast a custom_json to Hive Engine."""
    return hive.custom_json(
        id="ssc-mainnet-hive",
        json_data={"contractName": contract, "contractAction": action, "contractPayload": payload},
        required_auths=[ACCOUNT]
    )

def swap_tokens(hive: Hive, pair: str, from_symbol: str, amount: float, max_slippage: str = MAX_SLIPPAGE) -> dict:
    """Swap tokens via marketpools."""
    return broadcast_custom_json(hive, "marketpools", "swapTokens", {
        "tokenPair": pair,
        "tokenSymbol": from_symbol,
        "tokenAmount": f"{amount:.8f}",
        "tradeType": "exactInput",
        "maxSlippage": max_slippage
    })

def add_liquidity(hive: Hive, pair: str, base_qty: str, quote_qty: str, max_slippage: str = MAX_SLIPPAGE) -> dict:
    """Add liquidity to a pool."""
    return broadcast_custom_json(hive, "marketpools", "addLiquidity", {
        "tokenPair": pair,
        "baseQuantity": base_qty,
        "quoteQuantity": quote_qty,
        "maxSlippage": max_slippage,
        "maxDeviation": "0"
    })

def remove_liquidity(hive: Hive, pair: str, shares_pct: float) -> dict:
    """Remove liquidity (shares_pct = percentage 0-100)."""
    return broadcast_custom_json(hive, "marketpools", "removeLiquidity", {
        "tokenPair": pair,
        "sharesOut": f"{shares_pct:.3f}"
    })

# ─── MAIN AGENT LOOP ────────────────────────────────────────────

def run_agent_cycle():
    """Single agent cycle: audit → decide → act."""
    print(f"\n{'='*60}")
    print(f"  AGENT CYCLE START — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # Load keys & init client
    acct = json.load(open(KEY_FILE))
    hive = Hive(node=[HIVE_NODE], keys={ACCOUNT: acct["active"]})

    state = AgentState()
    state.hive_client = hive
    state.refresh()
    state.print_summary()

    # ── DECISION LOGIC ─────────────────────────────────────────
    actions_taken = []

    # 1. Check if any LP position needs rebalancing (impermanent loss protection)
    for lp in state.lp_positions:
        pool = next((p for p in state.pools if p.pair == lp["tokenPair"]), None)
        if not pool:
            continue

        # If pool health dropped significantly, consider partial exit
        if pool.health_score == 0 and pool.my_shares > 0:
            print(f"\n⚠️  Pool {pool.pair} health=0 — considering exit")
            # Remove 50% to reduce exposure
            tx = remove_liquidity(hive, pool.pair, 50.0)
            print(f"   EXIT 50%: {tx.get('trx_id')}")
            actions_taken.append(f"exit_50_{pool.pair}")
            time.sleep(15)

    # 2. Find best pool to enter / add to
    # Prioritize pools where we already have position (compound) OR high health pools we can enter
    scored_pools = [p for p in state.pools if p.health_score > 0]
    scored_pools.sort(key=lambda x: -x.health_score)

    for pool in scored_pools[:5]:  # Top 5 candidates
        if pool.my_shares == 0:
            # New entry — check if we have both tokens
            have_base = state.balances.get(pool.base_token, 0)
            have_quote = state.balances.get(pool.quote_token, 0)

            if have_base > 0 and have_quote > 0:
                # Calculate proportional amounts
                ratio = pool.ratio
                max_base_for_quote = ratio * have_quote
                max_quote_for_base = have_base / ratio if ratio > 0 else 0

                if have_base <= max_base_for_quote * 1.01:
                    base_amt = have_base * 0.95  # Use 95% of base
                    quote_amt = base_amt / ratio
                else:
                    quote_amt = have_quote * 0.95
                    base_amt = quote_amt * ratio

                if base_amt > 0.001 and quote_amt > 0.001:
                    print(f"\n➕ ENTERING {pool.pair}: {base_amt:.4f} {pool.base_token} + {quote_amt:.4f} {pool.quote_token}")
                    tx = add_liquidity(hive, pool.pair, f"{base_amt:.8f}", f"{quote_amt:.8f}")
                    print(f"   ADDLIQ: {tx.get('trx_id')}")
                    actions_taken.append(f"enter_{pool.pair}")
                    time.sleep(15)
                    break  # One new entry per cycle

        else:
            # Already in pool — consider compounding rewards if we have spare tokens
            have_base = state.balances.get(pool.base_token, 0)
            have_quote = state.balances.get(pool.quote_token, 0)
            if have_base > 1.0 and have_quote > 0.1:  # Meaningful amounts
                ratio = pool.ratio
                base_amt = min(have_base * 0.5, ratio * have_quote * 0.9)
                quote_amt = base_amt / ratio
                if base_amt > 0.01 and quote_amt > 0.001:
                    print(f"\n🔄 COMPOUNDING {pool.pair}: +{base_amt:.4f} {pool.base_token} +{quote_amt:.4f} {pool.quote_token}")
                    tx = add_liquidity(hive, pool.pair, f"{base_amt:.8f}", f"{quote_amt:.8f}")
                    print(f"   ADDLIQ: {tx.get('trx_id')}")
                    actions_taken.append(f"compound_{pool.pair}")
                    time.sleep(15)
                    break

    # 3. Swap excess single-sided tokens into useful pairs
    # If we have lots of one token but none of its pair partners, swap
    major_tokens = ["SWAP.HIVE", "SWAP.HBD", "CENT", "BEE", "SPS", "LEO", "DEC"]
    for token in major_tokens:
        bal = state.balances.get(token, 0)
        if bal > 10:  # Arbitrary threshold
            # Find best pool for this token
            candidates = [p for p in scored_pools if p.base_token == token or p.quote_token == token]
            if candidates:
                best = candidates[0]
                other = best.quote_token if best.base_token == token else best.base_token
                other_bal = state.balances.get(other, 0)
                if other_bal < 0.1:  # Don't have the pair token
                    print(f"\n🔄 SWAPPING excess {token} ({bal:.4f}) → {other} via {best.pair}")
                    swap_amt = bal * 0.5
                    tx = swap_tokens(hive, best.pair, token, swap_amt)
                    print(f"   SWAP: {tx.get('trx_id')}")
                    actions_taken.append(f"swap_{token}_{other}")
                    time.sleep(15)
                    break

    print(f"\n✅ CYCLE COMPLETE — Actions: {actions_taken or 'none'}")
    return actions_taken

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        print("🤖 Starting daemon mode (Ctrl+C to stop)...")
        while True:
            try:
                run_agent_cycle()
                print("\n😴 Sleeping 300s...")
                time.sleep(300)
            except KeyboardInterrupt:
                print("\n👋 Stopped by user")
                break
            except Exception as e:
                print(f"\n❌ ERROR: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(60)
    else:
        run_agent_cycle()