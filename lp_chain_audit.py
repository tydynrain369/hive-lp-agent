#!/usr/bin/env python3
"""lp_chain_audit.py -- reconcile on-chain marketpools ops against the LP ledgers.

WHY THIS EXISTS
---------------
On 2026-11-03 two holes opened in the LP agent's audit trail, and neither was
detectable from the ledgers themselves:

  1. 09:05 HST -- a removeLiquidity broadcast (trx c9e1a80b...) never reached
     reallocations.jsonl because execute() raised AFTER the broadcast. The
     removal is real on-chain; the ledger does not know it happened.
  2. 08:35 HST -- five addLiquidity ops reached the chain with NO ledger record
     anywhere. liquidity_agent_v2.py's deploy path broadcasts through
     broadcast_once_hive() and writes no ledger at all.

A successful broadcast is not an audited action. This tool treats the CHAIN as
ground truth, diffs it against both ledgers, and reports every orphan in BOTH
directions:

  * UNLOGGED  -- on-chain, in no ledger        (the dangerous direction)
  * UNBACKED  -- ledgered as live, not on chain (a broadcast that silently died)

READ-ONLY BY CONSTRUCTION: no keys are loaded, nothing is signed, nothing is
broadcast, and neither ledger is ever written.

Usage:
  lp_chain_audit.py [--account NAME] [--pages N] [--json]
Exit codes:
  0 reconciled (no post-ledger orphans) | 1 orphans found | 2 could not read chain
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

# P1-9 (Opus round 3): fall back across public nodes — a single node's
# outage used to read as "ledger entry unbacked", a false alarm by
# construction.
NODES = [
    "https://api.hive.blog",
    "https://api.deathwing.me",
    "https://hive-api.arcange.eu",
]
NODE = NODES[0]
LP_DIR = os.path.expanduser("~/.hermes/lp_tests")
REALLOC_LOG = os.path.join(LP_DIR, "reallocations.jsonl")
EXEC_LOG = os.path.join(LP_DIR, "test_executions.jsonl")
# 2026-11-16 reconciliation: 31 pre-fix broadcasts (Oct 9 - Nov 3) that predate
# the ledger-logging fixes were backfilled from chain evidence with per-op
# provenance (actor path + why-unlogged). The chain is ground truth; these rows
# record it. They count as "logged" so the audit measures ONGOING coverage.
BACKFILL_LOG = os.path.join(LP_DIR, "chain_backfill.jsonl")

MARKETPOOLS_ID = "ssc-mainnet-hive"

# Every Hive-Engine contract that moves the agent's LP capital. Auditing only
# add/removeLiquidity would let a live swapTokens (which is capital movement
# too) slip through unseen; the ledger must cover the whole set.
CAPITAL_CONTRACTS = ("marketpools", "market")

# The ledger only came into being with its first entry; anything on-chain
# before that is historical and reported separately as context, not as a fault.
FIRST_LEDGER_TS = "2026-10-03T18:02:06"  # UTC, matching entry 1's trx timestamp


def rpc(method: str, params: dict, timeout: int = 45) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": method, "params": params}).encode()
    last_err = None
    for node in NODES:
        req = urllib.request.Request(
            node, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            last_err = e
    raise RuntimeError(f"all Hive nodes failed ({len(NODES)} tried): {last_err}")


def _op_parts(entry: dict):
    """Return (type, value) whether the node encodes op as dict or [type, val]."""
    op = entry.get("op")
    if isinstance(op, dict):
        val = op.get("value")
        return op.get("type"), val if isinstance(val, dict) else {}
    if isinstance(op, list) and len(op) == 2:
        val = op[1]
        return op[0], val if isinstance(val, dict) else {}
    return None, {}


def fetch_liquidity_ops(account: str, pages: int = 6, page: int = 1000):
    """Every marketpools add/removeLiquidity op in the account's history."""
    out, start, seen = [], -1, 0
    for _ in range(pages):
        res = rpc("account_history_api.get_account_history",
                  {"account": account, "start": start, "limit": page,
                   "operation_filter_low": 1 << 18,
                   "operation_filter_high": 0}).get("result") or {}
        # The node returns {"history": [...]} today, but tolerate a bare list.
        hist = res if isinstance(res, list) else (res.get("history") or [])
        if not hist:
            break
        seen += len(hist)
        for e in hist:
            meta = e[1]
            typ, val = _op_parts(meta)
            if typ != "custom_json_operation":
                continue
            if val.get("id") != MARKETPOOLS_ID:
                continue
            try:
                payload = json.loads(val.get("json") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                # some custom_json payloads are arrays (["reblog", {...}] etc.)
                continue
            if payload.get("contractName") not in CAPITAL_CONTRACTS:
                continue
            action = payload.get("contractAction")
            out.append({
                "action": action,
                "ts": str(meta.get("timestamp")),
                "trx_id": meta.get("trx_id"),
                "block": meta.get("block"),
                "auths": list(val.get("required_auths") or []),
                "detail": payload.get("contractPayload") or {},
            })
        # Older = smaller operation index, whatever order the node returned.
        idxs = []
        for e in hist:
            raw = str(e[1].get("operation_id") or "")
            try:
                idxs.append(int(raw.split(".")[-1]))
            except (ValueError, IndexError):
                continue
        if not idxs or len(hist) < page:
            break
        start = min(idxs) - 1
    # de-dupe across pages, then order chronologically
    uniq = {o["trx_id"]: o for o in out}
    ordered = sorted(uniq.values(), key=lambda o: o["ts"])
    return ordered, seen


# A trx_id reaches the ledgers in three shapes: a plain JSON key,
# an escaped key inside a serialized tx_result, and a Python repr inside a
# `note` string (single quotes). Missing the repr form produced false
# "unlogged" verdicts, so all three must match.
TRX_RE = re.compile(r"""trx_id\\*['"]\s*:\s*\\*['"]([0-9a-f]{40})""")


def ledger_trx_ids(path: str):
    """Every 40-hex trx_id appearing anywhere in a JSONL ledger, with counts."""
    ids = {}
    if not os.path.exists(path):
        return ids
    with open(path, "r", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            for trx in TRX_RE.findall(line):
                ids.setdefault(trx, []).append(i)
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default=os.environ.get("LP_ACCOUNT",
                                                        "hermes-sandbox"))
    ap.add_argument("--pages", type=int, default=6)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reap = ledger_trx_ids(REALLOC_LOG)
    eexc = ledger_trx_ids(EXEC_LOG)
    bf = ledger_trx_ids(BACKFILL_LOG)

    try:
        ops, scanned = fetch_liquidity_ops(args.account, pages=args.pages)
    except Exception as exc:  # noqa: BLE001 - report, never crash the tick
        print(f"lp_chain_audit: could not read chain: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    unlogged, unbacked, pre = [], [], 0
    for o in ops:
        where = []
        if o["trx_id"] in reap:
            where.append("reallocations")
        if o["trx_id"] in eexc:
            where.append("test_executions")
        if o["trx_id"] in bf:
            where.append("chain_backfill")
        if o["ts"] < FIRST_LEDGER_TS:
            pre += 1
            continue
        if not where:
            unlogged.append({**o, "ledgers": []})
        else:
            o["ledgers"] = where

    # A ledger row whose trx never landed is the reverse fault: it records a
    # capital move the chain has no evidence of.
    onchain = {o["trx_id"] for o in ops}
    for ledger_name, ids in (("reallocations", reap), ("test_executions", eexc),
                             ("chain_backfill", bf)):
        for trx, lines in ids.items():
            if trx not in onchain:
                unbacked.append({"trx_id": trx, "ledger": ledger_name,
                                 "lines": lines})

    report = {
        "account": args.account,
        "scanned_history_entries": scanned,
        "liquidity_ops_on_chain": len(ops),
        "pre_ledger_ops": pre,
        "reallocations_entries_trx": len(reap),
        "test_executions_entries_trx": len(eexc),
        "chain_backfill_entries_trx": len(bf),
        "unlogged": unlogged,
        "unbacked": unbacked,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        post = len(ops) - pre
        print(f"=== LP CHAIN AUDIT -- {args.account} ===")
        print(f"  scanned {scanned} history entries; {len(ops)} capital ops "
              f"on chain ({pre} pre-ledger, informational)")
        print(f"  since the ledger began ({FIRST_LEDGER_TS}Z): {post} capital "
              f"ops, {post - len(unlogged)} logged, {len(unlogged)} unlogged")
        print(f"  reallocations.jsonl trx_ids  : {len(reap)}")
        print(f"  test_executions.jsonl trx_ids: {len(eexc)}")
        print(f"  chain_backfill.jsonl trx_ids : {len(bf)} (2026-11-16 reconciliation)")
        print()
        if unlogged:
            print(f"  UNLOGGED ON-CHAIN ACTIONS ({len(unlogged)}) "
                  f"-- broadcast with no ledger record:")
            for o in unlogged:
                d = o["detail"]
                pair = d.get("tokenPair", "?")
                amt = (f"sharesOut={d['sharesOut']}" if o["action"] == "removeLiquidity"
                       else f"base={d.get('baseQuantity')} quote={d.get('quoteQuantity')}")
                print(f"    {o['ts']}  {o['action']:16s} {pair:22s} {amt}")
                print(f"      trx {o['trx_id']}  block {o['block']}  auths={o['auths']}")
            print()
        else:
            print("  no unlogged post-ledger actions")
        if unbacked:
            print(f"  LEDGERED BUT NOT ON CHAIN ({len(unbacked)}) "
                  f"-- claimed live, never landed:")
            for u in unbacked:
                print(f"    {u['trx_id']} ({u['ledger']} line {u['lines']})")
            print()

    return 1 if (unlogged or unbacked) else 0


if __name__ == "__main__":
    sys.exit(main())
