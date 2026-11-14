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
# 2026-10-10: fallback HIVE price for the reward-per-HIVE term when the
# price map lacks a SWAP.HIVE row (the engine normally prices it; this is
# the belt-and-braces default). Named, not inline, per the standing rule.
# (HIVE_USD_FALLBACK removed 2026-11-13 — Opus P0-10: a fabricated anchor
# price is how a pricing gap becomes a wrong number. Unknown means the
# dependent term deactivates, never a made-up constant.)


# ---- Reward Discovery (Progressive Liquidity Discovery) ----
def discover_rewards(pe, portfolio):
    """Scan distribution contract for active pool rewards (the primary reward mechanism).
    Falls back to mining lottery for pools not in distribution contract.
    Returns: {pool_pair: [{"mined_token": symbol, "daily_reward": est, "type": "distribution"}, ...]}"""
    reward_discovery = {}
    # --- Primary: Distribution contract (pool rewards) ---
    dist = he('find', {'contract': 'distribution', 'table': 'batches',
                      'query': {'active': True, 'strategy': 'pool'}, 'limit': 1000})
    # 2026-10-07 FIX (live bug): the tick length is a contract PARAM, not a
    # batch field. The old code read b.get('tickHours') or
    # b.get('distributionPeriodHours') — fields that DO NOT EXIST on batch
    # rows — so period_hours was always None downstream. lp_reward_math and
    # lp_position_scenario already read the deployed params; mirror them.
    try:
        dp = he('findOne', {'contract': 'distribution', 'table': 'params', 'query': {}}) or {}
    except Exception:
        dp = {}
    dist_tick_hours = None
    try:
        dist_tick_hours = float(dp.get('distTickHours', 0) or 0) or None
    except (TypeError, ValueError):
        dist_tick_hours = None
    for b in dist:
        pair = b.get('tokenPair', '')
        if not pair:
            continue
        # 2026-10-08 FIX (multi-batch bug #1b): exhausted batches
        # (numTicksLeft=0) never pay again — the deployed scheduler
        # consumes ticks, and with none left the remaining tokenBalances
        # are dead budget, not income. Reporting them made exhausted
        # batches look like live reward streams (phantom presence and
        # phantom value). Filter at the source. Per the standing rule:
        # an expired batch coexisting with a live replacement is normal —
        # only the LIVE replacement's entries survive this filter.
        try:
            _ticks_left = int(b.get('numTicksLeft', 0) or 0)
        except (TypeError, ValueError):
            _ticks_left = 0
        # 2026-10-08 FIX (multi-batch bug #1a): ACCUMULATE entries across
        # ALL live batches of a pool instead of overwriting — assignment
        # kept only the LAST batch, so a pool paying through two
        # concurrent contracts silently lost the other's entire income
        # from scoring. Live cases: 36 pools run >1 batch (DVS:DHEDGE:
        # batch 179 with 124 ticks left AND batch 329 with 626 ticks left
        # — whichever processed last erased the other from the decision).
        # exhausted-filter (bug #1b) above: only batches with ticks LEFT
        # produce entries, so accumulation counts live income only.
        # The exhausted-batch skip below this block is retained as a
        # second guard (belt and braces) — see #1b.
        if _ticks_left <= 0:
            continue
        # 2026-10-08: batch-scoped metadata. Fields a pool can differ on
        # across batches (curve, our position age, runway) are PER-BATCH
        # facts; the consumer must never read the last batch's value as
        # the pool's. days_remaining uses the MAX across live batches
        # (most optimistic runway is the honest bound for 'does this
        # pool still pay'), and entries carry their own curve so dilution
        # math stays per-batch.
        tokens = b.get('tokenBalances', [])
        # 2026-09-30: these figures are the ENTIRE batch's tokenBalances
        # divided by numTicks — i.e. the whole distribution spread across all
        # LPs, not our share, and not a per-day figure in a literal sense.
        # Dividing by numTicks converts a 7-day batch into a per-tick amount;
        # the number is therefore "per tick, for the whole pool", which is
        # only equal to a daily figure when numTicks is 1. Tydyn confirmed
        # this reading directly: the values looked like whole-period
        # distribution totals rather than his own earnings. Report the
        # denominator explicitly instead of asserting "per day", and do NOT
        # compare these magnitudes across tokens — deflationary high-supply
        # tokens (e.g. THREE, quoted in billions) are not comparable to
        # low-supply ones without a price map.
        num_ticks = int(b.get('numTicks', 1))
        total_qty = sum(float(t.get('quantity', 0)) for t in tokens)
        # 2026-10-07: tickHours fix — use the contract param, fall back to the
        # declared one-tick-per-day equivalence (Tydyn 2026-10-02), never a
        # nonexistent batch field.
        tick_hours = dist_tick_hours  # contract param, verified source
        # 2026-10-07: carry the age-bonus data the decision layer needs to
        # score top-up dilution (PsyopDaily mechanics, browser-verified
        # 2026-10-07): the batch's bonusCurve plus OUR position's timeFactor.
        # Both are already in hand (batch row; our position row) — no extra
        # network calls. The age bonus applies per BATCH, and one pool can
        # carry multiple active batches, so the curve rides each entry.
        # days_remaining: reward-batch runway (PsyopDaily flags under 14
        # days as 'rewards end soon'). numTicksLeft x tickHours, using the
        # contract param fixed earlier this session.
        our_tf = None
        _pos = portfolio.positions.get(pair)
        if _pos is not None:
            our_tf = float(getattr(_pos, 'time_factor_ms', 0) or 0) or None
        try:
            _ticks_left = int(b.get('numTicksLeft', 0) or 0)
            _days_left = _ticks_left * (tick_hours or 24.0) / 24.0
        except (TypeError, ValueError):
            _days_left = None
        reward_discovery.setdefault(pair, []).extend([{
            'mined_token': t['symbol'],
            'daily_reward': float(t['quantity']) / max(num_ticks, 1),
            'whole_batch_quantity': float(t['quantity']),
            'num_ticks': num_ticks,
            'period_hours': tick_hours,
            'denominator': 'whole batch tokenBalances / numTicks (NOT our share)',
            'units_comparable_across_tokens': False,
            'lottery_winners': 0,  # pool distribution, not lottery
            'lottery_interval_hours': 24,
            'token_balance': float(t['quantity']),
            'type': 'distribution',
            # ---- age-bonus fields (2026-10-07) ----
            'bonus_curve': b.get('bonusCurve'),
            'our_time_factor_ms': our_tf,
            'tick_hours': tick_hours,
            'days_remaining': _days_left,
            # ---- batch-scoped identity (2026-10-08) ----
            'batch_id': str(b.get('_id', '')),
            'batch_ticks_left': _ticks_left,
        } for t in tokens])
    print(f"  Reward landscape: distribution contract returned {len(dist)} pools")
    # --- Secondary: Mining contract (lottery rewards) ---
    mining = he('find', {'contract': 'mining', 'table': 'pools', 'query': {}})
    for m in mining:
        if not m.get('active'):
            continue
        external_miners = m.get('externalMiners', '')
        mined_token = m.get('minedToken', '')
        # 2026-10-08 FIX (multi-stream bug #2): the old
        # `external_miners not in reward_discovery` guard made a pool
        # paying through BOTH the distribution contract AND the mining
        # lottery lose its ENTIRE lottery income the moment any
        # distribution batch existed for it — the two streams are
        # independent income and must both score. Live cases:
        # SWAP.HIVE:PAL and MEME:LOLZ. Accumulate alongside
        # distribution entries instead of skipping.
        if external_miners:
            lottery_winners = int(m.get('lotteryWinners', 0))
            lottery_interval = float(m.get('lotteryIntervalHours', 24))
            lottery_amount = float(m.get('lotteryAmount', 0))
            daily_lotteries = 24 / lottery_interval if lottery_interval > 0 else 0
            daily_reward = lottery_amount * lottery_winners * daily_lotteries
            # 2026-10-08 FIX (multi-stream bug #2b): EXTEND, not assign.
            # The removed skip-guard was accidentally masking a SECOND
            # overwrite bug: mining assignment would have REPLACED the
            # distribution entries of any dual-stream pool. Both
            # streams are independent income; accumulate.
            reward_discovery.setdefault(external_miners, []).extend([{
                'mined_token': mined_token,
                'daily_reward': daily_reward,
                'lottery_winners': lottery_winners,
                'lottery_interval_hours': lottery_interval,
                'lottery_amount': lottery_amount,
                'type': 'mining'
            }])
    print(f"  Reward landscape: {len(reward_discovery)} pools with reward discovery")
    # 2026-10-22: the broad try/except that used to wrap this body SWALLOWED
    # RPC failures into a silently empty landscape — a failed probe is not
    # evidence of absence (pinned: test_rpc_failure_not_empty_landscape).
    # The cron main's own wrapper provides the resilience; failures now
    # propagate to it loudly.
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
            have_qty = 0.0   # 2026-10-22: bound on every path (Pyright)
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
    # 2026-10-22: current_positions was NEVER DEFINED in run_lp_tests — a
    # latent NameError (Pyright caught it; the live cron never reached it
    # only while every candidate pool was already in reward_discovery).
    # Same definition as manage_positions uses.
    current_positions = set(agent.portfolio.positions.keys())
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
    """Position-management pipeline (2026-10-22 wiring of the
    test_position_assessment contract — previously never wired):

      1. pool rows: lp_pool_assessment.assess_pools (per-pool direct-amount
         mechanics)
      2. position assessment: lp_position_assessment.assess_positions via
         PUBLIC reads only (cron's he) — stored on the agent for the
         report pipeline
      3. candidate screen: lp_candidate_screen.screen over fresh pool
         rows. The position assessment is CONDITIONAL scenario evidence:
         it is deliberately NOT passed as economic_evidence to the selector
         (screen-to-selector evidence boundary — conditional reports must
         never silently become economic evidence).

    Failure of the position assessment fails SAFE: decision stays 'observe',
    nothing feeds the selector, and an empty action list returns. The
    screen's selection is ADVISORY — capital motion stays with the
    decision layer and its guard stack, never here.
    """
    import time as _t
    import lp_pool_assessment
    import lp_position_assessment
    import lp_candidate_screen

    print("  Scanning positions for management actions...")
    rows = lp_pool_assessment.assess_pools(agent.pe, agent.portfolio,
                                           reward_discovery)
    try:
        report = lp_position_assessment.assess_positions(
            he, agent.portfolio.account)
    except Exception as e:
        # Fail closed: an unreadable landscape is not permission to act.
        agent.position_assessment = {'decision': 'observe',
                                     'error': str(e),
                                     'transactions_submitted': 0}
        print(f"  Position assessment failed — staying observe: {e}")
        return []
    agent.position_assessment = report
    # --- EVIDENCE PIPELINE (2026-11-03): lp_evidence was dormant.
    # assess_positions already produces {'comparisons': [...]} in exactly the
    # shape lp_evidence.candidates_from_assessment consumes. joint_allocation
    # runs the shared-wallet subset selection over those comparisons and
    # returns per-pool economic evidence, which the candidate screen
    # validates against its own deposit quotes (identity, freshness, gaps).
    # Without this the screen runs with economic_evidence=None and every
    # candidate carries missing_evidence=['common reward-schedule horizon',
    # 'candidate reward entitlement', 'fee/risk bounds', ...] — the selector
    # has nothing to select on.
    economic_evidence = None
    try:
        import lp_evidence
        _ea = lp_evidence.joint_allocation(
            report,
            {s: str(b.balance) for s, b in agent.portfolio.balances.items()},
            captured_at_ms=report.get('captured_at_ms') or int(_t.time() * 1000),
            now_ms=int(_t.time() * 1000),
            max_staleness_ms=7 * 86400 * 1000)
        # choose_joint returns {'selected': [pool_ids...], 'assessments': [...]}.
        # 'selected' is a list of pool-id STRINGS; the per-pool evidence dicts
        # live in 'assessments', where decision == 'candidate'. Iterating the
        # selection dict directly yields its keys and crashes on _c['pool'].
        economic_evidence = {}
        for _a in (_ea.get('selection', {}).get('assessments') or []):
            if _a.get('decision') == 'candidate':
                _pool = _a.get('id')
                if _pool:
                    economic_evidence[_pool] = _a
        _rej = _ea.get('rejected') or []
        print(f"  Evidence: {len(economic_evidence)} pools with joint "
              f"allocation evidence ({len(_rej)} rejected)")
        # A bare rejection COUNT reads as silence: 12 rejected tells you the
        # pipeline is dormant but not why. Surface the dominant causes so the
        # next tick's log is actionable rather than merely honest.
        if _rej:
            from collections import Counter
            for _why, _n in Counter(
                    r.get('reason', '?') for r in _rej).most_common(5):
                print(f"    rejected x{_n}: {_why}")
    except Exception as e:
        # Non-fatal: the screen degrades to evidence-less screening, never
        # the tick dies (2026-10-22: the first live run crashed the whole
        # main through exactly this path).
        print(f"  Evidence pipeline unavailable (non-fatal): "
              f"{type(e).__name__}: {e}")
    try:
        raw_pools = he('find', {'contract': 'marketpools',
                                'table': 'pools', 'query': {},
                                'limit': 1000})
        contract = he('getContract', {'name': 'marketpools'})
        code_hash = (contract.get('codeHash')
                     if isinstance(contract, dict) else None)
    except Exception as e:
        print(f"  Candidate screen inputs unavailable: {e}")
        return []
    # Only pass economic_evidence when the pipeline actually produced
    # something — a None here is still a kwarg, and the screen's evidence
    # validation would then see None for every candidate. Conditional on
    # the value, not the presence of the key.
    _screen_kwargs = dict(rows=rows, raw_pools=raw_pools,
                          precisions=agent.pe.token_precisions,
                          balances=agent.portfolio.balances,
                          positions=agent.portfolio.positions,
                          at_ms=int(_t.time() * 1000),
                          code_hash=code_hash)
    if economic_evidence:
        _screen_kwargs['economic_evidence'] = economic_evidence
    try:
        out = lp_candidate_screen.screen(**_screen_kwargs)
    except Exception as e:
        # The screen is an ASSESSMENT layer — a failure here must degrade to
        # 'no selection', never kill the tick (2026-10-22: the first live run
        # crashed the whole main through exactly this path).
        print(f"  Candidate screen failed (non-fatal): {type(e).__name__}: {e}")
        return []
    return out.get('selection', [])


# 2026-10-22: restored — the manage_positions rewrite's line-range
# replacement accidentally swallowed this module constant (caught by the
# dry-run regression test walking the real main).
FEE_SNAPSHOT_STORE = os.path.expanduser('~/.hermes/lp_tests/fee_snapshots.jsonl')


def record_fee_snapshots(rpc, account, positions, *, store_path=None, at_ms,
                         contract_hash=None):
    """Snapshot+diff held pools via lp_fee_evidence; fail closed, never raise."""
    from lp_fee_evidence import record_fee_snapshots as _impl
    return _impl(rpc, account, positions, store_path=store_path,
                 at_ms=at_ms, contract_hash=contract_hash)


def candidate_watchlist(agent, held_pools, reward_pairs, limit=30):
    """Pools the decision layer might CHOOSE, for fee-evidence pre-measurement.

    2026-10-10 (the honest-limitation fix): fee snapshots ran over held
    positions only, so a candidate pool's fee APR was permanently unknown
    at the moment of choice — the CENT:TRIBE comparison was rewards-only
    because CENT:TRIBE had never been held. This watchlist adds pools we
    COULD deploy into: every pool pairing a wallet token, then the richest
    reward-paying pools, capped at a named limit. Snapshots carry
    scope='candidate' so downstream consumers can distinguish them.

    2026-10-17 (lifecycle gate): candidates now pass the PoolLifecycleFilter
    BEFORE entering the watchlist. "Pools we COULD deploy into" must mean
    deployable — delisted bridge tokens (SWAP.WAX/SWAP.EOS, avoid-listed),
    compromised bridged tokens (SWAP.MATIC etc.), self-issued and unlisted
    tokens were being snapshotted as prospective destinations, burning
    observation cycles on dead assets and priming a future deploy into a
    pool with no real exit. A pool is a candidate only when EVERY token of
    its pair is dividend-eligible. On filter-import failure the cron must
    not die: print loud and proceed unfiltered (same fallback posture as
    decide()'s optional lifecycle wiring).
    """
    # lifecycle gate (2026-10-17)
    _lf = None
    try:
        from lp_lifecycle import PoolLifecycleFilter
        _lf = PoolLifecycleFilter()
    except Exception as _e:
        print(f"  candidate_watchlist: lifecycle filter unavailable "
              f"({type(_e).__name__}) — CANDIDATES NOT LIFECYCLE-GATED")

    def _deployable(pair):
        if _lf is None:
            return True
        for _tok in (t for t in pair.split(":") if t):
            ok, _ = _lf.is_dividend_eligible(_tok, pair)
            if not ok:
                return False
        return True

    _dropped = 0
    def _keep(pair):
        nonlocal _dropped
        if _deployable(pair):
            return True
        _dropped += 1
        return False

    watch = {}
    held = set(held_pools or [])
    # 1. pools pairing any wallet token (what we can deploy into directly)
    wallet_syms = set((getattr(agent.portfolio, "balances", None) or {}).keys()) \
        if hasattr(agent.portfolio, "balances") else set()
    for pair, pool in (getattr(agent.pe, "pools", None) or {}).items():
        if pair in held:
            continue
        b, _, q = pair.partition(":")
        if (b in wallet_syms or q in wallet_syms) and _keep(pair):
            watch[pair] = "wallet-token pool"
    # 2. top reward-paying pools not already held or watched
    def reward_value_usd(entries):
        total = 0.0
        for e in (entries or []):
            if not isinstance(e, dict):
                continue
            qty = e.get("daily_reward")
            sym = e.get("mined_token")
            if qty is None or not sym:
                continue
            px = (getattr(agent.pe, "token_prices", None) or {}).get(sym)
            if px:
                total += qty * px
        return total
    ranked = sorted(((reward_value_usd(v), k) for k, v in (reward_pairs or {}).items()),
                    key=lambda t: -t[0])
    for value, pair in ranked:
        if len(watch) >= limit:
            break
        if pair in held or pair in watch:
            continue
        if not _keep(pair):
            continue
        watch[pair] = f"reward-paying pool (${value:.4f}/tick)"
    if _dropped:
        print(f"  candidate_watchlist: lifecycle gate dropped {_dropped} "
              f"pool(s) (delisted/compromised/self-issued/unlisted token in pair)")
    return dict(list(watch.items())[:limit])


def fee_snapshots_section(agent, extra_pairs=None):
    """Wire live RPC into the collector for every held LP position.

    2026-10-10: extra_pairs (the candidate watchlist) get snapshots too,
    each tagged scope='candidate', so future decisions have fee evidence
    for pools BEFORE they are chosen — closing the 'rewards-only for new
    destinations' limitation.
    """
    lp = getattr(agent.portfolio, 'positions', {}) or {}
    extra = list((extra_pairs or {}).keys())
    if not lp and not extra:
        print("  [FEE] no LP positions and no candidates; no snapshots taken")
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
    # 2026-10-10: held positions first (scope='held'), then the candidate
    # watchlist (scope='candidate') — same store, tagged by scope so fee
    # evidence accumulates for pools BEFORE the decision layer needs it.
    all_pairs = sorted(lp) + [p for p in extra if p not in lp]
    out = record_fee_snapshots(rpc, account, all_pairs,
                               store_path=FEE_SNAPSHOT_STORE,
                               at_ms=int(datetime.now().timestamp() * 1000),
                               contract_hash=code_hash)
    held_set = set(lp)
    for pair, r in (out or {}).get('results', {}).items():
        scope = 'held' if pair in held_set else 'candidate'
        print(f"  [FEE:{scope}] {pair}: {r.get('status')}"
              + (f" reason={r.get('reason')}" if r.get('reason') else ""))
        d = r.get('diff')
        if d and d.get('status') == 'measured':
            # 2026-09-30: this used to print `attributable_fees` as {'CENT':'0',
            # 'POB':'0'}, which read as "we earned nothing" and looked like a
            # measurement. It was reserve-delta mislabelled as fees, and the
            # underlying values included NEGATIVE fee legs. The reconciler no
            # longer claims fee income; print what it actually has and point at
            # the volume-based measurement as the real source.
            print("        fee basis: " + str(d.get('fee_basis', 'unknown')))
            rm = d.get('reserve_movement') or {}
            if rm:
                parts = []
                for sym, mv in rm.items():
                    try:
                        v = float(mv['our_share_of_delta'])
                        parts.append(f"{sym}={v:+.4g}")
                    except (TypeError, ValueError):
                        parts.append(f"{sym}=?")
                print("        net reserve movement (NOT fees): " + ", ".join(parts))
    return out


def main():
    print("=== HERMES LP AGENT CRON RUN ===")
    print(f"Started: {datetime.now().isoformat()}")

    # Initialize agent
    agent = LiquidityAgent()

    # 2026-10-02: tell the PriceEngine which symbols to attempt MARKET pricing
    # for even though they appear in no pool. Reward tokens and wallet holdings
    # are exactly the pool-less set — BBHO, FRESA, HOP, IDD, WRD, POKEMON,
    # BEATCZ, PEPECASH, SWAP.PEPE, SWAP.PEPET all read 0 without this, which is
    # what made ALIVE:HEARTBEAT and ALIVE:SWAP.HIVE look dividend-free. They
    # cannot be discovered from pool ids, so the caller must supply them.
    # SCOPED, NOT GLOBAL (2026-10-02): the first attempt supplied every symbol
    # from every active distribution (~200), which meant ~200 sequential HTTP
    # round-trips inside a one-shot load_all(). The run was cut short partway,
    # so the tokens that motivated the change were never priced while the
    # summary still read "198 filled" — an aggregate that hid a total miss on
    # the specific set. Only OUR held pools' reward tokens plus wallet holdings
    # can affect a decision about OUR portfolio, so that is all we ask for.
    _cands = set()
    _needed = set()
    try:
        # Positions are NOT loaded yet at this point in the sequence, so
        # agent.portfolio.positions is empty here. That silently emptied the
        # distribution filter and left the candidate set wallet-only — which is
        # why BEATCZ/PEPECASH/FRESA never got priced. Take the held pools
        # directly from the portfolio loader instead.
        try:
            agent.portfolio.load_positions()
            _needed = set(str(k) for k in (agent.portfolio.positions or {}).keys())
        except Exception:
            _needed = set()
        _dist = he('find', {'contract': 'distribution', 'table': 'batches',
                            'query': {'active': True, 'strategy': 'pool'},
                            'limit': 1000})
        for _b in (_dist or []):
            if _b.get('tokenPair') in _needed:
                for _t in (_b.get('tokenBalances') or []):
                    _sym = _t.get('symbol')
                    if _sym:
                        _cands.add(_sym)
    except Exception:
        pass
    try:
        _bal = he('find', {'contract': 'tokens', 'table': 'balances',
                           'query': {'account': 'hermes-sandbox'}, 'limit': 200})
        for _b in (_bal or []):
            _sym = _b.get('symbol')
            if _sym:
                _cands.add(_sym)
    except Exception:
        pass
    # Pool pair tokens too — they are what we would deposit into.
    for _pid in list(agent.pe.pools.keys())[:0]:
        pass
    # Which pools we actually hold — the PriceEngine bounds market pricing to
    # these, so we make a handful of HTTP calls instead of 213.
    _held = sorted(_needed) or list((agent.portfolio.positions or {}).keys())
    agent.pe.held_pools = set(_held)
    agent.pe.extra_price_candidates = _cands
    # Remember what we ASKED for so a partial fill can be named, not hidden.
    agent.pe.requested_price_candidates = set(_cands)
    print(f"  Market pricing scoped to {len(_cands)} symbols for held pools: "
          f"{', '.join(sorted(_held))}")

    agent.pe.load_all()
    agent.portfolio.load_balances()
    agent.portfolio.load_positions()

    total = agent.portfolio.total_value()
    idle = agent.portfolio.idle_value()
    # total_value is None when any position is UNPRICED — an unknown is not
    # a zero and must not be formatted as one
    if total is None:
        print("Portfolio: value UNKNOWN (unpriced positions: "
              f"{agent.portfolio.valuation_summary()['unpriced_positions']}); "
              f"idle: ${idle:.4f}")
    else:
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
    # 2026-10-10: candidates get snapshots too — pre-measure fee evidence
    # for pools the decision might choose, so their fee APR is not unknown
    # at the moment of choice (the CENT:TRIBE rewards-only limitation).
    try:
        _held_for_watch = sorted(agent.portfolio.positions.keys())
        _watch = candidate_watchlist(agent, _held_for_watch, reward_discovery)
        if _watch:
            print(f"  candidate watchlist: {len(_watch)} pools "
                  f"({', '.join(list(_watch)[:8])}{'…' if len(_watch) > 8 else ''})")
    except Exception as _e:
        _watch = {}
        print(f"  candidate watchlist unavailable ({type(_e).__name__}) — held-only snapshots")
    fee_snapshots_section(agent, extra_pairs=_watch)

    # ---- RECOMMENDATION (read-only advisory; never auto-executes) ----
    print("\n=== RECOMMENDATION ===")
    # 2026-10-22: pre-bound on every path — if this step fails (non-fatal),
    # the DECISION section below reads rec/held_pools and a NameError there
    # would degrade the entire decision, masked as another soft failure.
    rec = None
    held_pools = []
    try:
        held_pools = sorted(agent.portfolio.positions.keys())
        # 2026-09-30: pass pool+position so the report can show OUR
        # entitlement, not just the whole-pool distribution. Without this
        # the agent says a pool is "reward-productive" while we earn
        # nothing visible, which is exactly the gap Tydyn spotted.
        position_lookup = {}
        for pid in held_pools:
            pos = agent.portfolio.positions.get(pid)
            if pos is None:
                continue
            b, q = pid.split(":", 1)
            pool = agent.pe.get_pool(b, q)
            if pool is not None:
                position_lookup[pid] = (pool, pos)
        rec = generate_recommendation(held_pools, reward_discovery,
                                     position_lookup=position_lookup,
                                     price_map=agent.pe.token_prices)
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
        # 2026-09-30: feed the realized economics and threshold distance into the
        # decision layer, otherwise every position defaults to "hold" forever
        # and the agent can never act on a concentration strategy.
        # 2026-10-22: rec is pre-bound (None on recommendation failure) —
        # guard the decision inputs instead of NameError-ing into the
        # decision try's soft-catch and silently degrading the whole verdict.
        eco_map = {}
        th_map = {}
        if rec:
            eco_map = {p: v.get("position_economics")
                       for p, v in rec["verdicts"].items()
                       if v.get("position_economics")}
            th_map = {p: v.get("threshold_analysis")
                      for p, v in rec["verdicts"].items()
                      if v.get("threshold_analysis")}
        # 2026-10-02: bid/ask spreads so a dividend paid in unsellable tokens
        # cannot score as income. Derived from the liquidity verdicts the
        # PriceEngine already gathered, so no extra network cost.
        _spread_map = {sym: (m["spread_pct"] / 100.0)
                       for sym, m in (agent.pe.market_liquidity or {}).items()
                       if m.get("spread_pct") is not None}
        # 2026-10-07: exit-liquidity map for the destination-safety gate —
        # scoped to exactly the symbols the decision consumes: held pools'
        # pair tokens plus their reward tokens (the same candidate set the
        # spread gate uses). None depth never gates (unknown ≠ zero).
        try:
            _exit_syms = set()
            for _pid in held_pools:
                _b, _q = _pid.split(":", 1)
                _exit_syms.update({_b, _q})
                for _r in (reward_discovery.get(_pid) or []):
                    if isinstance(_r, dict) and _r.get("mined_token"):
                        _exit_syms.add(_r["mined_token"])
            _exit_map = agent.pe.exit_liquidity(sorted(_exit_syms))
            _below_floor = [s for s, v in _exit_map.items()
                            if v is not None and v < 0.5]
            if _below_floor:
                print(f"  Exit-liquidity: {len(_below_floor)} token(s) under the "
                      f"0.5 HIVE floor: {', '.join(_below_floor)}")
        except Exception as _e:
            _exit_map = None
            print(f"  Exit-liquidity lookup failed ({type(_e).__name__}) — gate not applied")
        # 2026-10-10: pool TVL in HIVE for the reward-per-HIVE scoring term
        # (Tydyn's CENT question: rank destinations by what a NEW HIVE
        # deposited there would capture per day). Built from the same
        # PriceEngine pool data the decision already consumes — no extra
        # network cost. Pool TVL in HIVE = liquidity_usd / HIVE price.
        try:
            # P0-10 (Opus round 3): no fabricated HIVE price. When the
            # anchor pool is missing, token_prices carries no SWAP.HIVE and
            # the reward-per-HIVE term deactivates (empty TVL map) instead
            # of valuing TVL at a made-up rate.
            _hive_usd_px = agent.pe.token_prices.get("SWAP.HIVE")
            if _hive_usd_px is None:
                print("  HIVE price UNKNOWN this tick — reward-per-HIVE term inactive")
            _tvl_map = {}
            for _pid, _pool in (agent.pe.pools or {}).items():
                if _pool.liquidity_usd and _hive_usd_px:
                    _tvl_map[_pid] = _pool.liquidity_usd / _hive_usd_px
        except Exception:
            _tvl_map = None
            print("  Pool-TVL map unavailable — reward-per-HIVE term inactive this run")
        decision = decide(held_pools, reward_discovery,
                          position_economics=eco_map,
                          threshold_analysis=th_map,
                          fee_evidence_override={
                              p: {"fee_apr_pct_authoritative": v.get("fee_apr_pct")}
                              for p, v in rec["verdicts"].items()
                              if v.get("fee_apr_pct") is not None
                          },
                          # 2026-10-02: without a price map the decision layer
                          # cannot value a distribution, so every pool would read
                          # dividend_usd=None and the new dividend scoring would
                          # silently never fire.
                          price_map=agent.pe.token_prices,
                          spread_map=_spread_map,
                          exit_liquidity_map=_exit_map,
                          pool_tvl_map=_tvl_map)
        for pool, v in decision.verdicts.items():
            print(f"  {pool:25s} {v.action:12s} fee_apr={v.fee_apr_pct}% rewards={v.reward_token_count} — {v.reason}")
        if decision.recommended_reallocation:
            r = decision.recommended_reallocation
            print(f"  RECO: {r['from_pool']} -> {r['to_pool']} ({r['rationale']})")
        dpath = write_decision(decision)
        print(f"  Wrote {dpath}")

        # --- ADVISORY ADJUDICATION (2026-10-02) ------------------------------
        # Strictly read-only and non-blocking. The LLM reviews the decision
        # AFTER it is written and can change nothing. It never sees keys, never
        # produces an amount, and its verdict is stored in a sidecar file.
        # Any failure here is swallowed: advisory must never break the agent.
        try:
            import lp_advisory
            rec_verdicts = {}
            try:
                import glob as _glob, os as _os
                _reps = sorted(_glob.glob("/home/tydynrain/.hermes/scripts/reports/"
                                         "recommendation-*.json"))
                if _reps:
                    rec_verdicts = (json.load(open(_reps[-1])) or {}).get(
                        "verdicts", {}) or {}
            except Exception:
                rec_verdicts = {}

            adv = lp_advisory.adjudicate(decision.to_dict(), rec_verdicts)
            sidecar = dpath.replace(".json", ".advisory.json")
            # P1-5 (Opus round 3): the sidecar carries the decision_id from
            # the written artifact, so the executor can verify it belongs to
            # THIS decision. Atomic write, same discipline as the decision.
            _d_written = {}
            try:
                with open(dpath) as _dfh:
                    _d_written = json.load(_dfh)
            except Exception:
                _d_written = {}
            _stmp = sidecar + ".tmp"
            with open(_stmp, "w") as fh:
                json.dump({
                    "decision_id": _d_written.get("decision_id"),
                    "adjudicated_decision": dpath,
                    "verdict": adv.get("verdict"),
                    "reason": adv.get("reason"),
                    "ok": adv.get("ok"),
                    "ms": adv.get("ms"),
                    "model": adv.get("model"),
                    "possible_quantity_leak": adv.get("possible_quantity_leak"),
                    "authority": "ADVISORY ONLY — no effect on the decision",
                }, fh, indent=2)
            os.replace(_stmp, sidecar)
            flag = "  <-- REVIEW" if adv.get("verdict") == "CHALLENGE" else ""
            print(f"  ADVISORY: {adv.get('verdict')} ({adv.get('ms')}ms){flag}")
            if adv.get("verdict") == "CHALLENGE":
                print(f"    {adv.get('reason','')[:180]}")
            if adv.get("possible_quantity_leak"):
                print("    *** advisory emitted a quantity — boundary violation, ignored ***")
        except Exception as e:
            print(f"  Advisory step skipped (non-fatal): {type(e).__name__}")
    except Exception as e:
        print(f"  Decision step failed (non-fatal): {e}")
        decision = None
        dpath = None   # P1-5: the spawn sites below must never see an
                       # unbound or half-written decision path

    print("\n=== TEST EXECUTION (guarded; LP_TEST_LIVE env required for broadcast) ===")
    _acted = False
    try:
        if decision is not None:
            actions = execute_from_decision(agent, decision)
            # 2026-11-13 (Claude review P0-2): one action per run is a HARD
            # invariant. If the exit leg broadcast, the executor subprocess
            # below must NOT run — two capped removals in one tick is still
            # two removals.
            # 2026-11-13 (Opus review, A3): "error" includes exceptions raised
            # AFTER the node accepted the transaction (BroadcastResendRefused,
            # timeout, lost reply) and "rejected" is equally ambiguous — in
            # both, running the executor subprocess would be a SECOND removal
            # in one tick. Anything that is not an explicitly safe no-op
            # counts as acted.
            _acted = any(a.get("status") not in ("dry-run", "skipped")
                         for a in actions)
            if not actions:
                print("  No exit action this round.")
            for a in actions:
                print(f"  [{a['status']}] {a['action']} {a['pool']} mag={a['magnitude']} — {a['note']}")
        else:
            print("  Skipped (no decision available).")
        # --- 2026-10-17 THE AUTONOMY EXPAND (Tydyn's switch) ---
        # Removals proved themselves live (Leg A, TX 33250382);
        # the expand path now follows: when live mode is on and the
        # decision recommends a reallocation, invoke the proven
        # Leg-A realloc executor (LP_TEST_LIVE=1 + --execute, the
        # $0.05 hard cap + route-cost ceiling + refusal gates all
        # stack inside it). Dry-run nights print the plan only.
        _live = os.environ.get("LP_TEST_LIVE", "") == "1"
        _rec = getattr(decision, "recommended_reallocation", None) \
            if decision is not None else None
        _exp = getattr(decision, "expand_candidate", None) \
            if decision is not None else None
        import subprocess as _sp, sys as _sys
        _rc_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "lp_realloc_executor.py")
        if _live and _acted:
            print("  [AUTONOMY] exit leg already broadcast this run — "
                  "one action per run; executor subprocess skipped.")
        elif _live and _rec:
            # 2026-10-17: the reallocation branch — an exit source
            # exists, run the proven Leg-A executor (remove+stage).
            print("  [AUTONOMY] reallocation recommended — invoking "
                  "the executor (capped, live):")
            _argv = [_sys.executable, _rc_path, "--execute"]
            if dpath:
                # P1-5 (Opus round 3): the executor acts on the decision
                # artifact THIS tick wrote — never a re-derived one.
                _argv += ["--decision", dpath]
            _r = _sp.run(_argv, capture_output=True, text=True, timeout=600)
            for _ln in (_r.stdout or "").splitlines():
                print(f"    {_ln}")
            if _r.returncode != 0:
                print(f"    [executor rc={_r.returncode}]")
                print((_r.stderr or "").strip()[-600:])
        elif _live and _exp and not _rec:
            # 2026-10-17: the FROM-IDLE deploy — no exit source, but
            # the decision wants to expand into _exp. Deploy fresh
            # idle SWAP.HIVE through the proven from-idle path
            # (2026-10-17 08:20 HST, first live run: swap legs +
            # add_liquidity all confirmed). The executor's own
            # 6-hour throttle (IDLE_DEPLOY_MIN_INTERVAL_HOURS) keeps
            # this from dust-adding in a loop; the $0.05 cap + 1%
            # route ceiling + advisory verdict + validated-price
            # sizing all stack inside.
            print("  [AUTONOMY] expand verdict with idle capital — "
                  "invoking the from-idle deploy (capped, live):")
            _argv = [_sys.executable, _rc_path,
                     "--deploy-idle", "--execute"]
            if dpath:
                _argv += ["--decision", dpath]
            _r = _sp.run(_argv, capture_output=True, text=True, timeout=600)
            for _ln in (_r.stdout or "").splitlines():
                print(f"    {_ln}")
            if _r.returncode != 0:
                print(f"    [executor rc={_r.returncode}]")
                print((_r.stderr or "").strip()[-600:])
        elif _exp and not _live:
            print("  [AUTONOMY OFF] expand verdict stands "
                  f"({_exp}) — dry-run, no deploy.")
    except Exception as e:
        print(f"  Test execution failed (non-fatal): {e}")

    print(f"\nCompleted: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
