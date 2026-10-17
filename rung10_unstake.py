#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Unstake PSX (0.005 of the 0.010 staked) — tokens contract unstake action."""
import json, time, urllib.request
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.deathwing.me"], keys={"hermes-sandbox": acct["active"]})

tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName":"tokens","contractAction":"unstake",
               "contractPayload":{"symbol":"PSX","quantity":"0.005"}},
    required_auths=["hermes-sandbox"])
print("UNSTAKE TRX:", tx.get("trx_id"))
time.sleep(25)

def he(symbol):
    req = urllib.request.Request(
        "https://api.hive-engine.com/rpc/contracts",
        data=json.dumps({"jsonrpc":"2.0","method":"findOne","params":{"contract":"tokens","table":"balances","query":{"account":"hermes-sandbox","symbol":symbol}},"id":1}).encode(),
        headers={"Content-Type":"application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=25)).get("result")
    return (r["balance"], r["stake"], r.get("pendingUnstake")) if r else None

print("PSX (balance, stake, pendingUnstake):", he("PSX"))
