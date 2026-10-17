#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Test stake via alternate broadcast nodes to isolate HE filtering."""
import json, time, urllib.request
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
NODE = "https://api.deathwing.me"  # alternate L1 node (anyx down, palnet dead)
h = Hive(node=[NODE], keys={"hermes-sandbox": acct["active"]})

tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName": "tokens", "contractAction": "stake",
               "contractPayload": {"symbol": "PSX", "quantity": "0.01"}},
    required_auths=["hermes-sandbox"])
print("broadcast via", NODE, "| TRX_ID:", tx.get("trx_id"))

def balances():
    req = urllib.request.Request(
        "https://api.hive-engine.com/rpc/contracts",
        data=json.dumps({"jsonrpc":"2.0","method":"find","params":{"contract":"tokens","table":"balances","query":{"account":"hermes-sandbox"},"limit":10},"id":1}).encode(),
        headers={"Content-Type":"application/json"})
    return {b["symbol"]: (b["balance"], b["stake"])
            for b in json.load(urllib.request.urlopen(req, timeout=25))["result"]}

for wait in (30, 60):
    time.sleep(wait)
    print(f"after ~{wait}s:", balances())
