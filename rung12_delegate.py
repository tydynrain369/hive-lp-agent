#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Delegate 0.005 staked PSX to tydynrain, then undelegate it (full round-trip)."""
import json, time, urllib.request, sys
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.deathwing.me"], keys={"hermes-sandbox": acct["active"]})

def he(method, params):
    req = urllib.request.Request(
        "https://api.hive-engine.com/rpc/contracts",
        data=json.dumps({"jsonrpc":"2.0","method":method,"params":params,"id":1}).encode(),
        headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req, timeout=25)).get("result")

def show():
    b = he("findOne", {"contract":"tokens","table":"balances",
                       "query":{"account":"hermes-sandbox","symbol":"PSX"}})
    print("sandbox PSX:", {k: b[k] for k in ("balance","stake","delegationsOut","pendingUndelegations") if k in b})
    t = he("findOne", {"contract":"tokens","table":"balances",
                       "query":{"account":"tydynrain","symbol":"PSX"}})
    if t: print("tydynrain delegationsIn:", t.get("delegationsIn"))

# 1) DELEGATE
tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName":"tokens","contractAction":"delegate",
               "contractPayload":{"to":"tydynrain","symbol":"PSX","quantity":"0.005"}},
    required_auths=["hermes-sandbox"])
print("DELEGATE TRX:", tx.get("trx_id"))
time.sleep(30)
show()

# 2) UNDELEGATE
tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName":"tokens","contractAction":"undelegate",
               "contractPayload":{"from":"tydynrain","symbol":"PSX","quantity":"0.005"}},
    required_auths=["hermes-sandbox"])
print("UNDELEGATE TRX:", tx.get("trx_id"))
time.sleep(30)
show()
