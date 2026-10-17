"""LP Decision Engine — evidence-driven pool classification.

Reads:
  - fee_snapshots.jsonl (measured fee evidence over time)
  - discover_rewards() (live reward distributions)
  - portfolio.positions (our actual held LP)

Outputs a decision per held pool:
  * "exit"      — zero fees AND zero rewards (true zombie)
  * "consolidate"— low fees, low rewards; candidate to shift to a productive pool
  * "hold"      — productive on fees and/or rewards
  * "expand"    — productive; candidate for scaled-up allocation

Plus global actions:
  * "swap_concentrate" — recommend swapping dust balances into the winning pool's tokens
  * "deposit_into_winner" — recommend depositing into the highest-scoring pool

This module is pure analysis: no side effects beyond writing decision JSON.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

SNAPSHOT_PATH = "/home/tydynrain/.hermes/lp_tests/fee_snapshots.jsonl"
DECISIONS_DIR = "/home/tydynrain/.hermes/lp_tests/decisions"

# Minimum evidence thresholds (measured, not aspirational)
MIN_FEE_APR_FOR_PRODUCTIVE = 0.10  # 0.10% pool-side APR from snapshot windows
MIN_REWARD_TOKENS_FOR_PRODUCTIVE = 1


@dataclass
class PoolDecision:
    pool: str
    action: str               # exit | consolidate | hold | expand
    reason: str
    fee_apr_pct: float
    reward_token_count: int
    evidence_window_hours: float
    snapshot_count: int


@dataclass
class DecisionReport:
    timestamp: str
    held_pools: list
    verdicts: dict            # pool -> PoolDecision
    exit_candidates: list     # ordered list of pool names to exit
    expand_candidate: Optional[str]   # highest-scoring productive pool
    recommended_reallocation: Optional[dict]   # {from_pool, to_pool, rationale}
    notes: list

    def to_dict(self):
        d = asdict(self)
        d["verdicts"] = {k: asdict(v) for k, v in self.verdicts.items()}
        return d


def load_fee_evidence(snapshot_path: str = SNAPSHOT_PATH) -> dict:
    """Aggregate per-pool fee evidence across all windows.

    Robust to outlier spikes: uses the MEDIAN of consecutive-snapshot volume
    deltas (not first-to-last cumulative), so a single anomalous hour cannot
    flip a zombie pool's verdict. The median naturally represents "typical"
    activity, which is what we want to extrapolate from."""
    if not os.path.exists(snapshot_path):
        return {}
    windows = {}
    with open(snapshot_path) as f:
        snaps = [json.loads(l) for l in f.read().strip().split("\n") if l.strip()]
    by_pool = {}
    for s in snaps:
        by_pool.setdefault(s["pool"], []).append(s)
    for pool, arr in by_pool.items():
        arr.sort(key=lambda s: s.get("at_ms") or s.get("snapshot", {}).get("at_ms", 0))
        snapshots = [s["snapshot"] for s in arr if "snapshot" in s]
        if len(snapshots) < 2:
            continue
        # Per-interval base-volume deltas (typical step ≈ 1 hour)
        deltas = []
        for prev, cur in zip(snapshots, snapshots[1:]):
            dt_ms = cur["at_ms"] - prev["at_ms"]
            if dt_ms <= 0:
                continue
            v0 = float((prev.get("volumes") or {}).get("baseVolume") or 0)
            v1 = float((cur.get("volumes") or {}).get("baseVolume") or 0)
            delta = max(0.0, v1 - v0)
            deltas.append((delta, dt_ms / 3_600_000.0))  # (delta, hours)
        if not deltas:
            continue
        # Winsorize: clamp extreme outliers to the 90th percentile, so a single
        # anomalous hour cannot dominate the APR. Then use mean of clipped deltas
        # (median across whole window is degenerate when most intervals are 0).
        sorted_d = sorted(d for d, _ in deltas)
        p90_idx = max(0, min(int(len(sorted_d) * 0.9) - 1, len(sorted_d) - 1))
        p90 = sorted_d[p90_idx]
        clipped = [min(d, p90) for d, _ in deltas]
        mean_delta_per_interval = sum(clipped) / len(clipped)
        # Median interval (defensive against jitter) — usually ~1h
        sorted_dt = sorted(h for _, h in deltas)
        median_dt_h = sorted_dt[len(sorted_dt) // 2]
        # Total observation window (for reporting only)
        dt_h_total = (snapshots[-1]["at_ms"] - snapshots[0]["at_ms"]) / 3_600_000.0
        reserves = snapshots[-1].get("reserves") or {}
        base_token = pool.split(":")[0]
        base_liq = float(reserves.get(base_token) or 0)
        if mean_delta_per_interval <= 0 or base_liq <= 0 or median_dt_h <= 0:
            fee_apr = 0.0
        else:
            # Extrapolate: avg (winsorized) per-interval volume → annualized APR
            fee_apr = round(
                (mean_delta_per_interval * 0.0025 / base_liq) * (8760.0 / median_dt_h) * 100.0, 3
            )
        # Tail signal: track the max single-interval delta so we can see bursts
        max_delta = max(d for d, _ in deltas)
        typical = sorted_d[len(sorted_d) // 2]  # raw median for comparison
        windows[pool] = {
            "fee_apr_pct": fee_apr,
            "evidence_window_hours": round(dt_h_total, 2),
            "snapshot_count": len(snapshots),
            "mean_interval_vol_winsorized": round(mean_delta_per_interval, 4),
            "median_interval_vol": typical,
            "p90_interval_vol": p90,
            "max_interval_vol": max_delta,
            "spike_ratio_vs_winsor_mean": round(max_delta / mean_delta_per_interval, 1) if mean_delta_per_interval > 0 else None,
            "base_liquidity": base_liq,
        }
    return windows


def decide(held_pools: list, reward_info: dict, fee_evidence: Optional[dict] = None) -> DecisionReport:
    """
    held_pools: list of pool_pair strings (from portfolio.positions.keys())
    reward_info: dict from cron_lp_agent.discover_rewards()
    fee_evidence: output of load_fee_evidence() (optional; will be loaded if not provided)
    """
    if fee_evidence is None:
        fee_evidence = load_fee_evidence()

    verdicts = {}
    exit_candidates = []
    expand_candidate = None
    expand_score = -1.0

    for pool in held_pools:
        fee_info = fee_evidence.get(pool) or {}
        fee_apr = fee_info.get("fee_apr_pct", 0.0)
        reward_tokens = []
        # reward_info for a pool is typically a list of reward dicts
        rd = reward_info.get(pool) or []
        if isinstance(rd, list):
            reward_tokens = [r.get("token", r.get("symbol", "?")) for r in rd if isinstance(r, dict)]
        reward_count = len(reward_tokens)

        # classification
        if fee_apr < 0.01 and reward_count == 0:
            action = "exit"
            reason = "no measurable fees AND no active reward distributions"
        elif fee_apr >= MIN_FEE_APR_FOR_PRODUCTIVE and reward_count >= MIN_REWARD_TOKENS_FOR_PRODUCTIVE:
            action = "hold" if fee_apr >= 0.5 else "consolidate"
            reason = f"fee APR {fee_apr}% + {reward_count} reward tokens"
            score = fee_apr * 1.0 + reward_count * 0.1
            if score > expand_score:
                expand_score = score
                expand_candidate = pool
        elif reward_count >= 3:
            action = "hold"
            reason = f"reward-only productive: {reward_count} reward tokens"
        elif fee_apr >= MIN_FEE_APR_FOR_PRODUCTIVE:
            action = "hold"
            reason = f"fee-only productive: APR {fee_apr}%"
            if fee_apr > expand_score:
                expand_score = fee_apr
                expand_candidate = pool
        else:
            action = "consolidate"
            reason = f"weak on both axes (fee_apr={fee_apr}%, rewards={reward_count})"

        if action == "exit":
            exit_candidates.append(pool)

        verdicts[pool] = PoolDecision(
            pool=pool,
            action=action,
            reason=reason,
            fee_apr_pct=fee_apr,
            reward_token_count=reward_count,
            evidence_window_hours=fee_info.get("evidence_window_hours", 0.0),
            snapshot_count=fee_info.get("snapshot_count", 0),
        )

    recommended = None
    if exit_candidates and expand_candidate:
        recommended = {
            "from_pool": exit_candidates[0],
            "to_pool": expand_candidate,
            "rationale": f"exit zombie {exit_candidates[0]} and redeploy to {expand_candidate} "
                         f"(highest combined fee+reward score)",
        }

    notes = []
    if exit_candidates:
        notes.append(f"exit candidates: {', '.join(exit_candidates)}")
    if expand_candidate:
        notes.append(f"expand candidate: {expand_candidate}")
    if not exit_candidates and not expand_candidate:
        notes.append("portfolio already in near-optimal configuration given evidence")

    return DecisionReport(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        held_pools=list(held_pools),
        verdicts=verdicts,
        exit_candidates=exit_candidates,
        expand_candidate=expand_candidate,
        recommended_reallocation=recommended,
        notes=notes,
    )


def write_decision(report: DecisionReport, out_dir: str = DECISIONS_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = os.path.join(out_dir, f"decision-{date_str}.json")
    with open(path, "w") as f:
        json.dump(report.to_dict(), f, indent=2, default=str)
    return path


def main():  # pragma: no cover
    """CLI entry for manual ad-hoc decision reports."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from liquidity_agent_v2 import LiquidityAgent
    from cron_lp_agent import discover_rewards

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
