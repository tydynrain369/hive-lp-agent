#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Sell all GLITTER into the 0.0102 standing bid."""
import json, time, urllib.request
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.deathwing.me"], keys={"hermes-sandbox": acct["active"]})

tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName": "market", "contractAction": "sell",
               "contractPayload": {"symbol": "GLITTER", "quantity": "2.92282371",
                                   "price": "0.01020000"}},
    required_auths=["hermes-sandbox"])
print("SELL TRX_ID:", tx.get("trx_id"))

def state():
    req = urllib.request.Request(
        "https://api.hive-engine.com/rpc/contracts",
        data=json.dumps({"jsonrpc":"2.0","method":"findOne","params":{"contract":"tokens","table":"balances","query":{"account":"hermes-sandbox","symbol":"SWAP.HIVE"}},"id":1}).encode(),
        headers={"Content-Type":"application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=25)).get("result")
    return r["balance"] if r else "0"

for wait in (20, 30):
    time.sleep(wait)
    print(f"after ~{wait}s more, SWAP.HIVE:", state())
