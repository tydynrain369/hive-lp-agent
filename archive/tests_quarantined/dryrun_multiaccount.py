#!/usr/bin/env python3
"""MULTI-ACCOUNT DRY-RUN: decision layer across all four real accounts.

READ-ONLY: public RPC only, no vault keys, no signing, nothing broadcast.

Accounts: tydynrain, aliveandthriving, heartbeatvault01, heartbeatonhive
For each account:
  1. Load LP positions (EXPLICIT limit=1000 — the pagination default of 100
     silently truncated tydynrain to 100 of 136 once; never again).
  2. Value each position against live reserves + price map (USD, not ratios).
  3. Compute real fee APR per pool from on-chain 24h volume x 0.1% / liquidity.
  4. Run the decision layer with the lifecycle filter active.
Then, cross-account:
  5. Check GLOBAL staking thresholds (CENT 20k, PIMP 5k, BBHO 20k, BBH 1k)
     — these must be met across ALL accounts before any LP allocation.
  6. Flag any guardrail constraint relevant to the verdicts (ALIVE/ALIVEM/BEE
     reserves on @aliveandthriving, dividend-gated tokens).
"""
import sys
sys.path.insert(0, "/home/tydynrain/.hermes/scripts")

import json, time

from liquidity_agent_v2 import LiquidityAgent, he
from lp_decision import decide
from lp_token_constraints import LPConstraints
from nectar import Hive
from nectarengine.pool import LiquidityPool

ACCOUNTS = ["tydynrain", "aliveandthriving", "heartbeatvault01", "heartbeatonhive"]

a = LiquidityAgent()
a.pe.load_all()
px = a.pe.token_prices or {}

lp = LiquidityPool(blockchain_instance=Hive(node=["https://api.hive-engine.com"]))
constraints = LPConstraints()


def load_account_positions(account):
    """Positions with an EXPLICIT limit; reports truncation rather than implying it."""
    pos = lp.get_liquidity_positions(account, limit=1000) or []
    live = [p for p in pos if float(p.get("shares") or 0) > 0]
    return pos, live


def value_positions(live):
    """USD valuation per position from reserves + price map. Returns econ/thr dicts."""
    econ, thr = {}, {}
    for p in live:
        pair = p["tokenPair"]
        sh = float(p["shares"])
        if pair not in a.pe.pools or sh <= 0:
            continue
        row = a.pe.pools[pair]
        tot = float(getattr(row, "total_shares", 0) or 0)
        if tot <= 0:
            continue
        b, q = pair.split(":")[:2]
        pb, pq = px.get(b), px.get(q)
        if not pb or not pq:
            continue
        f = sh / tot
        val = f * (float(row.base_reserve) * pb + float(row.quote_reserve) * pq)
        econ[pair] = {"economics_known": True, "our_position_value_usd": val,
                      "fee_evidence": "measured", "reason": None}
        thr[pair] = {"computable": True, "current_value_usd": val,
                     "target_fee_usd": 1e-6, "note": "proportionality, not a forecast"}
    return econ, thr


def real_fee_aprs(pairs):
    """Per-pool fee APR from on-chain 24h volume, keyed for fee_evidence_override."""
    out = {}
    for pair in pairs:
        try:
            r = he("find", {"contract": "marketpools", "table": "pools",
                            "query": {"tokenPair": pair}, "limit": 1})
            if not r:
                out[pair] = {"fee_apr_pct_authoritative": None}
                continue
            pool = r[0]
            bv, qv = float(pool.get("baseVolume") or 0), float(pool.get("quoteVolume") or 0)
            bq, qq = float(pool.get("baseQuantity") or 0), float(pool.get("quoteQuantity") or 0)
            b, q = pair.split(":")
            pb, pq = px.get(b, 0), px.get(q, 0)
            vol_usd = bv * pb + qv * pq
            liq_usd = bq * pb + qq * pq
            if liq_usd > 0 and vol_usd > 0:
                out[pair] = {"fee_apr_pct_authoritative": (vol_usd * 0.001 * 365) / liq_usd * 100}
            else:
                out[pair] = {"fee_apr_pct_authoritative": None}
        except Exception:
            out[pair] = {"fee_apr_pct_authoritative": None}
    return out


class _Portfolio:
    """Minimal portfolio shape for discover_rewards(pe, portfolio)."""
    def __init__(self, pairs):
        self.positions = {p: None for p in pairs}


from cron_lp_agent import discover_rewards

results = {}
print("=" * 74)
print("MULTI-ACCOUNT DRY-RUN (read-only; no keys, no broadcast)")
print("=" * 74)

for account in ACCOUNTS:
    t0 = time.time()
    pos, live = load_account_positions(account)
    pairs = [p["tokenPair"] for p in live]
    print(f"\n--- {account} ---")
    print(f"  positions: {len(live)} live (query limit 1000; "
          f"{'CAPPED - TRUNCATION LIKELY' if len(pos) >= 1000 else 'not capped'})")

    econ, thr = value_positions(live)
    print(f"  valued:    {len(econ)} of {len(pairs)} positions have full USD valuation")

    fee_ov = real_fee_aprs(pairs)
    n_apr = sum(1 for v in fee_ov.values() if v["fee_apr_pct_authoritative"] is not None)
    print(f"  fee APRs:  {n_apr} of {len(pairs)} pools have real volume-based APR")

    try:
        reward_info = discover_rewards(a.pe, _Portfolio(pairs))
    except Exception as e:
        print(f"  rewards:    discovery failed ({type(e).__name__}) — continuing without")
        reward_info = {}

    d = decide(pairs, reward_info, position_economics=econ, threshold_analysis=thr,
               price_map=px, fee_evidence=fee_ov,
               fee_evidence_override=fee_ov)

    acts = {}
    for k, v in d.verdicts.items():
        acts[v.action] = acts.get(v.action, 0) + 1
    total_val = sum(v["our_position_value_usd"] for v in econ.values()) if econ else 0.0

    print(f"  verdicts:   {len(d.verdicts)}  mix: {acts}")
    print(f"  portfolio:  ${total_val:.4f}")
    print(f"  expand:     {d.expand_candidate}")
    print(f"  exits:      {len(d.exit_candidates)}  releases: {len(getattr(d, 'release_candidates', []) or [])}")
    if d.recommended_reallocation:
        print(f"  realloc:    {d.recommended_reallocation.get('from_pool')} -> "
              f"{d.recommended_reallocation.get('to_pool')}")
    filtered = [n for n in d.notes if "filtered (lifecycle" in n]
    if filtered:
        print(f"  lifecycle:  {len(filtered)} pool(s) filtered")
        for n in filtered[:3]:
            print(f"      {n.split(': ', 1)[-1][:90]}")

    results[account] = {
        "positions": len(live), "valued": len(econ), "apr_count": n_apr,
        "portfolio_usd": total_val, "action_mix": acts,
        "expand": d.expand_candidate, "exits": len(d.exit_candidates),
        "realloc": d.recommended_reallocation,
        "notes": d.notes,
    }
    print(f"  elapsed:    {time.time()-t0:.1f}s")

# ---------- CROSS-ACCOUNT: global staking thresholds ----------
print("\n" + "=" * 74)
print("CROSS-ACCOUNT GUARDRAILS (global thresholds; read-only checks)")
print("=" * 74)

# Query staked balances for the threshold tokens across all accounts
from liquidity_agent_v2 import he as _he
staked_by_token = {tok: {} for tok in constraints.GLOBAL_STAKING_THRESHOLDS}
for account in ACCOUNTS:
    try:
        rows = _he("find", {"contract": "tokens", "table": "balances",
                           "query": {"account": account}, "limit": 1000}) or []
        for row in rows:
            sym = row.get("symbol", "")
            if sym in staked_by_token:
                staked_by_token[sym][account] = float(row.get("stake", 0) or 0)
    except Exception as e:
        print(f"  balance query failed for {account}: {type(e).__name__}")

print("\nGLOBAL STAKING THRESHOLDS (sum across ALL accounts before any LP)")
all_ok = True
for tok, threshold in sorted(constraints.GLOBAL_STAKING_THRESHOLDS.items()):
    per = staked_by_token.get(tok, {})
    total = sum(per.values())
    met = total >= threshold
    status = "MET    " if met else "NOT MET"
    detail = ", ".join(f"{acc.split('@')[-1]}: {v:,.0f}" for acc, v in per.items()) or "no stakes found"
    print(f"  {tok:6s} {status} {total:>12,.0f} / {threshold:>10,.0f}   ({detail})")
    if not met:
        all_ok = False
        print(f"         -> LP allocation of {tok} would be BLOCKED until the gap is staked")

# Report per-account constraint-relevant holdings
print("\nACCOUNT-SPECIFIC RESERVES RELEVANT TO VERDICTS")
for account in ACCOUNTS:
    tokens = constraints.TOKEN_CONSTRAINTS
    relevant = []
    try:
        rows = _he("find", {"contract": "tokens", "table": "balances",
                            "query": {"account": account}, "limit": 1000}) or []
        bals = {r.get("symbol"): (float(r.get("balance", 0) or 0),
                                  float(r.get("stake", 0) or 0)) for r in rows}
    except Exception:
        bals = {}
    for tok, rule in tokens.items():
        if tok in bals and (bals[tok][0] + bals[tok][1]) > 0:
            liquid, staked = bals[tok]
            c = ""
            if rule.get("min_liquid_balance") and liquid < rule["min_liquid_balance"]:
                c = f"  <-- BELOW min_liquid {rule['min_liquid_balance']} ({rule.get('purpose','')})"
            pool_possible = constraints.pool_exists(tok)
            if not pool_possible:
                c += "  [no pool exists — LP impossible]"
            if c:
                relevant.append(f"    {tok:8s} liquid {liquid:>10,.0f} staked {staked:>10,.0f}{c}")
    if relevant:
        print(f"  {account}:")
        for r in relevant[:8]:
            print(r)
    else:
        print(f"  {account}: no reserve constraints triggered")

# ---------- SUMMARY ----------
print("\n" + "=" * 74)
print("SUMMARY")
print("=" * 74)
g_val = sum(r["portfolio_usd"] for r in results.values())
g_pos = sum(r["positions"] for r in results.values())
print(f"  accounts:   {len(results)}   positions: {g_pos}   portfolio: ${g_val:.2f}")
for account, r in results.items():
    print(f"  {account:20s} {r['positions']:>4d} pos  ${r['portfolio_usd']:>9.2f}  "
          f"expand={str(r['expand'])[:22]:<22s} exits={r['exits']}")
print(f"\n  Nothing was signed, broadcast, or moved. LP_TEST_LIVE is unset.")
