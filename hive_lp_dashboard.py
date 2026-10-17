#!/usr/bin/env python3
"""
Hermes Hive LP Dashboard - Unified view of all liquidity positions, fees, and pool health.
Uses nectarengine for reliable data access.
"""

import json
import time
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, Dict, List
import os

from nectarengine.pool import LiquidityPool
from nectar import Hive

HIVE_RPC = "https://api.deathwing.me"
HE_RPC = "https://api.hive-engine.com/rpc/contracts"

TARGET_POOLS = [
    "ALIVE:HEARTBEAT", "ALIVE:SWAP.HIVE", "BBH:SWAP.HIVE",
    "POB:SWAP.HIVE", "SWAP.HIVE:CENT", "CENT:POB",
    "SWAP.HIVE:BEE", "HSBIDAO:PIXYDUST", "SWAP.HIVE:PHOTO", "SWAP.HIVE:PIZZA"
]

STAKED_TOKENS = ["PHOTO", "PIZZA"]

# ============================================================
# POOL FEES TRACKING (Our stake's accrued fees - from Tribaldex)
# These are MANUAL entries from the Tribaldex UI (requires login)
# Format: {pool_pair: {"token_a_fee": amount, "token_b_fee": amount, "threshold_crossed": bool}}
# Updated: 2026-09-12 from Tribaldex screenshot
# ============================================================
OUR_POOL_FEES = {
    "CENT:POB": {
        "token_a": "CENT", "token_a_fee": 0.00929195,
        "token_b": "POB", "token_b_fee": 0.00127011,
        "threshold_crossed": True
    },
    "POB:SWAP.HIVE": {
        "token_a": "POB", "token_a_fee": 0.0,
        "token_b": "SWAP.HIVE", "token_b_fee": 0.0,
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
    "ALIVE:SWAP.HIVE": {
        "token_a": "ALIVE", "token_a_fee": 0.0,
        "token_b": "SWAP.HIVE", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
    "HSBIDAO:PIXYDUST": {
        "token_a": "HSBIDAO", "token_a_fee": 0.0,
        "token_b": "PIXYDUST", "token_b_fee": 0.0,
        "threshold_crossed": False
    },
}

def rpc_call(method: str, params: dict) -> dict:
    import urllib.request
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(HE_RPC,
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def hive_rpc(method: str, params: list) -> dict:
    import urllib.request
    # Try multiple endpoints for reliability
    endpoints = [
        "https://api.hive.blog",
        "https://api.deathwing.me",
        "https://hive-api.arcange.eu",
    ]
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    
    for endpoint in endpoints:
        try:
            req = urllib.request.Request(endpoint,
                data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
            resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
            return resp if isinstance(resp, list) else resp.get("result", resp)
        except Exception:
            continue
    # All failed - return empty list
    return []


def get_all_pools(lp: LiquidityPool) -> Dict[str, dict]:
    """Fetch all target pools using nectarengine."""
    pool_map = {}
    for pair in TARGET_POOLS:
        try:
            pool = lp.get_pool(pair)
            if pool:
                pool_map[pair] = pool
        except Exception:
            pass
    return pool_map


def get_our_positions(lp: LiquidityPool, account: str = "hermes-sandbox") -> Dict[str, dict]:
    """Get our LP positions using nectarengine."""
    positions = {}
    try:
        raw_positions = lp.get_liquidity_positions(account)
        for pos in raw_positions:
            pair = pos.get("tokenPair", "")
            shares = float(pos.get("shares", 0))
            positions[pair] = {
                "shares": shares,
                "_id": pos.get("_id"),
                "timeFactor": pos.get("timeFactor")
            }
    except Exception as e:
        print(f"Error fetching positions: {e}")
    return positions


def get_token_info(symbol: str) -> Optional[dict]:
    """Get token metadata including precision, staking info."""
    result = rpc_call("find", {
        "contract": "tokens",
        "table": "tokens",
        "query": {"symbol": symbol},
        "limit": 1
    })
    tokens = result.get("result", [])
    return tokens[0] if tokens else None


def get_staking_info(symbol: str, account: str = "hermes-sandbox") -> List[dict]:
    """Get staking info for a token."""
    result = rpc_call("find", {
        "contract": "tokens",
        "table": "stakes",
        "query": {"symbol": symbol, "account": account},
        "limit": 10
    })
    data = result.get("result", [])
    if data:
        return data
    # Try alternative table name
    result2 = rpc_call("find", {
        "contract": "tokens",
        "table": "staking",
        "query": {"symbol": symbol, "account": account},
        "limit": 10
    })
    return result2.get("result", [])


def get_all_pool_shares(lp: LiquidityPool, pool_id: int, limit: int = 50) -> List[dict]:
    """Get ALL liquidity providers by querying liquidityPositions via RPC."""
    # Query the liquidityPositions table with tokenPair
    # We need the token pair to query - pool_id doesn't directly map
    # This will be called with the pool dict containing tokenPair
    return []


def calculate_pool_metrics(pool: dict) -> dict:
    """Calculate derived metrics for a pool from nectarengine pool format."""
    try:
        liq_a = float(pool.get("baseQuantity", 0))
        liq_b = float(pool.get("quoteQuantity", 0))
        vol_a = float(pool.get("baseVolume", 0))
        vol_b = float(pool.get("quoteVolume", 0))
        
        # Calculate fees from volume * 0.25% (standard Hive Engine fee)
        fee_rate = 0.0025
        fee_a_24h = vol_a * fee_rate
        fee_b_24h = vol_b * fee_rate
        
        # APR estimation
        total_liq_usd = liq_a + liq_b  # approximate
        apr = 0.0
        if total_liq_usd > 0:
            daily_fees = fee_a_24h + fee_b_24h
            apr = (daily_fees * 365 / total_liq_usd) * 100

        return {
            "token_a": pool.get("tokenPair", "").split(":")[0] if ":" in pool.get("tokenPair", "") else "",
            "token_b": pool.get("tokenPair", "").split(":")[1] if ":" in pool.get("tokenPair", "") else "",
            "token_pair": pool.get("tokenPair", ""),
            "pool_id": pool.get("_id"),
            "liquidity_a": liq_a,
            "liquidity_b": liq_b,
            "total_liquidity": total_liq_usd,
            "volume_24h_a": vol_a,
            "volume_24h_b": vol_b,
            "total_volume_24h": vol_a + vol_b,
            "fees_24h_a": fee_a_24h,
            "fees_24h_b": fee_b_24h,
            "total_fees_24h": fee_a_24h + fee_b_24h,
            "apr": apr,
            "fee_rate": fee_rate,
            "ratio": liq_a / liq_b if liq_b > 0 else 0,
            "total_shares": float(pool.get("totalShares", 0)),
            "base_price": float(pool.get("basePrice", 0)),
            "quote_price": float(pool.get("quotePrice", 0)),
        }
    except Exception as e:
        return {"error": str(e), "token_pair": pool.get("tokenPair", "")}


def get_position_fees(pool: dict, shares: float, total_shares: float) -> dict:
    """Estimate fees accrued for our position."""
    if total_shares <= 0 or shares <= 0:
        return {"fees_a": 0, "fees_b": 0, "total_fees": 0, "fee_share_pct": 0}

    fee_a_24h = float(pool.get("feeA24h", pool.get("volumeA24h", 0))) * 0.0025
    fee_b_24h = float(pool.get("feeB24h", pool.get("volumeB24h", 0))) * 0.0025
    total_shares_float = float(total_shares)

    our_share_pct = shares / total_shares_float

    return {
        "our_shares": shares,
        "total_shares": total_shares_float,
        "fee_share_pct": our_share_pct * 100,
        "fees_a_24h": fee_a_24h * our_share_pct,
        "fees_b_24h": fee_b_24h * our_share_pct,
        "total_fees_24h": (fee_a_24h + fee_b_24h) * our_share_pct,
    }


def get_pool_distributions(pool_id: int) -> List[dict]:
    """Get active distribution contracts for a pool from the distribution contract."""
    # Query distribution contract batches table
    # Need to map pool_id to tokenPair - done in caller
    return []


def get_position_rewards(pool_id: int, account: str = "hermes-sandbox") -> List[dict]:
    """Get reward distributions for our position in a pool."""
    return []


def get_all_distributions() -> List[dict]:
    """Get all active pool-based distributions from the distribution contract."""
    result = rpc_call("find", {
        "contract": "distribution",
        "table": "batches",
        "query": {"strategy": "pool", "active": True},
        "limit": 500
    })
    batches = result.get("result", [])
    # Filter to only those with numTicksLeft > 0 (actively distributing)
    active = [b for b in batches if b.get("numTicksLeft", 0) > 0]
    return active


def get_distributions_by_tokenpair(token_pair: str) -> List[dict]:
    """Get active distributions for a specific token pair."""
    result = rpc_call("find", {
        "contract": "distribution",
        "table": "batches",
        "query": {"strategy": "pool", "tokenPair": token_pair, "active": True},
        "limit": 100
    })
    batches = result.get("result", [])
    return [b for b in batches if b.get("numTicksLeft", 0) > 0]


def format_number(n: float, decimals: int = 4) -> str:
    """Format number with appropriate precision."""
    if n == 0:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.2f}K"
    if n < 0.0001:
        return f"{n:.8f}"
    return f"{n:.{decimals}f}"


def get_account_history(account: str, limit: int = 100) -> List:
    """Get account history for deployment tracking."""
    return hive_rpc("condenser_api.get_account_history", [account, -1, limit])


# ============================================================
# SECTION 9: REALLOCATION PLANNER
# ============================================================
def plan_reallocation(our_positions, pool_metrics, dist_by_pool):
    """Calculate exit proceeds and optimal reallocation for zombie pools."""
    print("\n" + "=" * 100)
    print("🔄 SECTION 9: REALLOCATION PLANNER")
    print("=" * 100)
    
    # Token importance weights (Tier 1=10, Tier 2=8, Tier 3=5, Tier 4=2, Tier 5=1)
    TOKEN_IMPORTANCE = {
        # Tier 1: Protocol/Infrastructure - Always useful
        "BEE": 10, "WORKERBEE": 10,
        # Tier 2: Tribe Core - Our pools, we control contracts
        "ALIVE": 10, "ALIVEM": 10, "HEARTBEAT": 10,
        # Tier 3: Allied Ecosystems - Supported projects
        "LBI": 8, "PIXYDUST": 8, "UNICOIN": 8, "GLITTER": 8,
        "CENT": 8, "CENTG": 8, "TRADE": 8, "TRIBE": 8, "SHORT": 8,
        "BBH": 8, "BBHM": 8,
        "PEPE": 8, "PPDLR": 8, "PPBT": 8,
        "PIMP": 8, "AEGIS": 8,
        # ATF REMOVED - project ended
        # Tier 4: Other Hive Engine tokens
        "HSBIDAO": 5, "POB": 5, "SWAP.HIVE": 5,
        "PHOTO": 5, "PIZZA": 5, "BBH": 5,
    }
    
    # Target pools for reallocation (Tier 2 + high-yield Tier 3)
    REALLLOC_TARGETS = {
        "ALIVE:SWAP.HIVE": {"tier": 2, "notes": "Core tribe liquidity, ALIVEM/LBI/HEARTBEAT rewards"},
        "ALIVE:HEARTBEAT": {"tier": 2, "notes": "Fees crossed + allied tokens (BBH, PIXYDUST)"},
        "ALIVE:ALIVEM": {"tier": 2, "notes": "Governance stack (CENT, TRADE, TRIBE, PEPE)"},
        "ALIVE:LBI": {"tier": 2, "notes": "Newest, high ally density (PIMP, AEGIS)"},
        "HSBIDAO:PIXYDUST": {"tier": 3, "notes": "1.35K/day rewards, PIXYDUST ecosystem"},
        "CENT:POB": {"tier": 2, "notes": "Dual yield (fees + rewards), CENT ecosystem"},
    }
    
    # Calculate exit proceeds from zombie pools
    zombie_pools = ["SWAP.HIVE:BEE", "POB:SWAP.HIVE"]
    exit_proceeds = {"SWAP.HIVE": 0, "BEE": 0, "POB": 0}
    
    print("\n📤 EXIT PROCEEDS (from zombie pools):")
    print("─" * 100)
    print(f"{'Pool':<25} {'Our Shares':>14} {'Pool Total Shares':>18} {'Our %':>8} {'Token A':>12} {'Token B':>12}")
    print("─" * 100)
    
    for pair in zombie_pools:
        pos = our_positions.get(pair, {})
        our_shares = pos.get("shares", 0)
        metrics = pool_metrics.get(pair, {})
        total_shares = metrics.get("total_shares", 0)
        our_pct = our_shares / total_shares if total_shares > 0 else 0
        
        # Pool token reserves - use liquidity_a/liquidity_b from metrics
        token_a_reserve = metrics.get("liquidity_a", 0)
        token_b_reserve = metrics.get("liquidity_b", 0)
        token_a_sym = metrics.get("token_a", "")
        token_b_sym = metrics.get("token_b", "")
        
        # Our share of reserves
        our_token_a = token_a_reserve * our_pct
        our_token_b = token_b_reserve * our_pct
        
        # Accumulate by token symbol
        if token_a_sym in exit_proceeds:
            exit_proceeds[token_a_sym] += our_token_a
        else:
            exit_proceeds[token_a_sym] = our_token_a
        if token_b_sym in exit_proceeds:
            exit_proceeds[token_b_sym] += our_token_b
        else:
            exit_proceeds[token_b_sym] = our_token_b
        
        print(f"{pair:<25} {format_number(our_shares):>14} {format_number(total_shares):>18} {our_pct*100:>7.2f}% {format_number(our_token_a):>12} {format_number(our_token_b):>12}")
    
    print("─" * 100)
    print("\n💰 TOTAL EXIT PROCEEDS BY TOKEN:")
    total_usd_est = 0
    for sym, amt in exit_proceeds.items():
        if amt > 0:
            importance = TOKEN_IMPORTANCE.get(sym, 1)
            print(f"  {sym:<12}: {format_number(amt):>18}  (importance weight: {importance})")
    
    # Score target pools
    print("\n🎯 REALLOCATION TARGET SCORING:")
    print("─" * 100)
    print(f"{'Pool':<25} {'Tier':>4} {'Daily Rewards':>14} {'Reward Score':>12} {'Fee Score':>10} {'Total Score':>11} Notes")
    print("─" * 100)
    
    pool_scores = []
    for pair, info in REALLLOC_TARGETS.items():
        metrics = pool_metrics.get(pair, {})
        total_shares = metrics.get("total_shares", 0)
        our_shares = our_positions.get(pair, {}).get("shares", 0)
        our_pct = our_shares / total_shares if total_shares > 0 else 0
        
        # Calculate daily rewards
        rewards_per_day = 0
        reward_token_weights = 0
        if pair in dist_by_pool:
            for dist in dist_by_pool[pair]:
                token_balances = dist.get("tokenBalances", [])
                num_ticks_left = dist.get("numTicksLeft", 0)
                for tb in token_balances:
                    quantity = float(tb.get("quantity", 0))
                    est_daily = quantity / num_ticks_left if num_ticks_left > 0 else 0
                    rewards_per_day += est_daily * our_pct
                    # Weight by token importance
                    token_sym = tb.get("symbol", "")
                    reward_token_weights += est_daily * our_pct * TOKEN_IMPORTANCE.get(token_sym, 1)
        
        # Fee score
        fee_data = OUR_POOL_FEES.get(pair, {})
        fee_a = fee_data.get("token_a_fee", 0)
        fee_b = fee_data.get("token_b_fee", 0)
        total_fees = fee_a + fee_b
        fee_score = total_fees * 100  # Scale up
        
        # Combined score: reward token importance + fee proximity
        total_score = reward_token_weights + fee_score + (info["tier"] * 100)
        
        pool_scores.append({
            "pair": pair,
            "tier": info["tier"],
            "rewards_per_day": rewards_per_day,
            "reward_score": reward_token_weights,
            "fee_score": fee_score,
            "total_score": total_score,
            "notes": info["notes"]
        })
        
        print(f"{pair:<25} {info['tier']:>4} {format_number(rewards_per_day):>14} {format_number(reward_token_weights):>12} {format_number(fee_score):>10} {format_number(total_score):>11} {info['notes']}")
    
    # Sort by total score
    pool_scores.sort(key=lambda x: x["total_score"], reverse=True)
    
    print("\n🏆 RANKED REALLOCATION PRIORITY:")
    print("─" * 100)
    for i, ps in enumerate(pool_scores, 1):
        print(f"  {i}. {ps['pair']:<25} Score: {format_number(ps['total_score']):>10}  {ps['notes']}")
    
    # Suggest allocation percentages
    print("\n📊 SUGGESTED ALLOCATION (of exit proceeds):")
    print("─" * 100)
    total_score = sum(p["total_score"] for p in pool_scores)
    for ps in pool_scores:
        pct = (ps["total_score"] / total_score * 100) if total_score > 0 else 0
        print(f"  {ps['pair']:<25}: {pct:.1f}%")
    
    print("\n🔁 NEXT STEPS:")
    print("  1. Remove liquidity from SWAP.HIVE:BEE and POB:SWAP.HIVE")
    print("  2. Swap proceeds to target pool tokens via Hive Swap / Tribaldex")
    print("  3. Add liquidity to top-ranked pools per allocation above")
    print("  4. Re-run dashboard to verify new positions")
    print("=" * 100)


# ============================================================
# SECTION 8: AUTONOMOUS AGENT DECISION LOGIC
# ============================================================
def analyze_pool_decisions(our_positions, pool_metrics, dist_by_pool):
    """Generate actionable recommendations for pool management."""
    print("\n" + "=" * 100)
    print("🤖 SECTION 8: AUTONOMOUS AGENT POOL MANAGEMENT DECISIONS")
    print("=" * 100)
    print(f"{'Pool':<25} {'Status':<12} {'Daily Rewards':>14} {'Accrued Fees':>14} {'Action':<30}")
    print("─" * 100)
    
    for pair in TARGET_POOLS:
        pos = our_positions.get(pair, {})
        our_shares = pos.get("shares", 0)
        
        if our_shares == 0:
            print(f"{pair:<25} {'NO POSITION':<12} {'--':>14} {'--':>14} {'--':<30}")
            continue
           
        metrics = pool_metrics.get(pair, {})
        total_shares = metrics.get("total_shares", 0)
        our_pct = our_shares / total_shares if total_shares > 0 else 0
        
        # Calculate daily rewards
        rewards_per_day = 0
        if pair in dist_by_pool:
            for dist in dist_by_pool[pair]:
                token_balances = dist.get("tokenBalances", [])
                num_ticks_left = dist.get("numTicksLeft", 0)
                for tb in token_balances:
                    quantity = float(tb.get("quantity", 0))
                    est_daily = quantity / num_ticks_left if num_ticks_left > 0 else 0
                    rewards_per_day += est_daily * our_pct
        
        # Get accrued fees
        fee_data = OUR_POOL_FEES.get(pair, {})
        fee_a = fee_data.get("token_a_fee", 0)
        fee_b = fee_data.get("token_b_fee", 0)
        total_fees = fee_a + fee_b
        threshold_crossed = fee_data.get("threshold_crossed", False)
        
        # Decision logic
        if threshold_crossed and rewards_per_day > 0.001:
            action = "✅ HOLD - Claim fees + compound rewards"
            status = "🟢 OPTIMAL"
        elif threshold_crossed and rewards_per_day <= 0.001:
            action = "💰 CLAIM FEES - Evaluate exit if no rewards"
            status = "🟡 FEES ONLY"
        elif not threshold_crossed and rewards_per_day > 0.1:
            action = "📈 HOLD - Strong rewards, fees accruing"
            status = "🟢 REWARDS"
        elif not threshold_crossed and rewards_per_day > 0.001:
            action = "⏳ HOLD - Modest rewards, monitor fees"
            status = "🟡 MODEST"
        elif not threshold_crossed and rewards_per_day > 0:
            action = "👀 MONITOR - Low rewards, no fee threshold"
            status = "🟠 LOW"
        else:
            action = "🚫 CONSIDER EXIT - No yield source"
            status = "🔴 ZOMBIE"
        
        print(f"{pair:<25} {status:<12} {format_number(rewards_per_day):>14} {format_number(total_fees):>14} {action:<30}")
    
    print("─" * 100)
    print("Logic: Combined yield = Liquidity rewards (distribution contracts) + Pool fees (Trading fees accrued)")
    print("Threshold crossed = Fees exceeded payout minimum (claimable)")
    print("=" * 100)


def render_dashboard():
    """Main dashboard renderer."""
    print("\n" + "=" * 100)
    print("🛸 HERMES HIVE LP DASHBOARD".center(100))
    print("=" * 100)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S HST')}")
    print(f"Account: hermes-sandbox")
    print()

    # Initialize nectarengine
    print("📡 Initializing nectarengine...")
    hive = Hive(node=[HIVE_RPC])
    lp = LiquidityPool(blockchain_instance=hive)

    # Fetch all target pools
    print("📡 Fetching pool data...")
    pool_map = get_all_pools(lp)
    print(f"   Found {len(pool_map)}/{len(TARGET_POOLS)} target pools")

    # Get our positions
    print("📊 Fetching our positions...")
    our_positions = get_our_positions(lp)
    print(f"   Found {len(our_positions)} active positions")

    # Get staking info for PHOTO/PIZZA
    staking_status = {}
    for sym in STAKED_TOKENS:
        stakes = get_staking_info(sym)
        staking_status[sym] = stakes[0] if stakes else None

    # Get all distributions
    all_distributions = get_all_distributions()

    # Group distributions by pool (tokenPair) - needed for Sections 3 and 7
    dist_by_pool = defaultdict(list)
    for dist in all_distributions:
        pool_id = dist.get("tokenPair")
        dist_by_pool[pool_id].append(dist)

    # ============================================================
    # SECTION 1: POOL HEALTH OVERVIEW
    # ============================================================
    print("\n" + "─" * 100)
    print("📈 SECTION 1: TARGET POOL HEALTH OVERVIEW")
    print("─" * 100)
    print(f"{'Pool':<25} {'APR':>8} {'Liq A':>12} {'Liq B':>12} {'Vol 24h':>12} {'Fees 24h':>12} {'Ratio':>8} {'Status'}")
    print("─" * 100)

    pool_metrics = {}
    for pair in TARGET_POOLS:
        pool = pool_map.get(pair)
        if not pool:
            print(f"{pair:<25} {'NOT FOUND':>8}")
            continue

        metrics = calculate_pool_metrics(pool)
        pool_metrics[pair] = metrics
        pos = our_positions.get(pair, {})
        has_position = pos.get("shares", 0) > 0

        status = "🟢 ACTIVE" if has_position else "⚪ WATCHING"
        if pair in ["SWAP.HIVE:PHOTO", "SWAP.HIVE:PIZZA"]:
            status = "🔴 STAKED"

        print(f"{metrics['token_pair']:<25} {metrics['apr']:>7.1f}% "
              f"{format_number(metrics['liquidity_a']):>12} {format_number(metrics['liquidity_b']):>12} "
              f"{format_number(metrics['total_volume_24h']):>12} {format_number(metrics['total_fees_24h']):>12} "
              f"{metrics['ratio']:>7.3f}  {status}")

    # ============================================================
    # SECTION 2: OUR POSITIONS & FEE ACCRUAL
    # ============================================================
    print("\n" + "─" * 100)
    print("💰 SECTION 2: OUR POSITIONS & FEE ACCRUAL (The key metric!)")
    print("─" * 100)
    print(f"{'Pool':<25} {'Our Shares':>12} {'Total Shares':>14} {'% of Pool':>10} {'Fees A/24h':>12} {'Fees B/24h':>12} {'Total Fees/24h':>14} {'Crossed Threshold?'}")
    print("─" * 100)

    total_fees_24h = 0
    for pair in TARGET_POOLS:
        pool = pool_map.get(pair)
        pos = our_positions.get(pair, {})
        our_shares = pos.get("shares", 0)
        
        metrics = pool_metrics.get(pair, {})
        total_shares = metrics.get("total_shares", 0)

        if our_shares == 0:
            print(f"{pair:<25} {'--':>12} {'--':>14} {'--':>10} {'--':>12} {'--':>12} {'--':>14} {'--'}")
            continue

        fees = get_position_fees(pool, our_shares, total_shares)
        total_fees_24h += fees["total_fees_24h"]

        # Check if fees > 0.001 (dust threshold)
        threshold_crossed = "✅ YES" if fees["total_fees_24h"] > 0.001 else "⏳ NO"

        token_a, token_b = metrics["token_a"], metrics["token_b"]

        print(f"{pair:<25} {format_number(our_shares):>12} {format_number(total_shares):>14} "
              f"{fees['fee_share_pct']:>9.4f}% {format_number(fees['fees_a_24h']):>12} "
              f"{format_number(fees['fees_b_24h']):>12} {format_number(fees['total_fees_24h']):>14}  {threshold_crossed}")

    print("─" * 100)
    print(f"{'TOTAL FEES/24h:':<25} {'':>12} {'':>14} {'':>10} {'':>12} {'':>12} {format_number(total_fees_24h):>14}")

    # ============================================================
    # SECTION 3: POOL FEES ACCRUED (Our stake - from Tribaldex)
    # Cross-referenced with liquidity rewards for combined yield
    # ============================================================
    print("\n" + "─" * 100)
    print("💎 SECTION 3: POOL FEES ACCRUED (Our stake - from Tribaldex UI)")
    print("─" * 100)
    print(f"{'Pool':<25} {'Fee A':>14} {'Fee B':>14} {'Total Fees':>14} {'Threshold':>12} {'Rewards/Day':>14} {'Combined Yield':>14}")
    print("─" * 100)

    total_combined_yield = 0
    for pair in TARGET_POOLS:
        pool = pool_map.get(pair)
        pos = our_positions.get(pair, {})
        our_shares = pos.get("shares", 0)
        metrics = pool_metrics.get(pair, {})
        total_shares = metrics.get("total_shares", 0)

        # Get accrued fees from Tribaldex data
        fee_data = OUR_POOL_FEES.get(pair, {})
        fee_a = fee_data.get("token_a_fee", 0)
        fee_b = fee_data.get("token_b_fee", 0)
        token_a_name = fee_data.get("token_a", metrics.get("token_a", ""))
        token_b_name = fee_data.get("token_b", metrics.get("token_b", ""))
        threshold = "✅ YES" if fee_data.get("threshold_crossed", False) else "⏳ NO"

        # Get daily rewards for this pool
        our_pct = our_shares / total_shares if total_shares > 0 else 0
        rewards_per_day = 0
        if pair in dist_by_pool:
            for dist in dist_by_pool[pair]:
                token_balances = dist.get("tokenBalances", [])
                num_ticks_left = dist.get("numTicksLeft", 0)
                for tb in token_balances:
                    quantity = float(tb.get("quantity", 0))
                    est_daily = quantity / num_ticks_left if num_ticks_left > 0 else 0
                    rewards_per_day += est_daily * our_pct

        # Combined yield indicator
        total_fees = fee_a + fee_b  # Simplified - different tokens
        combined_yield_signal = "🟢 HIGH" if (rewards_per_day > 0.001 and total_fees > 0.001) else \
                               "🟡 REWARDS" if rewards_per_day > 0.001 else \
                               "🟠 FEES" if total_fees > 0.001 else "🔴 LOW"

        if our_shares > 0:
            print(f"{pair:<25} {format_number(fee_a):>14} {format_number(fee_b):>14} "
                  f"{format_number(total_fees):>14} {threshold:>12} {format_number(rewards_per_day):>14} {combined_yield_signal:>14}")
        else:
            print(f"{pair:<25} {'--':>14} {'--':>14} {'--':>14} {threshold:>12} {'--':>14} {'--':>14}")

    print("─" * 100)
    print("Note: Fee amounts from Tribaldex (requires login). Combined yield = Liquidity rewards + Pool fees accrual.")

    # ============================================================
    # SECTION 4: STAKING STATUS (PHOTO/PIZZA)
    # ============================================================
    print("\n" + "─" * 100)
    print("🔒 SECTION 4: STAKING STATUS (Tokens unavailable for LP)")
    print("─" * 100)

    for sym in STAKED_TOKENS:
        stake = staking_status.get(sym)
        if stake:
            staked = float(stake.get("stake", 0))
            pending = float(stake.get("pendingUnstake", 0))
            cooldown = stake.get("cooldown", "unknown")
            print(f"  {sym}: {format_number(staked)} staked, {format_number(pending)} pending unstake, cooldown: {cooldown} days")
        else:
            print(f"  {sym}: Not staked")

    # ============================================================
    # SECTION 5: RECENT DEPLOYMENT HISTORY
    # ============================================================
    print("\n" + "─" * 100)
    print("📜 SECTION 5: RECENT DEPLOYMENT HISTORY (last 20 txs)")
    print("─" * 100)

    history = get_account_history("hermes-sandbox", 50)
    deployments = []

    for entry in history:
        if isinstance(entry, list) and len(entry) >= 2:
            op = entry[1]
            if isinstance(op, dict) and op.get("op", [""])[0] == "custom_json":
                json_data = op["op"][1].get("json", "")
                if "marketpools" in json_data and "addLiquidity" in json_data:
                    try:
                        payload = json.loads(json_data)
                        cp = payload.get("contractPayload", {})
                        deployments.append({
                            "block": op.get("block", 0),
                            "timestamp": datetime.fromtimestamp(op.get("timestamp", 0)).strftime("%m-%d %H:%M"),
                            "pair": cp.get("tokenPair", "?"),
                            "base": cp.get("baseQuantity", "?"),
                            "quote": cp.get("quoteQuantity", "?"),
                            "max_dev": cp.get("maxDeviation", "?"),
                            "txid": op.get("trx_id", "")[:12] + "...",
                        })
                    except:
                        pass

    print(f"{'Time':<12} {'Block':>10} {'Pool':<20} {'Base':>12} {'Quote':>14} {'MaxDev':>6} {'TxID'}")
    print("─" * 100)
    for d in deployments[:15]:
        print(f"{d['timestamp']:<12} {d['block']:>10} {d['pair']:<20} {d['base']:>12} {d['quote']:>14} {d['max_dev']:>6}  {d['txid']}")

    # ============================================================
    # SECTION 6: LIQUIDITY PROVIDERS (Top holders per pool)
    # ============================================================
    print("\n" + "─" * 100)
    print("👥 SECTION 6: TOP LIQUIDITY PROVIDERS PER POOL")
    print("─" * 100)

    for pair in TARGET_POOLS:
        pool = pool_map.get(pair)
        if not pool:
            continue

        # Query liquidityPositions table with tokenPair
        result = rpc_call("find", {
            "contract": "marketpools",
            "table": "liquidityPositions",
            "query": {"tokenPair": pair},
            "limit": 50,
            "sort": {"shares": -1}
        })
        providers = result.get("result", [])

        if not providers:
            print(f"\n{pair}: No providers found")
            continue

        pos = our_positions.get(pair, {})
        our_shares = pos.get("shares", 0)
        total_shares = pool_metrics.get(pair, {}).get("total_shares", 1)

        print(f"\n{pair} (Total providers: {len(providers)}):")
        print(f"  {'Rank':<5} {'Account':<20} {'Shares':>14} {'% of Pool':>10} {'Us?'}")
        print(f"  {'─'*5} {'─'*20} {'─'*14} {'─'*10} {'─'*5}")

        for i, prov in enumerate(providers, 1):
            account = prov.get("account", "?")
            shares = float(prov.get("shares", 0))
            pct = (shares / total_shares * 100) if total_shares > 0 else 0
            is_us = "← US" if account == "hermes-sandbox" else ""
            print(f"  {i:<5} {account:<20} {format_number(shares):>14} {pct:>9.2f}%  {is_us}")

    # ============================================================
    # SECTION 7: LIQUIDITY REWARDS (Active distribution contracts)
    # ============================================================
    print("\n" + "─" * 100)
    print("🎁 SECTION 7: LIQUIDITY REWARDS (Active distribution contracts)")
    print("─" * 100)

    if all_distributions:
        print(f"\nActive distributions found: {len(all_distributions)}")
        print(f"{'Pool':<25} {'Token':<12} {'Total Remaining':>16} {'Ticks Left':>10} {'Est Daily':>14} {'Active':>6} {'Our Reward/Day':>16}")
        print("─" * 100)

        for pool_pair, dists in dist_by_pool.items():
            our_shares = our_positions.get(pool_pair, {}).get("shares", 0)
            total_shares = pool_metrics.get(pool_pair, {}).get("total_shares", 1)
            our_pct = our_shares / total_shares if total_shares > 0 else 0

            for dist in dists:
                token_balances = dist.get("tokenBalances", [])
                num_ticks_left = dist.get("numTicksLeft", 0)
                active = "YES" if dist.get("active") else "NO"
                creator = dist.get("creator", "?")

                # Sum all reward tokens in this distribution
                for tb in token_balances:
                    token = tb.get("symbol", "?")
                    quantity = float(tb.get("quantity", 0))
                    est_daily = quantity / num_ticks_left if num_ticks_left > 0 else 0
                    our_reward_per_day = est_daily * our_pct

                    print(f"{pool_pair:<25} {token:<12} {format_number(quantity):>16} {num_ticks_left:>10} {format_number(est_daily):>14} {active:>6} {format_number(our_reward_per_day):>16}")

        print("\n--- Pool Aggregate Rewards (all distributions per pool) ---")
        for pool_pair, dists in dist_by_pool.items():
            our_shares = our_positions.get(pool_pair, {}).get("shares", 0)
            total_shares = pool_metrics.get(pool_pair, {}).get("total_shares", 1)
            our_pct = our_shares / total_shares if total_shares > 0 else 0

            total_pool_rewards_day = 0
            our_total_rewards_day = 0

            for dist in dists:
                token_balances = dist.get("tokenBalances", [])
                num_ticks_left = dist.get("numTicksLeft", 0)

                for tb in token_balances:
                    quantity = float(tb.get("quantity", 0))
                    est_daily = quantity / num_ticks_left if num_ticks_left > 0 else 0
                    total_pool_rewards_day += est_daily
                    our_total_rewards_day += est_daily * our_pct

            if total_pool_rewards_day > 0:
                print(f"{pool_pair:<25} Total daily: {format_number(total_pool_rewards_day):>16}  Our share: {format_number(our_total_rewards_day):>16} ({our_pct*100:.2f}%)")

            print(f"  {pool_pair}: Pool total/day: {format_number(total_pool_rewards_day)} | Our share/day: {format_number(our_total_rewards_day)} ({our_pct*100:.4f}%)")
    else:
        print("No active distribution contracts found across target pools.")

    # ============================================================
    # SECTION 7: TOKEN PRECISIONS (for precision compliance)
    # ============================================================
    print("\n" + "─" * 100)
    print("🔢 SECTION 7: TOKEN PRECISIONS (for precision compliance)")
    print("─" * 100)

    all_symbols = set()
    for p in pool_map.values():
        pair = p.get("tokenPair", "")
        if ":" in pair:
            all_symbols.add(pair.split(":")[0])
            all_symbols.add(pair.split(":")[1])

    for sym in sorted(all_symbols):
        info = get_token_info(sym)
        if info:
            precision = info.get("precision", 0)
            stake_enabled = info.get("stakeEnabled", False)
            print(f"  {sym:<12} precision: {precision}  stakeable: {stake_enabled}")

    print("\n" + "=" * 100)
    print("Dashboard complete. Run anytime with: python hive_lp_dashboard.py")
    print("=" * 100)

    return our_positions, pool_metrics, dist_by_pool


if __name__ == "__main__":
    our_positions, pool_metrics, dist_by_pool = render_dashboard()
    analyze_pool_decisions(our_positions, pool_metrics, dist_by_pool)
    plan_reallocation(our_positions, pool_metrics, dist_by_pool)