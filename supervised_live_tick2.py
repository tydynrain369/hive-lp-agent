#!/usr/bin/env python3
"""Supervised live tick #2 — 2026-10-09 (approved by Tydyn).

Sequence (order matters — step 1 funds step 3):
  1. SWAP 0.045 SWAP.HBD -> SWAP.HIVE via the anchor pool (SWAP.HIVE:SWAP.HBD).
     Pool prices HBD at 18.15 vs the book's 17.71 top bid; expected ~0.813
     SWAP.HIVE out. min_amount_out guards the route.
  2. TRIBE swap-half pattern (Tydyn's approach for one-sided assets):
     swap ~half the TRIBE (0.492) -> SWAP.HIVE through SWAP.HIVE:TRIBE,
     then deposit BOTH legs into SWAP.HIVE:TRIBE, floor-sized from live
     post-swap balances (the swap fee+slippage gap is what floor-sizing
     bridges). First live test of pool swap_tokens.
  3. DEPOSIT the full PEPE balance + ~0.2744 SWAP.HIVE into PEPE:SWAP.HIVE
     (its own project's hub pool: 15 reward streams, 419-day runway).
  Leaves ~0.75 SWAP.HIVE in the wallet as operating reserve.

Safety: same pattern as supervised_live_tick.py — LP_TEST_LIVE=1 AND
--execute required for broadcast; skip-if-done for re-entry; floor-sized
legs (never round up); $0.15 supervised cap via LP_TICK_CAP_USD; every
broadcast logged with cap+supervised flags; verified by balance delta.
"""
import json
import math
import os
import sys
import time

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")

CAP_USD = float(os.environ.get("LP_TICK_CAP_USD", "0.05"))
HIVE_USD = 0.057
VAULT = "/mnt/vault/Hermes-Keys/hermes-sandbox.json"
ACCOUNT = "hermes-sandbox"
LIVE = os.environ.get("LP_TEST_LIVE") == "1" and "--execute" in sys.argv
EXECUTION_LOG = "/home/tydynrain/.hermes/lp_tests/test_executions.jsonl"


def he(method, payload):
    import urllib.request
    body = json.dumps({"jsonrpc": "2.0", "method": method, "id": 1,
                       "params": payload}).encode()
    req = urllib.request.Request("https://api.hive-engine.com/rpc/contracts",
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    if "error" in resp:
        raise RuntimeError(f"RPC error: {resp['error']}")
    return resp.get("result")


def balances():
    rows = he("find", {"contract": "tokens", "table": "balances",
                       "query": {"account": ACCOUNT}, "limit": 200}) or []
    return {r["symbol"]: float(r.get("balance", 0) or 0) for r in rows}


def pool(pair):
    rows = he("find", {"contract": "marketpools", "table": "pools",
                       "query": {"tokenPair": pair}, "limit": 1}) or []
    return rows[0] if rows else None


def position_shares(pair):
    rows = he("find", {"contract": "marketpools", "table": "liquidityPositions",
                       "query": {"account": ACCOUNT, "tokenPair": pair},
                       "limit": 5}) or []
    return float(rows[0].get("shares", 0) or 0) if rows else 0.0


def log_execution(record):
    with open(EXECUTION_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


def broadcast_ready():
    acct = json.load(open(VAULT))
    from nectar import Hive
    return Hive(node=["https://api.deathwing.me"],
                keys={ACCOUNT: acct["active"]})


def settle(seconds=15, note=""):
    print(f"  ... settling {seconds}s {note}")
    time.sleep(seconds)


def step1_hbd_swap(bal):
    """SWAP 0.045 SWAP.HBD -> SWAP.HIVE via the anchor pool."""
    print("\n--- STEP 1: SWAP 0.045 SWAP.HBD -> SWAP.HIVE (anchor pool) ---")
    hbd = bal.get("SWAP.HBD", 0)
    if hbd < 0.044:
        print(f"  SKIP: SWAP.HBD balance {hbd} — swap already complete")
        return 0
    p = pool("SWAP.HIVE:SWAP.HBD")
    if not p:
        print("  ABORT: anchor pool not found")
        return 1
    bq, qq = float(p["baseQuantity"]), float(p["quoteQuantity"])
    # constant-product out for trading quote->base: out = x*eff/(y+eff)
    amt = math.floor(hbd * 1000) / 1000        # HBD precision 3
    eff = amt * 0.995
    expected = bq * eff / (qq + eff)
    min_out = math.floor(expected * 0.98 * 1e8) / 1e8   # 2% slippage guard
    print(f"  pool: {bq:.2f} SWAP.HIVE / {qq:.2f} SWAP.HBD")
    print(f"  swap {amt} SWAP.HBD -> expected {expected:.8f} SWAP.HIVE (min {min_out})")
    if not LIVE:
        print("  [DRY] not broadcast")
        return 0
    try:
        h = broadcast_ready()
        from nectarengine.pool import LiquidityPool
        lp = LiquidityPool(blockchain_instance=h)
        res = lp.swap_tokens(ACCOUNT, "SWAP.HIVE:SWAP.HBD", "SWAP.HBD",
                             amt, "exactInput", min_amount_out=min_out)
        print(f"  broadcast: {str(res)[:200]}")
        log_execution({"pipeline": "supervised_live_tick2_20261009",
                       "action": "pool_swap", "pair": "SWAP.HIVE:SWAP.HBD",
                       "in_symbol": "SWAP.HBD", "in_amount": amt,
                       "min_out": min_out, "cap_usd": CAP_USD, "supervised": True,
                       "live": True, "status": "broadcast",
                       "logged_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    except Exception as e:
        print(f"  SWAP FAILED: {type(e).__name__}: {e}")
        return 1
    settle(15, "(HE virtual-layer execution)")
    b = balances()
    got = b.get("SWAP.HIVE", 0)
    hbd_left = b.get("SWAP.HBD", 0)
    print(f"  VERIFY: SWAP.HIVE now {got:.8f}, SWAP.HBD left {hbd_left}")
    if hbd_left >= amt:
        print("  ⚠ SWAP.HBD not consumed — swap may have failed")
        return 1
    return 0


def step2_tribe_swap_half(bal):
    """Tydyn's swap-half pattern: swap half the TRIBE, deposit both legs."""
    print("\n--- STEP 2: TRIBE swap-half -> deposit SWAP.HIVE:TRIBE ---")
    tribe = bal.get("TRIBE", 0)
    if position_shares("SWAP.HIVE:TRIBE") > 0:
        print("  SKIP: SWAP.HIVE:TRIBE position already exists")
        return 0
    if tribe < 0.5:
        print(f"  SKIP: TRIBE balance {tribe} too small for the pattern")
        return 0
    p = pool("SWAP.HIVE:TRIBE")
    if not p:
        print("  ABORT: SWAP.HIVE:TRIBE pool not found")
        return 1
    # swap half (floor at TRIBE precision 3)
    half = math.floor(tribe / 2 * 1000) / 1000
    bq, qq = float(p["baseQuantity"]), float(p["quoteQuantity"])
    eff = half * 0.995
    expected = bq * eff / (qq + eff)
    min_out = math.floor(expected * 0.98 * 1e8) / 1e8
    print(f"  pool: {bq:.4f} SWAP.HIVE / {qq:.4f} TRIBE")
    print(f"  swap-half: {half} TRIBE -> expected {expected:.8f} SWAP.HIVE")
    if not LIVE:
        print("  [DRY] not broadcast")
        return 0
    try:
        h = broadcast_ready()
        from nectarengine.pool import LiquidityPool
        lp = LiquidityPool(blockchain_instance=h)
        res = lp.swap_tokens(ACCOUNT, "SWAP.HIVE:TRIBE", "TRIBE",
                             half, "exactInput", min_amount_out=min_out)
        print(f"  broadcast: {str(res)[:200]}")
        log_execution({"pipeline": "supervised_live_tick2_20261009",
                       "action": "pool_swap", "pair": "SWAP.HIVE:TRIBE",
                       "in_symbol": "TRIBE", "in_amount": half,
                       "min_out": min_out, "cap_usd": CAP_USD, "supervised": True,
                       "live": True, "status": "broadcast",
                       "logged_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    except Exception as e:
        print(f"  SWAP FAILED: {type(e).__name__}: {e}")
        return 1
    settle(15)
    b = balances()
    print(f"  post-swap: TRIBE={b.get('TRIBE',0)}, SWAP.HIVE={b.get('SWAP.HIVE',0):.8f}")
    # deposit both legs, floor-sized from LIVE post-swap balances
    p = pool("SWAP.HIVE:TRIBE")
    ratio = float(p["baseQuantity"]) / float(p["quoteQuantity"])  # SWAP.HIVE per TRIBE
    tribe_have = b.get("TRIBE", 0)
    hive_have = b.get("SWAP.HIVE", 0)
    # size by the SWAP.HIVE leg (the scarcer one post-fee), leaving wallet reserve alone:
    # use only the swap proceeds + original wallet for THIS deposit? Use min of legs.
    hive_leg = min(hive_have, tribe_have * ratio)
    hive_leg = math.floor(hive_leg * 1e8) / 1e8
    tribe_leg = math.floor(hive_leg / ratio * 1000) / 1000
    hive_leg2 = math.floor(tribe_leg * ratio * 1e8) / 1e8
    if hive_leg2 > hive_have:
        tribe_leg = math.floor((tribe_leg * 1000 - 1)) / 1000
        hive_leg2 = math.floor(tribe_leg * ratio * 1e8) / 1e8
    print(f"  deposit (floor-sized): {tribe_leg} TRIBE + {hive_leg2} SWAP.HIVE")
    dep_usd = (tribe_leg * 0.955 + hive_leg2) * HIVE_USD
    print(f"  deposit value ≈ ${dep_usd:.4f} — {'WITHIN cap ✓' if dep_usd <= CAP_USD else 'OVER CAP'}")
    if dep_usd > CAP_USD:
        return 1
    try:
        h = broadcast_ready()
        from nectarengine.pool import LiquidityPool
        lp = LiquidityPool(blockchain_instance=h)
        res = lp.add_liquidity(ACCOUNT, "SWAP.HIVE:TRIBE", hive_leg2, tribe_leg,
                               max_deviation=1)
        print(f"  broadcast: {str(res)[:200]}")
        log_execution({"pipeline": "supervised_live_tick2_20261009",
                       "action": "add_liquidity", "pool": "SWAP.HIVE:TRIBE",
                       "base": hive_leg2, "quote": tribe_leg,
                       "cap_usd": CAP_USD, "supervised": True,
                       "live": True, "status": "broadcast",
                       "logged_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    except Exception as e:
        print(f"  DEPOSIT FAILED: {type(e).__name__}: {e}")
        return 1
    settle(15)
    shares = position_shares("SWAP.HIVE:TRIBE")
    b = balances()
    print(f"  VERIFY: LP shares={shares}, TRIBE left={b.get('TRIBE',0)}, "
          f"SWAP.HIVE={b.get('SWAP.HIVE',0):.8f}")
    return 0


def step3_pepe_deposit(bal):
    """Deposit the full PEPE balance + SWAP.HIVE into PEPE:SWAP.HIVE."""
    print("\n--- STEP 3: DEPOSIT PEPE:SWAP.HIVE (full PEPE + SWAP.HIVE leg) ---")
    pepe = bal.get("PEPE", 0)
    if position_shares("PEPE:SWAP.HIVE") > 0:
        print("  SKIP: PEPE:SWAP.HIVE position already exists")
        return 0
    if pepe < 1000:
        print(f"  SKIP: PEPE balance {pepe} too small")
        return 0
    p = pool("PEPE:SWAP.HIVE")
    if not p:
        print("  ABORT: PEPE:SWAP.HIVE pool not found")
        return 1
    bq, qq = float(p["baseQuantity"]), float(p["quoteQuantity"])
    ratio = qq / bq  # SWAP.HIVE per PEPE
    b = balances()
    pepe_leg = math.floor(b.get("PEPE", 0) * 1e8) / 1e8
    hive_needed = pepe_leg * ratio
    hive_have = b.get("SWAP.HIVE", 0)
    if hive_have < hive_needed * 0.98:
        print(f"  SKIP/RESIZE: SWAP.HIVE {hive_have:.8f} < needed {hive_needed:.8f} — resizing PEPE leg")
        hive_leg = math.floor(hive_have * 1e8) / 1e8
        pepe_leg = math.floor(hive_leg / ratio * 1e8) / 1e8
    else:
        hive_leg = math.floor(hive_needed * 1e8) / 1e8
        # floor-check: paired PEPE must not exceed balance
        if pepe_leg * ratio > hive_have:
            pepe_leg = math.floor(hive_have / ratio * 1e8) / 1e8
            hive_leg = math.floor(pepe_leg * ratio * 1e8) / 1e8
    print(f"  pool: {bq:.0f} PEPE / {qq:.4f} SWAP.HIVE (ratio {ratio:.9f})")
    print(f"  deposit: {pepe_leg:.8f} PEPE + {hive_leg:.8f} SWAP.HIVE (floor-sized)")
    dep_usd = (pepe_leg * ratio + hive_leg) * HIVE_USD
    print(f"  deposit value ≈ ${dep_usd:.4f} — {'WITHIN cap ✓' if dep_usd <= CAP_USD else 'OVER CAP'}")
    if dep_usd > CAP_USD:
        return 1
    if not LIVE:
        print("  [DRY] not broadcast")
        return 0
    try:
        h = broadcast_ready()
        from nectarengine.pool import LiquidityPool
        lp = LiquidityPool(blockchain_instance=h)
        res = lp.add_liquidity(ACCOUNT, "PEPE:SWAP.HIVE", pepe_leg, hive_leg,
                               max_deviation=1)
        print(f"  broadcast: {str(res)[:200]}")
        log_execution({"pipeline": "supervised_live_tick2_20261009",
                       "action": "add_liquidity", "pool": "PEPE:SWAP.HIVE",
                       "base": pepe_leg, "quote": hive_leg,
                       "cap_usd": CAP_USD, "supervised": True,
                       "live": True, "status": "broadcast",
                       "logged_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    except Exception as e:
        print(f"  DEPOSIT FAILED: {type(e).__name__}: {e}")
        return 1
    settle(15)
    shares = position_shares("PEPE:SWAP.HIVE")
    b = balances()
    print(f"  VERIFY: LP shares={shares}, PEPE left={b.get('PEPE',0):.4f}, "
          f"SWAP.HIVE={b.get('SWAP.HIVE',0):.8f}")
    return 0


def main():
    print("=" * 64)
    print(f"SUPERVISED LIVE TICK #2 — mode: {'LIVE' if LIVE else 'DRY-RUN'}")
    print(f"cap ${CAP_USD}/action · node deathwing · account {ACCOUNT}")
    print("=" * 64)
    bal = balances()
    print(f"\nwallet: SWAP.HBD={bal.get('SWAP.HBD',0)} TRIBE={bal.get('TRIBE',0)} "
          f"PEPE={bal.get('PEPE',0):.4f} SWAP.HIVE={bal.get('SWAP.HIVE',0):.8f}")
    rc = step1_hbd_swap(bal)
    if rc:
        return rc
    bal = balances()
    rc = step2_tribe_swap_half(bal)
    if rc:
        return rc
    bal = balances()
    rc = step3_pepe_deposit(bal)
    print("\n" + "=" * 64)
    print("SEQUENCE COMPLETE" if LIVE else "DRY-RUN COMPLETE — payloads shown above")
    return rc


if __name__ == "__main__":
    sys.exit(main())
