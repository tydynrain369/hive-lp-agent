#!/usr/bin/env python3
"""FULL dry-run: decision layer against tydynrain's 136 positions.

- Real fee evidence computed from on-chain volume/liquidity.
- Real rewards discovered per pool.
- Lifecycle filter (SWAP.HIVE:FORGE is EOL, must not win).
- Read-only: no vault keys, no signing, no broadcast.
"""
import sys
sys.path.insert(0, "/home/tydynrain/.hermes/scripts")

import json, urllib.request

from liquidity_agent_v2 import LiquidityAgent, he
from lp_decision import decide
from nectar import Hive
from nectarengine.pool import LiquidityPool

a = LiquidityAgent()
a.pe.load_all()
px = a.pe.token_prices or {}

lp = LiquidityPool(blockchain_instance=Hive(node=["https://api.hive-engine.com"]))
pos = lp.get_liquidity_positions("tydynrain", limit=200) or []
live = [p for p in pos if float(p.get("shares") or 0) > 0]
held_pairs = [p["tokenPair"] for p in live]

# Economics + threshold analyses
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

# Real fee APR from 24h volume x 0.1% fee vs liquidity (USD)
fee_override = {}
for pair in held_pairs:
    try:
        r = he("find", {"contract": "marketpools", "table": "pools",
                       "query": {"tokenPair": pair}, "limit": 1})
        if not r:
            fee_override[pair] = {"fee_apr_pct_authoritative": None}
            continue
        pool = r[0]
        bv, qv = float(pool.get("baseVolume") or 0), float(pool.get("quoteVolume") or 0)
        bq, qq = float(pool.get("baseQuantity") or 0), float(pool.get("quoteQuantity") or 0)
        b, q = pair.split(":")
        pb, pq = px.get(b, 0), px.get(q, 0)
        vol_usd = bv * pb + qv * pq
        liq_usd = bq * pb + qq * pq
        if liq_usd > 0 and vol_usd > 0:
            fee_override[pair] = {"fee_apr_pct_authoritative": (vol_usd * 0.001 * 365) / liq_usd * 100}
        else:
            fee_override[pair] = {"fee_apr_pct_authoritative": None}
    except Exception:
        fee_override[pair] = {"fee_apr_pct_authoritative": None}

# Real reward discovery — returns {pool_pair: [{mined_token, daily_reward, type}, ...]}
# discover_rewards(pe, portfolio) takes the price engine + a portfolio object.
# We have the live positions list; reframe it into a minimal portfolio-like shape.
from cron_lp_agent import discover_rewards
class _P:
    def __init__(self): self.positions = {p["tokenPair"]: None for p in live}
reward_info = discover_rewards(a.pe, _P())

print(f"valued positions: {len(held_pairs)}")
print(f"fee evidence for: {len([v for v in fee_override.values() if v['fee_apr_pct_authoritative'] is not None])}")
print(f"reward pools found: {len(reward_info)}")

d = decide(held_pairs, reward_info, position_economics=econ, threshold_analysis=thr,
           price_map=px, fee_evidence=fee_override)

print("\n=== DECISION RESULT ===")
print(f"verdicts returned: {len(d.verdicts)}")
acts = {}
for k, v in d.verdicts.items():
    acts[v.action] = acts.get(v.action, 0) + 1
print(f"action mix: {acts}")
print(f"expand candidate: {d.expand_candidate}")
print(f"exit count: {len(d.exit_candidates)}")
print(f"recommended reallocation: {d.recommended_reallocation}")
print()
print("=== NOTES ===")
for n in d.notes:
    print(f"  - {n}")
