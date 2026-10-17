#!/home/tydynrain/hive-agent-test/.venv/bin/python
"""Re-add liquidity to CENT:POB — proportional to current pool ratio, capped by holdings."""
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

pool = he("findOne", {"contract":"marketpools","table":"pools","query":{"tokenPair":"CENT:POB"}})
ratio = float(pool["baseQuantity"]) / float(pool["quoteQuantity"])   # CENT per POB
print(f"pool ratio: {ratio:.4f} CENT per POB")

def bal(s):
    r = he("findOne", {"contract":"tokens","table":"balances",
                       "query":{"account":"hermes-sandbox","symbol":s}})
    return float(r["balance"]) if r else 0.0

cent, pob = bal("CENT"), bal("POB")
print(f"holdings: {cent} CENT, {pob} POB")

# POB-limited proportional amounts (leave a little dust)
pob_in = round(pob - 0.001, 8)
cent_needed = pob_in * ratio
if cent_needed > cent - 0.5:
    # CENT-limited instead
    cent_in = round(cent - 0.5, 8)
    pob_in = round(cent_in / ratio, 8)
    cent_needed = cent_in
    print(f"CENT-limited: {cent_in} CENT + {pob_in} POB")
else:
    print(f"POB-limited: {cent_needed:.8f} CENT + {pob_in} POB")

tx = h.custom_json(id="ssc-mainnet-hive",
    json_data={"contractName":"marketpools","contractAction":"addLiquidity",
               "contractPayload":{"tokenPair":"CENT:POB",
                                  "baseQuantity":f"{cent_needed:.8f}",
                                  "quoteQuantity":f"{pob_in:.8f}",
                                  "maxSlippage":"1","maxDeviation":"0"}},
    required_auths=["hermes-sandbox"])
print("ADDLIQ-v2 TRX:", tx.get("trx_id"))
time.sleep(30)

pos = he("find", {"contract":"marketpools","table":"liquidityPositions",
                  "query":{"account":"hermes-sandbox"},"limit":10})
for p in (pos or []):
    print("LP shares now:", p["shares"])
print("CENT:", bal("CENT"), "| POB:", bal("POB"))
