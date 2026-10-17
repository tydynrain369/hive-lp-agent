#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Remove half our LP shares from CENT:POB."""
import json, time, urllib.request
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.deathwing.me"], keys={"hermes-sandbox": acct["active"]})

# We hold 4.10873089251530394167 shares; remove 2.0
tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName":"marketpools","contractAction":"removeLiquidity",
               "contractPayload":{"tokenPair":"CENT:POB","shares":"2.0"}},
    required_auths=["hermes-sandbox"])
print("REMOVELIQ TRX:", tx.get("trx_id"))
time.sleep(30)

def he(method, params):
    req = urllib.request.Request(
        "https://api.hive-engine.com/rpc/contracts",
        data=json.dumps({"jsonrpc":"2.0","method":method,"params":params,"id":1}).encode(),
        headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req, timeout=25)).get("result")

pos = he("find", {"contract":"marketpools","table":"liquidityPositions",
                  "query":{"account":"hermes-sandbox"},"limit":10})
for p in (pos or []):
    print("remaining shares:", p["shares"])
for s in ("CENT","POB"):
    r = he("findOne", {"contract":"tokens","table":"balances",
                       "query":{"account":"hermes-sandbox","symbol":s}})
    print(s, "balance:", r["balance"] if r else 0)
