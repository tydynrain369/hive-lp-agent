#!/usr/bin/env python3
"""Two-step CENT:POB deploy — swap excess CENT to POB, then add_liquidity.

Guardrails:
  - LP_TEST_LIVE=1 required for broadcast; default is dry-run
  - LP_TEST_MAX_USD caps total deploy size (passed 1.30 for this run)
  - Swap step verified by balance delta before add_liquidity is attempted
  - Both steps log through lp_executor_test's _log for forensic replay
"""
from __future__ import annotations
import json, os, sys, time
sys.path.insert(0, '/home/tydynrain/hive-agent-test')

import requests
from liquidity_agent_v2 import LiquidityAgent
from lp_executor_test import TestExecutor, _log, MAX_USD_PER_ACTION, LIVE_MODE

RPC = 'https://api.hive-engine.com/rpc/contracts'
PAIR = 'CENT:POB'
BASE, QUOTE = PAIR.split(':')

def rpc_find(contract, table, query, limit=10):
    r = requests.post(RPC, json={'jsonrpc':'2.0','id':1,'method':'find',
        'params': {'contract': contract, 'table': table, 'query': query, 'limit': limit}}, timeout=30)
    return r.json().get('result') or []

def get_pool():
    return rpc_find('marketpools', 'pools', {'tokenPair': PAIR}, 1)[0]

def get_bal(acct, sym):
    r = rpc_find('tokens', 'balances', {'account': acct, 'symbol': sym}, 1)
    return float(r[0]['balance']) if r else 0.0

def hive_usd_price():
    r = rpc_find('marketpools', 'pools', {'tokenPair': 'SWAP.HIVE:SWAP.HBD'}, 1)[0]
    return float(r['quoteQuantity']) / float(r['baseQuantity'])

def pob_usd_price(pool):
    # POB:SWAP.HIVE pool — base=POB, quote=SWAP.HIVE
    # price_hive = base / quote  (1 POB in HIVE terms is base/quote since pool is deep in POB)
    # Actually: 1 SWAP.HIVE buys (base/quote) POB, so 1 POB = (quote/base) SWAP.HIVE
    r = rpc_find('marketpools', 'pools', {'tokenPair': 'POB:SWAP.HIVE'}, 1)[0]
    base = float(r['baseQuantity'])   # POB
    quote = float(r['quoteQuantity']) # SWAP.HIVE
    pob_hive = quote / base           # 1 POB costs this much SWAP.HIVE
    return pob_hive * hive_usd_price()

def main():
    os.chdir('/home/tydynrain/hive-agent-test')
    agent = LiquidityAgent()
    from liquidity_agent_v2 import VAULT_PATH
    acct = json.load(open(VAULT_PATH))['account']

    pool = get_pool()
    ratio = float(pool['baseQuantity']) / float(pool['quoteQuantity'])
    bal_cent = get_bal(acct, BASE)
    bal_pob = get_bal(acct, QUOTE)
    pob_usd = pob_usd_price(pool)

    print(f"== PRE-STATE ==")
    print(f"  pool {PAIR}: {pool['baseQuantity'][:24]} {BASE} / {pool['quoteQuantity'][:24]} {QUOTE}")
    print(f"  ratio: 1 {QUOTE} = {ratio:.6f} {BASE}")
    print(f"  wallet: {bal_cent:.6f} {BASE} + {bal_pob:.6f} {QUOTE}")
    print(f"  {QUOTE} price: ${pob_usd:.6f}")

    # Optimal split
    V_pob = bal_pob + bal_cent / ratio
    target_pob = V_pob / 2
    target_cent = target_pob * ratio
    swap_cent = max(0.0, bal_cent - target_cent)
    expected_pob_gain = (swap_cent / ratio) * 0.9975  # 0.25% fee
    print(f"\n== PLAN ==")
    print(f"  total value: {V_pob:.6f} {QUOTE} (~${V_pob * pob_usd:.4f})")
    print(f"  target: {target_cent:.6f} {BASE} + {target_pob:.6f} {QUOTE}")
    print(f"  swap: {swap_cent:.6f} {BASE} -> ~{expected_pob_gain:.6f} {QUOTE}")
    print(f"  deploy: all resulting {BASE} + {QUOTE}")
    print(f"  LP_TEST_LIVE={os.environ.get('LP_TEST_LIVE')}  LP_TEST_MAX_USD={os.environ.get('LP_TEST_MAX_USD')}")

    if swap_cent <= 0:
        print("\nNo swap needed; balances already balanced (or CENT is the shortfall).")
        return

    log_entry = {
        'pipeline': 'deploy_cent_pob',
        'pre': {'CENT': bal_cent, 'POB': bal_pob, 'ratio': ratio, 'pob_usd': pob_usd},
        'plan': {'swap_cent': swap_cent, 'expected_pob_gain': expected_pob_gain,
                 'target_cent': target_cent, 'target_pob': target_pob},
        'live': LIVE_MODE, 'cap_usd': MAX_USD_PER_ACTION,
    }

    if not LIVE_MODE:
        log_entry['status'] = 'dry-run'
        _log(log_entry)
        print(f"\nDRY-RUN — not broadcasting. Re-run with LP_TEST_LIVE=1.")
        return

    # ===== STEP 1: SWAP =====
    print("\n== STEP 1: SWAP ==")
    try:
        from nectar import Hive
        from nectarengine.pool import LiquidityPool
        from liquidity_agent_v2 import HIVE_RPC, VAULT_PATH
        creds = json.load(open(VAULT_PATH))
        hive = Hive(node=[HIVE_RPC], keys={creds['account']: creds['active']})
        lp = LiquidityPool(blockchain_instance=hive)

        # Query swap_tokens signature
        import inspect
        sig = inspect.signature(lp.swap_tokens)
        print(f"  swap_tokens signature: {sig}")

        # Correct signature: (account, token_pair, token_symbol, token_amount, trade_type, min_amount_out)
        min_receive = round(expected_pob_gain * 0.95, 8)  # 5% slippage tolerance
        print(f"  swapping {swap_cent:.6f} {BASE} for >= {min_receive} {QUOTE}")
        swap_result = lp.swap_tokens(
            account=creds['account'],
            token_pair=PAIR,
            token_symbol=BASE,
            token_amount=swap_cent,
            trade_type='exactInput',
            min_amount_out=min_receive,
        )
        print(f"  broadcast tx: {swap_result.get('trx_id') if isinstance(swap_result, dict) else swap_result}")
        log_entry['swap_tx'] = swap_result if isinstance(swap_result, dict) else {'raw': str(swap_result)}

        # Wait for settle & verify
        time.sleep(6)
        new_pob = get_bal(acct, QUOTE)
        new_cent = get_bal(acct, BASE)
        gain = new_pob - bal_pob
        spent = bal_cent - new_cent
        print(f"  post-swap: {new_cent:.6f} {BASE} (spent {spent:.6f}) + {new_pob:.6f} {QUOTE} (gained {gain:.6f})")
        log_entry['swap_verify'] = {'spent': spent, 'gained': gain, 'new_cent': new_cent, 'new_pob': new_pob}

        if gain <= 0:
            print("  ERROR: swap did not increase POB — aborting before deploy")
            log_entry['status'] = 'swap-no-effect'
            _log(log_entry)
            return
    except Exception as e:
        log_entry['status'] = 'swap-error'
        log_entry['error'] = f"{type(e).__name__}: {e}"
        _log(log_entry)
        print(f"  SWAP FAILED: {e}")
        raise

    # ===== STEP 2: ADD LIQUIDITY =====
    print("\n== STEP 2: ADD LIQUIDITY ==")
    # Use post-swap balances (rounded down slightly for safety)
    add_cent = round(new_cent * 0.9999, 8)
    add_pob = round(add_cent / ratio, 8)
    est_usd = (add_pob * pob_usd) * 2
    print(f"  adding: {add_cent:.6f} {BASE} + {add_pob:.6f} {QUOTE} (est ${est_usd:.4f})")

    ex = TestExecutor(agent)
    deploy_entry = ex.add_liquidity(PAIR, add_cent, add_pob, est_usd,
                                    reason='deploy CENT:POB after optimal swap')
    print(f"  executor status: {deploy_entry['status']}  note: {deploy_entry['note'][:120]}")
    log_entry['deploy'] = deploy_entry
    log_entry['status'] = 'complete'
    _log(log_entry)

    # ===== FINAL VERIFY =====
    print("\n== POST-STATE (after 6s settle) ==")
    time.sleep(6)
    fin_cent = get_bal(acct, BASE)
    fin_pob = get_bal(acct, QUOTE)
    positions = rpc_find('marketpools', 'liquidityPositions', {'account': acct, 'tokenPair': PAIR}, 1)
    print(f"  {BASE}: {fin_cent:.6f}  {QUOTE}: {fin_pob:.6f}")
    if positions:
        print(f"  {PAIR} LP position shares: {positions[0]['shares']}")
    else:
        print(f"  NO LP POSITION FOUND — unexpected")

if __name__ == '__main__':
    main()
