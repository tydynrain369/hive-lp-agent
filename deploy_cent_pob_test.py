#!/usr/bin/env python3
"""Deploy CENT:POB — capped test add_liquidity, dry-run first.

Safety:
  - LP_TEST_LIVE=1 required for on-chain broadcast
  - LP_TEST_MAX_USD caps deploy size (default 0.05)
  - Percentage math NOT needed for addLiquidity — it takes absolute amounts
    (they must match the pool's current ratio; we compute both sides from our
    smaller token budget).
"""
import json, os, sys
sys.path.insert(0, '/home/tydynrain/hive-agent-test')
os.environ.setdefault('LP_TEST_LIVE', '0')
os.environ.setdefault('LP_TEST_MAX_USD', '0.05')

import requests
from liquidity_agent_v2 import LiquidityAgent
from lp_executor_test import TestExecutor

POOL = 'CENT:POB'
RPC_CONTRACTS = 'https://api.hive-engine.com/rpc/contracts'

def get_pool(pair):
    r = requests.post(RPC_CONTRACTS, json={'jsonrpc':'2.0','id':1,'method':'find',
        'params': {'contract':'marketpools','table':'pools','query':{'tokenPair':pair},'limit':1}}, timeout=30)
    res = r.json().get('result') or []
    return res[0] if res else None

def get_bal(a, sym):
    r = requests.post(RPC_CONTRACTS, json={'jsonrpc':'2.0','id':1,'method':'find',
        'params': {'contract':'tokens','table':'balances','query':{'account':a,'symbol':sym},'limit':1}}, timeout=30)
    res = r.json().get('result') or []
    return float(res[0]['balance']) if res else 0.0

pool = get_pool(POOL)
base, quote = POOL.split(':')
ratio = float(pool['baseQuantity']) / float(pool['quoteQuantity'])  # CENT per POB
print(f"Pool ratio: 1 {quote} = {ratio:.4f} {base}")

# Load agent to get wallet address
os.chdir('/home/tydynrain/hive-agent-test')
agent = LiquidityAgent()
acct = agent.account_name
bal_cent = get_bal(acct, base)
bal_pob = get_bal(acct, quote)
print(f"Wallet: {bal_cent:.6f} {base}, {bal_pob:.6f} {quote}")

# Price POB in HIVE → USD
r = requests.post(RPC_CONTRACTS, json={'jsonrpc':'2.0','id':1,'method':'find',
    'params': {'contract':'market','table':'metrics','query':{'symbol':quote},'limit':1}}, timeout=30)
pob_hive = float(r.json()['result'][0]['lastPrice'])
r = requests.post(RPC_CONTRACTS, json={'jsonrpc':'2.0','id':1,'method':'find',
    'params': {'contract':'marketpools','table':'pools','query':{'tokenPair':'SWAP.HIVE:SWAP.HBD'},'limit':1}}, timeout=30)
shive_shbd = r.json()['result'][0]
hive_usd = float(shive_shbd['quoteQuantity']) / float(shive_shbd['baseQuantity'])
pob_usd = pob_hive * hive_usd
print(f"POB price: {pob_hive:.6f} HIVE = ${pob_usd:.6f} (HIVE=${hive_usd:.4f})")

# Choose deploy size: cap USD, constrained by both balances
cap_usd = float(os.environ['LP_TEST_MAX_USD'])
# Deploy sizing: use the SMALLER of (cap-converted-to-pob, full POB balance)
pob_by_cap = cap_usd / pob_usd
pob_deploy = min(pob_by_cap, bal_pob)
cent_deploy = pob_deploy * ratio
# Cent sanity: must be <= balance
if cent_deploy > bal_cent:
    # CENT is the limiting side, resize
    cent_deploy = bal_cent
    pob_deploy = cent_deploy / ratio

est_usd = pob_deploy * pob_usd * 2  # 2 sides of pool, roughly equal value
print(f"\nPLANNED DEPLOY:")
print(f"  {pob_deploy:.6f} {quote}")
print(f"  {cent_deploy:.6f} {base}")
print(f"  est ${est_usd:.4f}  (cap ${cap_usd})")
print(f"  live = {os.environ.get('LP_TEST_LIVE')}")

# Execute through guarded executor
ex = TestExecutor(agent)
result = ex.add_liquidity(POOL, cent_deploy, pob_deploy, est_usd,
                          reason=f"test deploy into {POOL}")
print(f"\nEXECUTOR: {json.dumps(result, indent=2, default=str)[:1400]}")
