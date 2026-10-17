#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Rung 5b: stake 0.01 PSX (single broadcast, then verify)."""
import json, time
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.hive.blog", "https://rpc.ecency.com"],
         keys={"hermes-sandbox": acct["active"]})

tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName": "tokens", "contractAction": "stake",
               "contractPayload": {"symbol": "PSX", "quantity": "0.01"}},
    required_auths=["hermes-sandbox"])
print("TRX_ID:", tx.get("trx_id"))

# single delayed verification (no retries)
time.sleep(15)
import urllib.request
req = urllib.request.Request(
    "https://api.hive-engine.com/rpc/contracts",
    data=json.dumps({"jsonrpc":"2.0","method":"find","params":{"contract":"tokens","table":"balances","query":{"account":"hermes-sandbox"},"limit":10},"id":1}).encode(),
    headers={"Content-Type":"application/json"})
for b in json.load(urllib.request.urlopen(req, timeout=25))["result"]:
    print(b["symbol"], "| balance:", b["balance"], "| stake:", b["stake"])
