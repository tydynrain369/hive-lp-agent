#!/usr/bin/env python3
"""
Hermes LP Agent - Cron Entry Point
===================================
Hourly cron job that:
1. Discovers active reward distributions across Hive-Engine pools
2. Evaluates due LP productivity tests (Progressive Liquidity Discovery)
3. Starts new escalated tests using actual wallet dust balances
4. Manages existing LP positions (rebalance, compound, exit)
"""

import os
import json
from datetime import datetime
from decimal import Decimal

# Local imports
from liquidity_agent_v2 import LiquidityAgent, he
from lp_test_tracker import LPTestTracker, measure_lp_test_earnings
from lp_recommendation import generate_recommendation, write_recommendation
from lp_decision import decide, write_decision
from lp_executor_test import execute_from_decision

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")


# ---- Reward Discovery (Progressive Liquidity Discovery) ----
def discover_rewards(pe, portfolio):
    """Scan distribution contract for active pool rewards (the primary reward mechanism).
    Falls back to mining lottery for pools not in distribution contract.
    Returns: {pool_pair: [{"mined_token": symbol, "daily_reward": est, "type": "distribution"}, ...]}"""
    reward_discovery = {}
    try:
        # --- Primary: Distribution contract (pool rewards) ---
        dist = he('find', {'contract': 'distribution', 'table': 'batches',
                          'query': {'active': True, 'strategy': 'pool'}, 'limit': 1000})
        for b in dist:
            pair = b.get('tokenPair', '')
            if not pair:
                continue
            tokens = b.get('tokenBalances', [])
            # Calculate daily reward per token (quantity / numTicks)
            num_ticks = int(b.get('numTicks', 1))
            total_qty = sum(float(t.get('quantity', 0)) for t in tokens)
            daily = total_qty / max(num_ticks, 1)
            reward_tokens = [t['symbol'] for t in tokens]
            reward_discovery[pair] = [{
                'mined_token': t['symbol'],
                'daily_reward': float(t['quantity']) / max(num_ticks, 1),
                'lottery_winners': 0,  # pool distribution, not lottery
                'lottery_interval_hours': 24,
                'token_balance': float(t['quantity']),
                'num_ticks': num_ticks,
                'type': 'distribution'
            } for t in tokens]
        print(f"  DEBUG: distribution contract returned {len(dist)} pools")
        # --- Secondary: Mining contract (lottery rewards) ---
        mining = he('find', {'contract': 'mining', 'table': 'pools', 'query': {}})
        for m in mining:
            if not m.get('active'):
                continue
            external_miners = m.get('externalMiners', '')
            mined_token = m.get('minedToken', '')
            # Only add if not already in reward_discovery
            if external_miners and external_miners not in reward_discovery:
                lottery_winners = int(m.get('lotteryWinners', 0))
                lottery_interval = float(m.get('lotteryIntervalHours', 24))
                lottery_amount = float(m.get('lotteryAmount', 0))
                daily_lotteries = 24 / lottery_interval if lottery_interval > 0 else 0
                daily_reward = lottery_amount * lottery_winners * daily_lotteries
                reward_discovery[external_miners] = [{
                    'mined_token': mined_token,
                    'daily_reward': daily_reward,
                    'lottery_winners': lottery_winners,
                    'lottery_interval_hours': lottery_interval,
                    'lottery_amount': lottery_amount,
                    'type': 'mining'
                }]
        print(f"  DEBUG: total reward_discovery entries: {len(reward_discovery)} pools")
    except Exception as e:
        print(f"  ERROR in reward discovery: {e}")
        import traceback
        traceback.print_exc()
    return reward_discovery


# ---- Configuration ----
LP_TEST_CONFIG = {
    "enabled": True,
    "escalation_factor": 1.5,
    "test_window_hours": 24,
    "max_failures": 4,
    "hold_days": 90,
    "max_concurrent_tests": 2,
    "min_test_usd": 0.01,  # Legacy fallback (essentially disabled with token mode)
    # Candidate pools the tracker is allowed to test (zombies eligible for retest per user)
    "testable_pools": None,  # None = any pool; or explicit set
    # NEW: token-quantity-based test config (overrides USD when present)
    # Format: { "pool_id": {"token_symbol": "BASE", "min_quantity": 0.001, "escalation_factor": 1.5} }
    # These are DUST-LEVEL starting points (sub-1 token). The agent will query actual wallet balance
    "token_test_config": {
        "CENT:POB": {"token_symbol": "CENT", "min_quantity": 0.001, "escalation_factor": 1.5},
        "SWAP.HIVE:CENT": {"token_symbol": "CENT", "min_quantity": 0.001, "escalation_factor": 1.5},
        "SWAP.HIVE:POB": {"token_symbol": "POB", "min_quantity": 0.001, "escalation_factor": 1.5},
        "SWAP.HIVE:BEE": {"token_symbol": "BEE", "min_quantity": 0.001, "escalation_factor": 1.5},
        "ALIVE:SWAP.HIVE": {"token_symbol": "ALIVE", "min_quantity": 0.01, "escalation_factor": 1.5},
        "ALIVE:HEARTBEAT": {"token_symbol": "HEARTBEAT", "min_quantity": 0.1, "escalation_factor": 1.5},
        "BBH:SWAP.HIVE": {"token_symbol": "BBH", "min_quantity": 0.1, "escalation_factor": 1.5},
        "HSBIDAO:PIXYDUST": {"token_symbol": "HSBIDAO", "min_quantity": 0.001, "escalation_factor": 1.5},
        "MEME:PEPE": {"token_symbol": "PEPE", "min_quantity": 0.001, "escalation_factor": 1.5},
    },
    # Pool health: prefer pools with high volume + high liquidity (earn fees + easier exits)
    "min_pool_liquidity_usd": 1000.0,   # min liquidity to consider ($1000+)
    "min_pool_volume_usd": 10.0,        # min 24h volume (if available)
    "min_reward_volume_ratio": 0.5,    # reward $ / volume $ ratio (fees vs. rewards)
}

# Dead projects - only ATF per user
DEAD_PROJECTS = {"ATF"}

# Zombie pools - low liquidity benefit at our capital level (eligible for retest)
ZOMBIE_POOLS = set()  # Populated dynamically


def snapshot_lp_baseline(agent, pool_pair, reward_tokens=None):
    """Capture baseline fees + reward balances before a test deployment."""
    baseline = {"pool_fees_base": None, "pool_fees_quote": None,
                "measurement_status": "unmeasured", "reward_balances": {}}
    # No account-attributed fee source exists here; do not manufacture zeros.
    try:
        agent.portfolio.load_balances()
        for tok in reward_tokens or []:
            b = agent.portfolio.balances.get(tok)
            if b is not None:
                baseline["reward_balances"][tok] = float(getattr(b, "amount", 0) or 0)
    except Exception:
        pass
    return baseline


def run_lp_tests(agent, reward_discovery, cfg):
    """Evaluate due tests; start escalated new ones. Returns number of active tests."""
    print("\n=== LP PRODUCTIVITY TESTS ===")
    tracker = LPTestTracker(config=cfg)
    active_tests = 0

    # 1. evaluate due tests
    due = tracker.due_tests()
    print(f"  Tests due for evaluation: {len(due)}")
    for test in due:
        earned, details = measure_lp_test_earnings(agent, test)
        result = tracker.record_result(test["id"], earned, details)
        if earned is None:
            print(f"  [{test['pool']}] earnings unmeasured; test remains pending")
            continue
        qty = test.get("level_quantity")
        tok = test.get("token_symbol")
        if qty and tok:
            print(f"  [{test['pool']}] test@ {qty:.6f} {tok} → "
                  f"earned={earned:.6f} → {result['outcome']}"
                  + (f" (hold until {result['hold_until']})" if result.get("hold_until") else ""))
        else:
            print(f"  [{test['pool']}] test@${test['level_usd']:.2f} → "
                  f"earned={earned:.6f} → {result['outcome']}"
                  + (f" (hold until {result['hold_until']})" if result.get("hold_until") else ""))

    # 2. count currently active tests
    all_rows = tracker.conn.execute(
        "SELECT pool FROM lp_tests WHERE outcome='testing'").fetchall()
    active_tests = len(all_rows)

    # 3. launch new tests (escalated) for candidate pools
    # candidates: pools with reward distributions, highest potential first
    candidates = []
    for pool_pair, infos in reward_discovery.items():
        if cfg.get("testable_pools") and pool_pair not in cfg["testable_pools"]:
            continue
        st = tracker.pool_state(pool_pair)
        if st["status"] == "testing":
            continue  # already running
        quantity, token_symbol, level_usd = tracker.next_test_quantity(pool_pair, agent.pe)
        if quantity is None and level_usd is None:
            continue  # hold or productive
        candidates.append((pool_pair, quantity, token_symbol, level_usd, infos, st))

    if not candidates:
        print("  No new tests to start")
    else:
        print(f"  Candidate test pools: {len(candidates)}")
        # PRIORITY: Sort candidates by reward $ value descending
        # Score = sum(daily_reward * token_price) over all reward tokens
        def reward_value_usd(infos):
            total = 0.0
            for info in infos:
                p = agent.pe.token_prices.get(info['mined_token'], 0)
                total += info['daily_reward'] * p
            return total
        candidates.sort(key=lambda c: -reward_value_usd(c[4]))
        # Show top 10 by reward value
        print("  Top candidates by reward value:")
        for pool_pair, quantity, token_symbol, level_usd, infos, st in candidates[:10]:
            rv = reward_value_usd(infos)
            print(f"    {pool_pair}: ${rv:.4f}/day rewards=[{', '.join(i['mined_token'] for i in infos)}]")
        for pool_pair, quantity, token_symbol, level_usd, infos, st in candidates:
            if active_tests >= cfg["max_concurrent_tests"]:
                break
            reward_tokens = [i["mined_token"] for i in infos]
            baseline = snapshot_lp_baseline(agent, pool_pair, reward_tokens)
            pool = agent.pe.pools.get(pool_pair)
            if not pool:
                continue

            # ---- DUST-LEVEL LOGIC: Use ACTUAL wallet balance, not hardcoded minimum ----
            # Get the config's min_quantity as a CAP (don't test with more than this)
            # But use whatever is actually available in the wallet
            if token_symbol:
                token_bal = agent.portfolio.balances.get(token_symbol)
                have_qty = float(getattr(token_bal, "balance", 0) or 0) if token_bal else 0
                # Config min_quantity is a CAP; actual test uses available dust (up to cap)
                if have_qty <= 0:
                    print(f"    {pool_pair}: no {token_symbol} balance available, skipping")
                    continue
                # Use available balance, capped at config min_quantity
                actual_test_qty = min(have_qty, quantity) if quantity else have_qty
                if actual_test_qty <= 0:
                    print(f"    {pool_pair}: calculated test quantity is zero, skipping")
                    continue
                # Update quantity to the actual dust amount we'll test with
                quantity = actual_test_qty
            else:
                # Fallback to USD check (legacy)
                base_b = agent.portfolio.balances.get(pool.base)
                quote_b = agent.portfolio.balances.get(pool.quote)
                base_val = float(getattr(base_b, "value_usd", 0) or 0) if base_b else 0
                quote_val = float(getattr(quote_b, "value_usd", 0) or 0) if quote_b else 0
                if base_val + quote_val < cfg["min_test_usd"]:
                    print(f"    {pool_pair}: insufficient tokens for ${level_usd:.2f} test "
                          f"(have ${base_val + quote_val:.2f})")
                    continue
            tid = tracker.start_test(pool_pair, level_usd or 0, level_quantity=quantity, token_symbol=token_symbol, baseline=baseline)
            active_tests += 1
            if quantity and token_symbol:
                cap_qty = tracker.config['token_test_config'].get(pool_pair, {}).get('min_quantity', 'N/A')
                print(f"    ▶ START TEST {pool_pair} at {quantity:.6f} {token_symbol} "
                      f"(wallet={have_qty:.6f}, cap={cap_qty}) "
                      f"(prev fail_streak={st.get('fail_streak', 0)}, rewards={reward_tokens}) → id={tid}")
            else:
                print(f"    ▶ START TEST {pool_pair} at ${level_usd:.2f} "
                      f"(prev fail_streak={st.get('fail_streak', 0)}, rewards={reward_tokens}) → id={tid}")

    # 4. DUST-LEVERAGING: also test pools that pair tokens we actually hold
    #    with high-volume quote currencies (SWAP.HIVE / SWAP.BTC / POB etc.).
    #    This is the "leverage what exists" path that doesn't depend on reward contracts.
    preferred_quote_tokens = {"SWAP.HIVE", "SWAP.BTC", "POB", "CENT", "ALIVE", "BBH", "BEE", "WAIV", "HEARTBEAT"}
    min_price_usd = 0.00001  # ignore effectively zero-priced tokens
    for sym, tb in agent.portfolio.balances.items():
        if active_tests >= cfg["max_concurrent_tests"]:
            break
        if tb.balance <= 0:
            continue
        if tb.price_usd < min_price_usd:
            continue  # dust with no market
        if sym in ("SWAP.HIVE", "SWAP.BTC"):  # skip quote-side anchors
            continue
        # Find candidate pools where this token is base or quote
        for pool_pair, pool in agent.pe.pools.items():
            if active_tests >= cfg["max_concurrent_tests"]:
                break
            if pool_pair in reward_discovery:
                continue  # already handled above
            if pool_pair in current_positions:
                continue  # already LP'd
            # Skip dead projects
            if any(t in DEAD_PROJECTS for t in (pool.base, pool.quote)):
                continue
            # pool must involve our held token on one side and a liquid quote on the other
            side = None
            other = None
            if pool.base == sym and pool.quote in preferred_quote_tokens:
                side, other = pool.base, pool.quote
            elif pool.quote == sym and pool.base in preferred_quote_tokens:
                side, other = pool.quote, pool.base
            else:
                continue
            st = tracker.pool_state(pool_pair)
            if st["status"] == "testing":
                continue
            quantity, token_symbol, level_usd = tracker.next_test_quantity(pool_pair, agent.pe)
            if quantity is None and level_usd is None:
                continue
            # Use held balance as test qty (capped small)
            cap = cfg.get("token_test_config", {}).get(pool_pair, {}).get("min_quantity", 0.5)
            have = float(getattr(agent.portfolio.balances.get(side, None) or 0, "balance", 0) or 0)
            if have <= 0:
                continue
            test_qty = min(have, cap)
            if test_qty <= 0:
                continue
            baseline = snapshot_lp_baseline(agent, pool_pair, [])
            tid = tracker.start_test(pool_pair, level_usd or 0, level_quantity=test_qty, token_symbol=side, baseline=baseline)
            active_tests += 1
            print(f"    ▶ DUST TEST {pool_pair} with {test_qty:.6f} {side} "
                  f"(have={have:.6f}) → id={tid}")

    # 4. SWAP-PATH AWARENESS: Identify pools we could enter by swapping existing dust
    #    Examples: POB → SWAP.HIVE to enter SWAP.HIVE:CENT, or POB → CENT for CENT:POB
    #    We DO NOT pre-define what swaps are possible. We check: for each high-reward pool,
    #    do we have ANY token with balance that could be converted to enter?
    #    This is a scoring/assessment function that the agent uses to PLAN, not execute blindly.
    preferred_transit_tokens = {"SWAP.HIVE", "SWAP.BTC", "POB", "CENT", "ALIVE", "BBH", "BEE", "WAIV", "HEARTBEAT", "PEPE", "MEME", "SWAP.ETH", "SWAP.USDT", "SWAP.HBD", "DEC", "SPS", "LEO", "WAIV", "ONEUP", "PIZZA"}
    for sym, tb in agent.portfolio.balances.items():
        if tb.balance <= 0 or tb.price_usd < 0.00001:
            continue  # No balance or worthless
        # Check all reward pools that DON'T include our token but DO include a preferred quote
        for pool_pair, infos in reward_discovery.items():
            if pool_pair in agent.portfolio.positions:
                continue  # Already LP'd
            base, quote = pool_pair.split(":")
            if base == sym or quote == sym:
                continue  # Already can enter directly
            if base not in preferred_transit_tokens and quote not in preferred_transit_tokens:
                continue  # Needs low-liquidity transit
            # Check if any of our tokens could serve as transit to either side
            if sym in preferred_transit_tokens:
                # We COULD swap sym -> base or sym -> quote -> enter pool
                # But we don't know conversion rates without checking swaps
                # Score this as a "potential entry via swap_path" for logging
                pass  # For now, just log that swap-path analysis is active

    return active_tests
def manage_positions(agent, reward_discovery):
    """Manage LP positions: exit zombies, enter high-reward pools."""
    print("  Scanning positions for management actions...")
    
    # Get zombie pools from agent
    zombie_positions = agent.portfolio.get_zombie_positions() if hasattr(agent.portfolio, 'get_zombie_positions') else []
    zombie_pool_ids = {z['pool_id'] for z in zombie_positions}
    
    actions = []
    
    # 1. Exit zombie pools
    for z in zombie_positions:
        pid = z['pool_id']
        pos = next((p for p in agent.portfolio.positions.values() if p.pool_id == pid), None)
        if pos and pos.shares > 0:
            actions.append({
                'type': 'exit',
                'pair': pid,
                'reason': 'zombie_pool',
                'shares': pos.shares
            })
    
    # 2. Exit positions with no active rewards (if value > $1)
    for pid, pos in agent.portfolio.positions.items():
        if pid in zombie_pool_ids:
            continue
        if pos.value_usd > 1.0:
            # Check if this pool has active rewards
            has_rewards = False
            for reward_token, infos in reward_discovery.items():
                for info in infos:
                    mined = info['mined_token']
                    if f":{mined}" in pid or f"{mined}:" in pid:
                        has_rewards = True
                        break
                if has_rewards:
                    break
            if not has_rewards:
                actions.append({
                    'type': 'exit',
                    'pair': pid,
                    'reason': 'no_active_rewards',
                    'shares': pos.shares
                })
    
    # 3. Enter top reward pools we're not in (max 2 per run)
    entered = 0
    current_positions = set(agent.portfolio.positions.keys())
    
    # Score pools: reward_value + volume_bonus + liquidity_moat
    scored_pools = []
    for pool_pair, infos in reward_discovery.items():
        if pool_pair in current_positions:
            continue
        pool = agent.pe.pools.get(pool_pair)
        if not pool:
            continue
        # Get pool stats: liquidity (depth) + volume (fees)
        # Use PoolData fields: liquidity_usd, volume_24h, fees_24h
        liq_score = pool.liquidity_usd if hasattr(pool, 'liquidity_usd') else 0
        vol_score = pool.volume_24h if hasattr(pool, 'volume_24h') else 0
        fee_apr = pool.apr if hasattr(pool, 'apr') else 0
        
        reward_value = sum(info['daily_reward'] * agent.pe.token_prices.get(info['mined_token'], 0) for info in infos)
        
        # Composite score: reward value * 5 (main), volume * 2 (fees), liquidity * 0.001 (small bonus)
        score = (reward_value * 5 +
                 vol_score * 2 +
                 liq_score * 0.001 +
                 fee_apr * 0.1)          # APR bonus
        
        scored_pools.append({
            'pool': pool_pair,
            'score': score,
            'reward_value': reward_value,
            'reward_tokens': [i['mined_token'] for i in infos],
            'liquidity_score': liq_score,
            'volume_24h': vol_score,
        })
    
    # Sort by composite score
    scored_pools.sort(key=lambda x: x['score'], reverse=True)
    
    # === SWAP-PATH AWARENESS ===
    # For pools we lack tokens for, calculate if a swap of our most valuable asset gets us in.
    # Our most valuable tokens by USD value: POB ($7.97), STFU ($0.0013), HEARTBEAT ($0.30), etc.
    assets_by_value = sorted(
        [(s, b.value_usd, b.balance) for s, b in agent.portfolio.balances.items() if b.balance > 0],
        key=lambda x: x[1], reverse=True
    )
    
    for t in scored_pools:
        if entered >= 2:
            break
        if t['pool'] not in current_positions:
            pool = agent.pe.pools.get(t['pool'])
            if pool:
                base, quote = pool.base, pool.quote
                base_bal = agent.portfolio.balances.get(base)
                quote_bal = agent.portfolio.balances.get(quote)
                base_val = base_bal.value_usd if base_bal else 0
                quote_val = quote_bal.value_usd if quote_bal else 0
                direct_possible = base_val + quote_val >= 1.0
                
                # SWAP-PATH: If we can't enter directly, try via our best assets
                swap_path_note = ''
                if not direct_possible and assets_by_value:
                    best_asset, best_val, best_qty = assets_by_value[0]
                    if best_asset != base and best_asset != quote:
                        # Estimate conversion: best_asset -> base or best_asset -> quote
                        # Simplified: assume half of best asset converts to each side
                        half_val = best_val / 2
                        conv_to_base = half_val / max(agent.pe.token_prices.get(base, float('inf')), 1e-8)
                        conv_to_quote = half_val / max(agent.pe.token_prices.get(quote, float('inf')), 1e-8)
                        conv_base_val = conv_to_base * agent.pe.token_prices.get(base, 0)
                        conv_quote_val = conv_to_quote * agent.pe.token_prices.get(quote, 0)
                        if conv_base_val + conv_quote_val >= 1.0:
                            swap_path_note = f' [via swap {best_asset}→base+quote]'
                            direct_possible = False  # Mark as swap-path entry
                
                if direct_possible or swap_path_note:
                    actions.append({
                        'type': 'enter',
                        'pair': t['pool'],
                        'reason': f'high_reward (daily={t["reward_tokens"][:3]}) score={t["score"]:.0f} liq=${t["liquidity_score"]:.0f} vol=${t["volume_24h"]:.0f}{swap_path_note}',
                        'score': t['score'],
                        'reward_tokens': t['reward_tokens'],
                        'swap_path': bool(swap_path_note),
                    })
                    entered += 1
    
    print(f"  Planned actions: {len(actions)}")
    for a in actions:
        if a['type'] == 'exit':
            print(f"    EXIT {a['pair']}: {a['reason']}")
        else:
            print(f"    ENTER {a['pair']}: {a['reason']} (score: {a['score']:.1f})")
    
    # Execute (dry-run mode for safety)
    print("  [DRY-RUN] Skipping execution - remove dry-run guard to enable")
    # Uncomment below to enable real execution:
    # for action in actions:
    #     if action['type'] == 'exit':
    #         pair = action['pair']
    #         pos = next((p for p in agent.portfolio.positions.values() if p.pool_id == pair), None)
    #         if pos and pos.shares > 0:
    #             try:
    #                 success = agent.executor.remove_liquidity(pos.base, pos.quote, 100.0)
    #                 print(f"    ✓ Removed 100% ({pos.shares:.4f} shares) from {pair}" if success else f"    ✗ Failed to exit {pair}")
    #             except Exception as e:
    #                 print(f"    ✗ ERROR exiting {pair}: {e}")


# ---- Fee evidence snapshots (read-only, longitudinal attribution) ----
FEE_SNAPSHOT_STORE = os.path.expanduser('~/.hermes/lp_tests/fee_snapshots.jsonl')


def record_fee_snapshots(rpc, account, positions, *, store_path=None, at_ms,
                         contract_hash=None):
    """Snapshot+diff held pools via lp_fee_evidence; fail closed, never raise."""
    from lp_fee_evidence import record_fee_snapshots as _impl
    return _impl(rpc, account, positions, store_path=store_path,
                 at_ms=at_ms, contract_hash=contract_hash)


def fee_snapshots_section(agent):
    """Wire live RPC into the collector for every held LP position."""
    lp = getattr(agent.portfolio, 'positions', {}) or {}
    if not lp:
        print("  [FEE] no LP positions; no snapshots taken")
        return None
    try:
        contract = he('getContract', {'name': 'marketpools'})
        code_hash = (contract or {}).get('codeHash')
    except Exception as e:
        print(f"  [FEE] contract lookup failed: {e}; skipping snapshots")
        return None
    def rpc(c, t, q):
        return he('find', {'contract': c, 'table': t, 'query': q, 'limit': 1000})
    account = getattr(agent.portfolio, 'account', 'hermes-sandbox')
    out = record_fee_snapshots(rpc, account, sorted(lp),
                               store_path=FEE_SNAPSHOT_STORE,
                               at_ms=int(datetime.now().timestamp() * 1000),
                               contract_hash=code_hash)
    for pair, r in (out or {}).get('results', {}).items():
        print(f"  [FEE] {pair}: {r.get('status')}"
              + (f" reason={r.get('reason')}" if r.get('reason') else ""))
        d = r.get('diff')
        if d and d.get('status') == 'measured':
            print(f"        attributable fees: {d['attributable_fees']}")
    return out


def main():
    print("=== HERMES LP AGENT CRON RUN ===")
    print(f"Started: {datetime.now().isoformat()}")

    # Initialize agent
    agent = LiquidityAgent()
    agent.pe.load_all()
    agent.portfolio.load_balances()
    agent.portfolio.load_positions()

    total = agent.portfolio.total_value()
    idle = agent.portfolio.idle_value()
    print(f"Portfolio: ${total:.4f} (idle: ${idle:.4f})")
    print(f"Positions: {len(agent.portfolio.positions)}")

    # ---- REWARD DISCOVERY ----
    print("\n=== REWARD DISCOVERY ===")
    reward_discovery = discover_rewards(agent.pe, agent.portfolio)
    print(f"  Discovered {sum(len(v) for v in reward_discovery.values())} active reward distributions")

    # ---- PROGRESSIVE LIQUIDITY DISCOVERY ----
    if LP_TEST_CONFIG.get("enabled"):
        run_lp_tests(agent, reward_discovery, LP_TEST_CONFIG)

    # ---- POSITION MANAGEMENT ----
    print("\n=== POSITION MANAGEMENT ===")
    manage_positions(agent, reward_discovery)

    # ---- FEE EVIDENCE SNAPSHOTS (read-only, longitudinal attribution) ----
    print("\n=== FEE EVIDENCE SNAPSHOTS ===")
    fee_snapshots_section(agent)

    # ---- RECOMMENDATION (read-only advisory; never auto-executes) ----
    print("\n=== RECOMMENDATION ===")
    try:
        held_pools = sorted(agent.portfolio.positions.keys())
        rec = generate_recommendation(held_pools, reward_discovery, price_engine=agent.pe)
        out_path = write_recommendation(rec, REPORTS_DIR)
        for v in rec["verdicts"].values():
            print(f"  {v['pool']:25s} {v['classification']:18s} fees={v['fee_apr_pct']}% rewards={v['reward_token_count']}")
        for r in rec["recommendations"]:
            print(f"  ACTION: exit {r['from_pool']} -> realloc to {r['to_pool']} ({r['confidence']}, {r['urgency']})")
        print(f"  Wrote {out_path}")
    except Exception as e:
        print(f"  Recommendation step failed (non-fatal): {e}")

    # ---- DECISION + GUARDED TEST EXECUTION ----
    # Decision: evidence-driven per-pool verdict (replaces hardcoded zombie lists)
    # Execution: tiny capped test transactions; DRY-RUN unless LP_TEST_LIVE=1
    print("\n=== DECISION ===")
    try:
        decision = decide(held_pools, reward_discovery)
        for pool, v in decision.verdicts.items():
            print(f"  {pool:25s} {v.action:12s} fee_apr={v.fee_apr_pct}% rewards={v.reward_token_count} — {v.reason}")
        if decision.recommended_reallocation:
            r = decision.recommended_reallocation
            print(f"  RECO: {r['from_pool']} -> {r['to_pool']} ({r['rationale']})")
        dpath = write_decision(decision)
        print(f"  Wrote {dpath}")
    except Exception as e:
        print(f"  Decision step failed (non-fatal): {e}")
        decision = None

    print("\n=== TEST EXECUTION (guarded; LP_TEST_LIVE env required for broadcast) ===")
    try:
        if decision is not None:
            actions = execute_from_decision(agent, decision)
            if not actions:
                print("  No action taken this round.")
            for a in actions:
                print(f"  [{a['status']}] {a['action']} {a['pool']} mag={a['magnitude']} — {a['note']}")
        else:
            print("  Skipped (no decision available).")
    except Exception as e:
        print(f"  Test execution failed (non-fatal): {e}")

    print(f"\nCompleted: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
