#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Swap half the SWAP.HIVE into POB via CENT:POB pool... actually swap SWAP.HIVE->POB needs a SWAP.HIVE:POB pool.

Plan B: use pool swap on an existing pair. Check which pairs let us convert
SWAP.HIVE -> something, then reach CENT/POB. Simpler: pick a pool whose two sides
we can obtain with market buys at sane prices. Let's just do:
  1) swapTokens SWAP.HIVE -> CENT via SWAP.HIVE:CENT pool (half)
  2) swapTokens SWAP.HIVE -> POB via SWAP.HIVE:POB pool (half) if exists,
     else buy POB on market.
Then addLiquidity CENT:POB.
"""
import json, time, urllib.request
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.deathwing.me"], keys={"hermes-sandbox": acct["active"]})

def he(method, params):
    req = urllib.request.Request(
        "https://api.hive-engine.com/rpc/contracts",
        data=json.dumps({"jsonrpc":"2.0","method":method,"params":params,"id":1}).encode(),
        headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req, timeout=25)).get("result")

def bal(symbol):
    r = he("findOne", {"contract":"tokens","table":"balances",
                       "query":{"account":"hermes-sandbox","symbol":symbol}})
    return float(r["balance"]) if r else 0.0

# check pools for SWAP.HIVE:POB
pools = he("find", {"contract":"marketpools","table":"pools","query":{},"limit":1000})
pairmap = {p["tokenPair"]: p for p in pools}
print("has SWAP.HIVE:POB?", "SWAP.HIVE:POB" in pairmap)

swap_hive = bal("SWAP.HIVE")
half = round(swap_hive / 2, 8)
print(f"SWAP.HIVE: {swap_hive}, swapping half ({half}) to CENT via SWAP.HIVE:CENT")

tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName":"marketpools","contractAction":"swapTokens",
               "contractPayload":{"tokenPair":"SWAP.HIVE:CENT","tokenSymbol":"SWAP.HIVE",
                                  "tokenAmount":f"{half:.8f}","tradeType":"exactInput",
                                  "maxSlippage":"0.5"}},
    required_auths=["hermes-sandbox"])
print("CENT-swap TRX:", tx.get("trx_id"))
time.sleep(25)
print("CENT balance now:", bal("CENT"))
