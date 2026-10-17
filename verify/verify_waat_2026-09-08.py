#!/usr/bin/env python3
"""Verify WAAT HIVE collection run of 2026-09-08 (~14:15 HST cron).
Confirms: TXs on-chain, balances, power-up, delegation state."""
import json
import urllib.request

NODES = ["https://api.hive.blog", "https://api.openhive.network", "https://anyx.io"]


def rpc(method, params):
    last_err = None
    for node in NODES:
        try:
            payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
            req = urllib.request.Request(node, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                resp = json.loads(r.read().decode())
            if "error" in resp:
                last_err = f"{node}: {resp['error'].get('message', '?')}"
                continue
            return resp["result"]
        except Exception as e:
            last_err = f"{node}: {e}"
            continue
    raise RuntimeError(f"All nodes failed for {method}: {last_err}")


props = rpc("condenser_api.get_dynamic_global_properties", [])
tvf = float(props["total_vesting_fund_hive"].split()[0])
tvs = float(props["total_vesting_shares"].split()[0])
vests_per_hp = tvs / tvf
print(f"Chain head block: {props['head_block_number']}  chain time: {props['time']}")
print(f"VESTS per 1 HP: {vests_per_hp:,.2f}")

accs = rpc("condenser_api.get_accounts", [["youarealive", "aliveandthriving", "bradleyarrow"]])
print("\n=== Account states ===")
for a in accs:
    vests = float(a["vesting_shares"].split()[0])
    hp = vests / vests_per_hp
    print(f"@{a['name']}:")
    print(f"  HIVE: {a['balance']}  HBD: {a['hbd_balance']}")
    print(f"  vesting_shares: {a['vesting_shares']} (~{hp:,.3f} HP)")
    print(f"  delegated_out: {a.get('delegated_vesting_shares')}  received: {a.get('received_vesting_shares')}")

txids = {
    "7e8d7efa75db1421": "transfer youarealive -> aliveandthriving (0.246 HIVE)",
    "6dae294058b3245b": "transfer_to_vesting (power-up 0.412 HIVE)",
    "aacc71415788a6bf": "delegate_vesting_shares to bradleyarrow",
}
print("\n=== TX verification ===")
for txid, desc in txids.items():
    try:
        tx = rpc("condenser_api.get_transaction", [txid])
        print(f"TX {txid} — CONFIRMED in block {tx.get('block_num')}: {desc}")
        for opname, opdata in tx["operations"]:
            brief = {k: v for k, v in opdata.items() if k in ("from", "to", "amount", "vesting_shares", "delegatee", "memo")}
            print(f"   op: {opname} {json.dumps(brief)}")
    except Exception as e:
        print(f"TX {txid} — lookup FAILED: {e}")

print("\n=== Outgoing vesting delegations from @aliveandthriving ===")
try:
    dels = rpc("condenser_api.get_vesting_delegations", ["aliveandthriving", "", 100])
    for d in dels:
        v = float(d["vesting_shares"].split()[0])
        print(f"  -> @{d['delegatee']}: {d['vesting_shares']} (~{v / vests_per_hp:,.3f} HP)")
except Exception as e:
    print(f"  get_vesting_delegations failed: {e}")
