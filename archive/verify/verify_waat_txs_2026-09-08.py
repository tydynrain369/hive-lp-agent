#!/usr/bin/env python3
"""Direct TX verification for 2026-09-08 14:15 HST WAAT run, via nodes that support get_transaction,
plus recent account history as an independent confirmation."""
import json
import urllib.request

TX_NODES = [
    "https://rpc.ecency.com",
    "https://api.deathwing.me",
    "https://hive-api.arcange.eu",
    "https://api.hive.blog",
]

TXIDS = {
    "7e8d7efa75db1421": "transfer youarealive -> aliveandthriving (0.246 HIVE)",
    "6dae294058b3245b": "transfer_to_vesting (power-up 0.412 HIVE)",
    "aacc71415788a6bf": "delegate_vesting_shares to bradleyarrow",
}


def rpc(url, method, params, timeout=25):
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    if "error" in resp:
        raise RuntimeError(resp["error"].get("message", "?"))
    return resp["result"]


def try_get_tx(txid):
    errors = []
    for node in TX_NODES:
        try:
            tx = rpc(node, "condenser_api.get_transaction", [txid])
            return node, tx
        except Exception as e:
            errors.append(f"{node}: {e}")
    return None, errors


print("=== Direct TX lookups ===")
for txid, desc in TXIDS.items():
    node, result = try_get_tx(txid)
    if result and not isinstance(result, list):
        print(f"TX {txid} — CONFIRMED via {node} in block {result.get('block_num')}: {desc}")
        for opname, opdata in result["operations"]:
            brief = {k: v for k, v in opdata.items() if k in ("from", "to", "amount", "vesting_shares", "delegatee")}
            print(f"   op: {opname} {json.dumps(brief)}")
    else:
        print(f"TX {txid} — no node could serve get_transaction ({desc})")
        for e in (result or [])[:4]:
            print(f"   {e}")

print("\n=== Recent ops: @youarealive (last 8) ===")
hist = rpc("https://api.hive.blog", "condenser_api.get_account_history", ["youarealive", -1, 8])
for _idx, ev in reversed(hist):
    opname, opdata = ev["op"]
    if opname in ("transfer", "transfer_to_vesting", "delegate_vesting_shares", "fill_transfer"):
        print(f"  [{ev['timestamp']}] #{ev['block']} {opname} {json.dumps({k: v for k, v in opdata.items() if k in ('from','to','amount','amount_invest','vesting_shares','delegatee')})}")

print("\n=== Recent ops: @aliveandthriving (last 12) ===")
hist = rpc("https://api.hive.blog", "condenser_api.get_account_history", ["aliveandthriving", -1, 12])
for _idx, ev in reversed(hist):
    opname, opdata = ev["op"]
    if opname in ("transfer", "transfer_to_vesting", "delegate_vesting_shares", "fill_transfer", "fill_vesting_withdraw"):
        print(f"  [{ev['timestamp']}] #{ev['block']} {opname} {json.dumps({k: v for k, v in opdata.items() if k in ('from','to','amount','amount_invest','vesting_shares','delegatee')})}")
