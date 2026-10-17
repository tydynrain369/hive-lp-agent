#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""POB full lifecycle: stake -> delegate to tydynrain -> undelegate."""
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

def show():
    b = he("findOne", {"contract":"tokens","table":"balances",
                       "query":{"account":"hermes-sandbox","symbol":"POB"}}) or {}
    print("sandbox POB:", {k: b.get(k) for k in ("balance","stake","delegationsOut","pendingUndelegations")})
    t = he("findOne", {"contract":"tokens","table":"balances",
                       "query":{"account":"tydynrain","symbol":"POB"}})
    if t: print("tydynrain delegationsIn:", t.get("delegationsIn"))

def op(action, payload):
    tx = h.custom_json(id="ssc-mainnet-hive",
        json_data={"contractName":"tokens","contractAction":action,"contractPayload":payload},
        required_auths=["hermes-sandbox"])
    print(f"{action.upper()} TRX:", tx.get("trx_id"))
    time.sleep(30)

# 1) stake 0.5 of our 0.907
op("stake", {"symbol":"POB","to":"hermes-sandbox","quantity":"0.50000000"})
show()

# 2) delegate the staked 0.5 to tydynrain
op("delegate", {"to":"tydynrain","symbol":"POB","quantity":"0.40000000"})
show()

# 3) undelegate back (7-day cooldown before it returns to our stake)
op("undelegate", {"from":"tydynrain","symbol":"POB","quantity":"0.40000000"})
show()
