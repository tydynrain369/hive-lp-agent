#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Swap remaining SWAP.HIVE to POB. No SWAP.HIVE:POB pool, so route via market buy at ask."""
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

sh = bal("SWAP.HIVE")
# Budget: only 0.0149 left. Pool ratio: 1 POB ≈ 6.887 CENT. With 14.63 CENT we need ~2.03 POB
# but can't afford at 0.01919. Instead buy max affordable POB at the cheapest ask (0.00820001, 41.6 avail):
qty = "1.80000000"
price = "0.00820001"
cost = 1.8 * 0.00820001
print(f"SWAP.HIVE avail: {sh}; buying {qty} POB @ {price} (~{cost:.8f})")
assert cost <= sh, "insufficient SWAP.HIVE"

tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName":"market","contractAction":"buy",
               "contractPayload":{"symbol":"POB","quantity":qty,"price":price}},
    required_auths=["hermes-sandbox"])
print("BUY TRX:", tx.get("trx_id"))
time.sleep(25)
print("POB balance now:", bal("POB"), "| SWAP.HIVE left:", bal("SWAP.HIVE"))
