#!/usr/bin/env python3
"""Delegation history: all delegate_vesting_shares ops from @aliveandthriving in recent window."""
import json
import urllib.request

def rpc(url, method, params, timeout=25):
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    if "error" in resp:
        raise RuntimeError(resp["error"].get("message", "?"))
    return resp["result"]

hist = rpc("https://api.hive.blog", "condenser_api.get_account_history", ["aliveandthriving", -1, 500])
print("=== @aliveandthriving: delegate_vesting_shares + transfer_to_vesting ops (last 500 entries) ===")
for _idx, ev in reversed(hist):
    opname, opdata = ev["op"]
    if opname in ("delegate_vesting_shares", "transfer_to_vesting"):
        print(f"  [{ev['timestamp']}] #{ev['block']} trx={ev.get('trx_id','')[:16]}... {opname} "
              f"{json.dumps({k: v for k, v in opdata.items() if k in ('from','to','amount','vesting_shares','delegatee')})}")
