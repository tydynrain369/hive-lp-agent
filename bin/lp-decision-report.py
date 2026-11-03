#!/usr/bin/env python3
"""Ad-hoc CLI for manual decision reports.

Moved out of lp_decision.py (2026-11-03): lp_decision is the pure scoring
module and must not import the cron/orchestration layer. This CLI is the only
place that pulls in liquidity_agent_v2 + cron_lp_agent.discover_rewards.
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liquidity_agent_v2 import LiquidityAgent
from cron_lp_agent import discover_rewards
from lp_decision import decide, write_decision


def main() -> None:
    a = LiquidityAgent()
    a.pe.load_all()
    a.portfolio.load_balances()
    a.portfolio.load_positions()
    rew = discover_rewards(a.pe, a.portfolio)
    held = sorted(a.portfolio.positions.keys())
    rep = decide(held, rew)
    print(json.dumps(rep.to_dict(), indent=2, default=str))
    path = write_decision(rep)
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
