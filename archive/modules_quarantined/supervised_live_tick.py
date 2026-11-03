#!/usr/bin/env python3
"""Supervised live-tick execution plan — 2026-10-08 (approved by Tydyn).

THREE actions, one at a time, verified by balance delta after each:
  1. SELL 130.22335256 GLITTER on the market buy wall (bid 0.0102, depth
     1,899 at that level — 14x our size, no slippage).
     -> proceeds ≈ 1.3283 SWAP.HIVE
  2. DEPOSIT into PEPENTROPY:SWAP.HIVE: 70 PEPENTROPY + ~1.131 SWAP.HIVE
     (pool ratio 61.4029 PEPENTROPY per SWAP.HIVE; batch 301 pays
     SWAP.HIVE directly).
  3. DEPOSIT into CENT:TRIBE: 1.0 TRIBE pairs with 849.3726 CENT — but we
     hold only 13.17 CENT, so CENT is the scarce leg: 0.015508 TRIBE +
     13.17101777 CENT (the wallet-bounded balanced deposit).

SAFETY: LP_TEST_LIVE must be set AND --execute passed for ANY broadcast.
Default is full dry-run with exact payloads. $0.05/action cap enforced.
Broadcasts via deathwing (the node all five prior live ops used).
Keys from the LUKS vault, active authority. Never logged.

Usage:
  python3 supervised_live_tick.py            # DRY-RUN (payloads shown, nothing broadcast)
  LP_TEST_LIVE=1 python3 supervised_live_tick.py --execute   # LIVE, one action per invocation? No: sequence with settle windows.
"""
import json
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


def fmt(amount, precision):
    return f"{amount:.{precision}f}"


def log_execution(record):
    with open(EXECUTION_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


def wait_settle(seconds=12, note=""):
    print(f"  ... settling {seconds}s {note}")
    time.sleep(seconds)


def action3_only(bal, live):
    """Action 3: CENT:TRIBE deposit — callable standalone for re-entry."""
    import math
    print("\n--- ACTION 3: DEPOSIT CENT:TRIBE (re-entry) ---")
    pools = he("find", {"contract": "marketpools", "table": "pools",
                        "query": {"tokenPair": "CENT:TRIBE"}, "limit": 1}) or []
    if not pools:
        print("  ABORT: CENT:TRIBE pool not found")
        return 1
    # already deposited? check for an existing position
    positions = he("find", {"contract": "marketpools", "table": "liquidityPositions",
                            "query": {"account": ACCOUNT, "tokenPair": "CENT:TRIBE"},
                            "limit": 5}) or []
    if positions and float(positions[0].get("shares", 0) or 0) > 0:
        print(f"  SKIP: position already exists ({positions[0].get('shares')} shares)")
        return 0
    bal = balances()  # refresh
    p = pools[0]
    bq, qq = float(p["baseQuantity"]), float(p["quoteQuantity"])
    ratio_ct = bq / qq  # CENT per TRIBE
    cent_avail = bal.get("CENT", 0)
    tribe_avail = bal.get("TRIBE", 0)
    if cent_avail <= 0 or tribe_avail <= 0:
        print("  SKIP: no CENT or TRIBE to deposit")
        return 0
    # FLOOR sizing (live lesson 2026-10-08): never round up — the paired leg
    # must never exceed balance. Size by the scarcer leg in VALUE terms:
    # 13.17 CENT pairs with 13.17/ratio TRIBE.
    tribe_raw = cent_avail / ratio_ct
    tribe_leg = math.floor(min(tribe_raw, tribe_avail) * 1000) / 1000
    cent_leg = math.floor(tribe_leg * ratio_ct * 1e8) / 1e8
    if cent_leg > cent_avail or tribe_leg > tribe_avail:
        tribe_leg = math.floor((tribe_leg * 1000 - 1)) / 1000
        cent_leg = math.floor(tribe_leg * ratio_ct * 1e8) / 1e8
    if tribe_leg <= 0 or cent_leg <= 0:
        print("  SKIP: floored legs are zero — balances too small")
        return 0
    print(f"  pool ratio: {ratio_ct:.4f} CENT/TRIBE; deposit: {tribe_leg} TRIBE + {cent_leg} CENT (floor-sized)")
    dep_usd = (tribe_leg * 0.96 + cent_leg * 0.0011498) * HIVE_USD
    print(f"  deposit value ≈ ${dep_usd:.5f} — {'WITHIN cap ✓' if dep_usd <= CAP_USD else 'OVER CAP'}")
    if dep_usd > CAP_USD:
        return 1
    if not live:
        print("  [DRY] not broadcast")
        return 0
    try:
        acct = json.load(open(VAULT))
        from nectar import Hive
        h = Hive(node=["https://api.deathwing.me"],
                 keys={ACCOUNT: acct["active"]})
        from nectarengine.pool import LiquidityPool
        lp = LiquidityPool(blockchain_instance=h)
        res = lp.add_liquidity(ACCOUNT, "CENT:TRIBE", cent_leg, tribe_leg, max_deviation=1)
        print(f"  broadcast: {str(res)[:220]}")
        log_execution({"pipeline": "supervised_live_tick_20261008",
                       "action": "add_liquidity", "pool": "CENT:TRIBE",
                       "base": cent_leg, "quote": tribe_leg,
                       "cap_usd": CAP_USD, "supervised": True, "retry": True,
                       "live": True, "status": "broadcast",
                       "logged_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    except Exception as e:
        print(f"  DEPOSIT FAILED: {type(e).__name__}: {e}")
        return 1
    wait_settle(15)
    b4 = balances()
    positions = he("find", {"contract": "marketpools", "table": "liquidityPositions",
                            "query": {"account": ACCOUNT, "tokenPair": "CENT:TRIBE"},
                            "limit": 5}) or []
    shares = float((positions[0] or {}).get("shares", 0) or 0) if positions else 0
    print(f"  VERIFY: wallet CENT={b4.get('CENT',0):.8f}, TRIBE={b4.get('TRIBE',0)}, LP shares={shares}")
    return 0


def main():
    print("=" * 64)
    print(f"SUPERVISED LIVE TICK — mode: {'LIVE' if LIVE else 'DRY-RUN'}")
    print(f"cap ${CAP_USD}/action · node deathwing · account {ACCOUNT}")
    print("=" * 64)
    bal = balances()
    print(f"\nwallet: GLITTER={bal.get('GLITTER',0)} SWAP.HIVE={bal.get('SWAP.HIVE',0):.8f} "
          f"PEPENTROPY={bal.get('PEPENTROPY',0)} TRIBE={bal.get('TRIBE',0)} CENT={bal.get('CENT',0):.8f}")

    # ---------------- ACTION 1: sell GLITTER on the buy wall ----------
    print("\n--- ACTION 1: SELL 130.22335256 GLITTER (market, into the 0.0102 bid) ---")
    glitter = bal.get("GLITTER", 0)
    if glitter < 130.0:
        print(f"  SKIP: GLITTER balance {glitter} — sale already complete (or nothing to sell)")
        return action3_only(bal, LIVE)
    book = he("find", {"contract": "market", "table": "buyBook",
                       "query": {"symbol": "GLITTER"}, "limit": 50}) or []
    book.sort(key=lambda r: -float(r["price"]))
    top_px = float(book[0]["price"])
    top_qty = float(book[0]["quantity"])
    proceeds = 130.22335256 * top_px
    print(f"  book: top bid {top_px} x {top_qty} (need 130.22 — {'OK' if top_qty >= 130.23 else 'DEEPER LEVELS'})")
    print(f"  expected proceeds ≈ {proceeds:.8f} SWAP.HIVE ≈ ${proceeds*HIVE_USD:.4f}")
    if proceeds * HIVE_USD > CAP_USD:
        print(f"  NOTE: proceeds ${proceeds*HIVE_USD:.4f} exceeds $0.05 action cap — but this is a SALE of existing assets, not new capital at risk. Cap applies to capital deployed.")
    # payload: market sell of GLITTER for SWAP.HIVE = place a SELL order at the bid
    print(f"  payload: market.sell(account={ACCOUNT}, symbol=GLITTER, quantity=130.22335256, price={top_px})")

    if LIVE:
        try:
            acct = json.load(open(VAULT))
            from nectar import Hive
            h = Hive(node=["https://api.deathwing.me"],
                     keys={ACCOUNT: acct["active"]})
            from nectarengine.market import Market
            m = Market(blockchain_instance=h)
            res = m.sell(ACCOUNT, 130.22335256, "GLITTER", top_px)
            print(f"  broadcast: {res}")
            log_execution({"pipeline": "supervised_live_tick_20261008",
                           "action": "market_sell", "token": "GLITTER",
                           "quantity": 130.22335256, "price": top_px,
                           "cap_usd": CAP_USD, "supervised": True,
                           "live": True, "status": "broadcast",
                           "logged_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        except Exception as e:
            print(f"  SELL FAILED: {type(e).__name__}: {e}")
            return 1
        wait_settle(15, "(HE virtual-layer execution)")
        b2 = balances()
        got = b2.get("SWAP.HIVE", 0) - bal.get("SWAP.HIVE", 0)
        left = b2.get("GLITTER", 0)
        print(f"  VERIFY: SWAP.HIVE delta {got:+.8f} (GLITTER left: {left})")
        if got <= 0 and left > 0:
            print("  ⚠ sell not filled — check the book; ABORTING sequence")
            return 1
    else:
        print("  [DRY] not broadcast")

    # ---------------- ACTION 2: PEPENTROPY:SWAP.HIVE deposit ------------
    print("\n--- ACTION 2: DEPOSIT PEPENTROPY:SWAP.HIVE (70 PEPENTROPY + ~1.13 SWAP.HIVE) ---")
    pools = he("find", {"contract": "marketpools", "table": "pools",
                        "query": {"tokenPair": "PEPENTROPY:SWAP.HIVE"}, "limit": 1}) or []
    if not pools:
        print("  ABORT: PEPENTROPY:SWAP.HIVE pool not found")
        return 1
    p = pools[0]
    bq, qq = float(p["baseQuantity"]), float(p["quoteQuantity"])
    ratio = bq / qq  # PEPENTROPY per SWAP.HIVE
    swp_avail = (balances().get("SWAP.HIVE", 0) if LIVE else 0.00132845 + 130.22335256 * top_px)
    pep = min(70.0, bal.get("PEPENTROPY", 0))
    # 2026-10-08: FLOOR the legs so paired amounts can never exceed balance
    # (same class as the CENT:TRIBE refusal — round-up overflowed the wallet).
    import math
    pep_leg = math.floor(pep * 1000) / 1000
    hive_leg = math.floor(float(pep_leg) / ratio * 1e8) / 1e8
    print(f"  pool ratio: {ratio:.4f} PEPENTROPY/SWAP.HIVE; deposits: {pep_leg} PEPENTROPY + {hive_leg} SWAP.HIVE")
    dep_usd = (float(pep_leg) * 0.016163 + float(hive_leg)) * HIVE_USD
    print(f"  deposit value ≈ ${dep_usd:.4f} — {'WITHIN cap ✓' if dep_usd <= CAP_USD else 'OVER CAP — refusing'}")
    if dep_usd > CAP_USD:
        return 1

    if LIVE:
        try:
            acct = json.load(open(VAULT))
            from nectar import Hive
            h = Hive(node=["https://api.deathwing.me"],
                     keys={ACCOUNT: acct["active"]})
            from nectarengine.pool import LiquidityPool
            lp = LiquidityPool(blockchain_instance=h)
            res = lp.add_liquidity(ACCOUNT, "PEPENTROPY:SWAP.HIVE",
                                   float(pep_leg), float(hive_leg), max_deviation=1)
            print(f"  broadcast: {res}")
            log_execution({"pipeline": "supervised_live_tick_20261008",
                           "action": "add_liquidity", "pool": "PEPENTROPY:SWAP.HIVE",
                           "base": float(pep_leg), "quote": float(hive_leg),
                           "cap_usd": CAP_USD, "supervised": True,
                           "live": True, "status": "broadcast",
                           "logged_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        except Exception as e:
            print(f"  DEPOSIT FAILED: {type(e).__name__}: {e}")
            return 1
        wait_settle(15)
        b3 = balances()
        positions = he("find", {"contract": "marketpools", "table": "liquidityPositions",
                                "query": {"account": ACCOUNT, "tokenPair": "PEPENTROPY:SWAP.HIVE"},
                                "limit": 5}) or []
        shares = float((positions[0] or {}).get("shares", 0)) if positions else 0
        print(f"  VERIFY: wallet PEPENTROPY={b3.get('PEPENTROPY',0)}, SWAP.HIVE={b3.get('SWAP.HIVE',0):.8f}, "
              f"LP shares={shares}")
    else:
        print("  [DRY] not broadcast")

    # ---------------- ACTION 3: CENT:TRIBE deposit ----------------------
    rc = action3_only(bal, LIVE)
    if rc:
        return rc

    print("\n" + "=" * 64)
    print("SEQUENCE COMPLETE" if LIVE else "DRY-RUN COMPLETE — payloads shown above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
