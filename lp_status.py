#!/usr/bin/env python3
"""lp_status.py — single-command view of LP agent state.

Usage:  python lp_status.py
Shows: held positions + values, latest decision verdicts, latest recommendation,
dry-run/live execution history (recent), and what would execute next.
"""
import glob
import json
import os
import sys
from datetime import datetime

BASE = "/home/tydynrain/.hermes/lp_tests"
DECISIONS_GLOB = os.path.join(BASE, "decisions", "decision-*.json")
EXECUTIONS_LOG = os.path.join(BASE, "test_executions.jsonl")
SNAPSHOTS_LOG = os.path.join(BASE, "fee_snapshots.jsonl")

LIVE = os.environ.get("LP_TEST_LIVE", "") == "1"
CAP = os.environ.get("LP_TEST_MAX_USD", "0.05")


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def _latest_decision():
    files = sorted(glob.glob(DECISIONS_GLOB))
    if not files:
        return None, None
    path = files[-1]
    with open(path) as f:
        return json.load(f), path


def _simulate_next_action(decision):
    """Replicate executor's next-action logic for preview."""
    verdicts = decision.get("verdicts") or {}
    exit_pools = [
        p for p, v in verdicts.items()
        if v["action"] == "exit" and v.get("confidence") != "low"
    ]
    for pool in exit_pools:
        pos = _lookup_position(pool)
        if pos and pos["shares"] > 0:
            return {
                "would_execute": "remove_liquidity",
                "pool": pool,
                "shares_held": pos["shares"],
                "value_usd": pos["value_usd"],
                "cap_usd": float(CAP),
                "shares_to_remove": min(pos["shares"], pos["shares"] * (float(CAP) / pos["value_usd"]) if pos["value_usd"] > float(CAP) else pos["shares"]),
                "reason": verdicts[pool]["reason"],
            }
    return None


def _lookup_position(pool):
    """Read the position straight from the snapshot log (most recent per pool)."""
    snaps = [s for s in _read_jsonl(SNAPSHOTS_LOG) if s.get("pool") == pool]
    if not snaps:
        return None
    s = snaps[-1]
    snapshot = s.get("snapshot", {})
    share_frac = snapshot.get("share") or {}
    num = float(share_frac.get("num", 0))
    den = float(share_frac.get("den", 1))
    reserves = snapshot.get("reserves") or {}
    value_usd_estimate = 0.0
    # rough value: no prices here, so just report share fraction and reserves
    return {
        "shares": num / den if den else 0.0,
        "share_pct": round((num / den * 100) if den else 0.0, 6),
        "reserves": reserves,
        "at_ms": s.get("at_ms"),
        "value_usd": snapshot.get("value_usd", 0.0),
    }


def fmt_ts(ms):
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000).strftime("%m-%d %H:%M:%S")


def main():
    print("=" * 70)
    print("LP AGENT STATUS")
    print("=" * 70)
    print(f"  Mode:           {'🔴 LIVE (transactions broadcast)' if LIVE else '🟡 DRY-RUN (safe, no broadcast)'}")
    print(f"  Per-tx cap:     ${CAP} USD")
    print(f"  Executions log: {EXECUTIONS_LOG}")
    print()

    decision, decision_path = _latest_decision()
    if not decision:
        print("  No decisions yet. Wait for next hourly cron run.")
        return

    print(f"  📋 Latest decision: {os.path.basename(decision_path)}")
    print(f"     generated: {decision.get('timestamp')}")
    print()
    print("  VERDICTS:")
    verdicts_map = decision.get("verdicts") or {}
    for pool, v in verdicts_map.items():
        marker = {"hold": "✅", "exit": "❌", "watch": "🟡", "expand": "📈", "enter": "➕"}.get(v["action"], "?")
        ev_h = v.get("evidence_window_hours", "?")
        print(f"    {marker} {pool:25s} {v['action']:6s} fee={v.get('fee_apr_pct',0):>7}% rewards={v.get('reward_token_count',0):>2}  ({ev_h}h evidence)")
        print(f"         └─ {v['reason']}")

    reco = decision.get("recommended_reallocation")
    if reco:
        print()
        print(f"  📌 RECOMMENDATION: {reco['from_pool']}  →  {reco['to_pool']}")
        print(f"     rationale: {reco['rationale']}")

    print()
    next_action = _simulate_next_action(decision)
    if next_action:
        print("  ⏭  NEXT HOURLY TICK WILL (dry-run unless LIVE=1):")
        print(f"     remove_liquidity on {next_action['pool']}")
        print(f"     shares to remove: {next_action['shares_to_remove']:.8f} (of {next_action['shares_held']:.8f} held)")
        print(f"     position value:   ${next_action['value_usd']:.4f} USD")
        print(f"     reason:           {next_action['reason']}")
    else:
        print("  ⏭  No exit pending; will monitor only.")

    print()
    execs = _read_jsonl(EXECUTIONS_LOG)
    if not execs:
        print("  📜 Executions: none logged yet.")
    else:
        print(f"  📜 Recent executions ({len(execs)} total):")
        for e in execs[-5:]:
            live_tag = "🟢" if e.get("live") else "🔵"
            print(f"    {live_tag} [{e.get('status'):>9s}] {e.get('action'):>18s} {e.get('pool'):>22s} mag={e.get('magnitude')}  @ {e.get('logged_at','')}")
            if e.get("note"):
                print(f"         └─ {e['note'][:120]}")
    print()


if __name__ == "__main__":
    main()
