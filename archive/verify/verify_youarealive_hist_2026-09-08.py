#!/usr/bin/env python3
"""Sender-side confirmation: last 10 raw history entries of @youarealive."""
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

hist = rpc("https://api.hive.blog", "condenser_api.get_account_history", ["youarealive", -1, 10])
print("=== @youarealive last 10 history entries (raw) ===")
for _idx, ev in reversed(hist):
    opname, opdata = ev["op"]
    brief = {k: opdata[k] for k in ("from", "to", "amount", "amount_invest", "reward_hive", "reward_vests") if k in opdata}
    print(f"  [{ev['timestamp']}] #{ev['block']} trx={ev.get('trx_id','')} {opname} {json.dumps(brief) if brief else ''}")
