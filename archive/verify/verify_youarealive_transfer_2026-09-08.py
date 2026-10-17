#!/usr/bin/env python3
"""Find the 0.246 HIVE transfer from @youarealive in a wider history window."""
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

hist = rpc("https://api.hive.blog", "condenser_api.get_account_history", ["youarealive", -1, 300])
print("=== @youarealive: transfer / transfer_to_vesting ops in last 300 entries ===")
found = 0
for _idx, ev in reversed(hist):
    opname, opdata = ev["op"]
    if opname in ("transfer", "transfer_to_vesting"):
        found += 1
        print(f"  [{ev['timestamp']}] #{ev['block']} trx={ev.get('trx_id','')} {opname} "
              f"{json.dumps({k: v for k, v in opdata.items() if k in ('from','to','amount','memo')})}")
if not found:
    print("  (none found in window)")
