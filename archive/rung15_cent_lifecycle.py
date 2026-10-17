#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""CENT lifecycle: stake -> delegate to tydynrain -> undelegate.
Balance snapshotted IMMEDIATELY before each op (lesson learned)."""
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

def bal_cent():
    b = he("findOne", {"contract":"tokens","table":"balances",
                       "query":{"account":"hermes-sandbox","symbol":"CENT"}}) or {}
    return {k: b.get(k) for k in ("balance","stake","delegationsOut","pendingUndelegations")}

def op(action, payload):
    tx = h.custom_json(id="ssc-mainnet-hive",
        json_data={"contractName":"tokens","contractAction":action,"contractPayload":payload},
        required_auths=["hermes-sandbox"])
    print(f"{action.upper()} TRX: {tx.get('trx_id')}")

# --- fresh snapshot BEFORE composing ops ---
print("pre-op state:", bal_cent())

# 1) stake 1 CENT
op("stake", {"symbol":"CENT","to":"hermes-sandbox","quantity":"1.00000000"})
time.sleep(30)
s = bal_cent(); print("after stake:", s)
assert float(s["stake"]) >= 1.0, "stake did not execute - aborting before delegate"

# 2) delegate 1 CENT to tydynrain
op("delegate", {"to":"tydynrain","symbol":"CENT","quantity":"1.00000000"})
time.sleep(30)
s = bal_cent(); print("after delegate:", s)
assert float(s["delegationsOut"]) >= 1.0, "delegate did not execute - aborting before undelegate"

# 3) undelegate back (7d cooldown -> returns to stake; unstake later per owner plan)
op("undelegate", {"from":"tydynrain","symbol":"CENT","quantity":"1.00000000"})
time.sleep(30)
print("final:", bal_cent())
