#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Add liquidity to CENT:POB pool, proportional to pool ratio (~6.887 CENT per POB)."""
import json, time, urllib.request
from nectar import Hive

acct = json.load(open('/vault/Hermes-Keys/hermes-sandbox.json'))
h = Hive(node=["https://api.deathwing.me"], keys={"hermes-sandbox": acct["active"]})

# We hold 14.62614777 CENT and 1.8 POB. Pool ratio: 1443623.43/209570.66 = 6.8875 CENT per POB.
# Proportional max: 1.8 * 6.8875 = 12.3975 CENT. Use 12.39 CENT + 1.8 POB.
base_q = "12.39000000"   # CENT (quote side in this pair naming? verify below)
quote_q = "1.80000000"   # POB
tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName":"marketpools","contractAction":"addLiquidity",
               "contractPayload":{"tokenPair":"CENT:POB","baseQuantity":base_q,
                                  "quoteQuantity":quote_q,"maxSlippage":"1","maxDeviation":"0"}},
    required_auths=["hermes-sandbox"])
print("ADDLIQ TRX:", tx.get("trx_id"))
time.sleep(30)

def he(method, params):
    req = urllib.request.Request(
        "https://api.hive-engine.com/rpc/contracts",
        data=json.dumps({"jsonrpc":"2.0","method":method,"params":params,"id":1}).encode(),
        headers={"Content-Type":"application/json"})
    return json.load(urllib.request.urlopen(req, timeout=25)).get("result")

pos = he("find", {"contract":"marketpools","table":"liquidityPositions",
                  "query":{"account":"hermes-sandbox"},"limit":10})
print("LP positions:", json.dumps(pos, indent=1)[:500])
for s in ("CENT","POB"):
    r = he("findOne", {"contract":"tokens","table":"balances",
                       "query":{"account":"hermes-sandbox","symbol":s}})
    print(s, "balance now:", float(r["balance"]) if r else 0)
