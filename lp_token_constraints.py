#!/usr/bin/env python3
"""LP Agent Token Constraints — Pool vs Stake discipline per token, per account.

2026-10-05: Encodes Tydyn's token usage rules as HARD blocks.

Key distinction per token:
  - can_stake      : a staking contract exists and pays dividends above a threshold
  - cannot_stake   : no staking contract; dividends come from holding, LP is the only yield
  - has_pool       : a pool exists; 'False' means LP is *structurally impossible*
  - prefer_hold    : holding beats LP (adds to no pool is a preference, not a permission)
  - prefer_not_lp  : included in LP only sparingly because the token has another purpose

Global staking thresholds are HARD caps (sum across ALL accounts must meet the floor
before any LP allocation may use that token). These are absolute, not preferences.
"""
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


class LPConstraints:
    """Per-account, per-token constraint surface."""

    GLOBAL_STAKING_THRESHOLDS: Dict[str, float] = {
        "CENT": 20000,
        "PIMP": 5000,
        "BBHO": 20000,  # confirmed by Tydyn (fact 262)
        # NOTE: BBH is deliberately ABSENT — BBH CANNOT be staked at all.
        # It pays weekly dividends on HOLDING, minimum ~10 BBH (see
        # HOLDING_DIVIDEND_THRESHOLDS below).
    }

    # 2026-10-05: BBH pays weekly dividends on plain HOLDING (no staking
    # contract exists). Minimum ~10 BBH (Tydyn's belief). These are NOT
    # staking thresholds — the enforcement is on liquid+staked balance,
    # not stake.
    HOLDING_DIVIDEND_THRESHOLDS: Dict[str, float] = {
        "BBH": 10,  # Tydyn's belief, approximate — confirm when convenient
    }

    POOL_EXISTS: Dict[str, bool] = {
        "BBHO": False,   # no pool — staking only
        "BBH": True,     # has at least one pool
        "ALIVE": True,
        "ALIVEM": True,
    }

    TOKEN_CONSTRAINTS: Dict[str, Dict] = {
        "CENT": {"staking_threshold": 20000, "max_lp_fraction": 0.50},
        "PIMP": {"staking_threshold": 5000, "max_lp_fraction": 0.50},
        "ALIVE": {"max_lp_fraction": 0.10, "min_liquid_balance": 5000, "purpose": "payments/donations/prizes"},
        "ALIVEM": {"max_lp_fraction": 0.10, "min_liquid_balance": 5000, "purpose": "payments/donations/prizes"},
        "BEE": {"max_lp_fraction": 0.05, "min_liquid_balance": 10000, "purpose": "pool-creation reserve"},
        "BBH": {"max_lp_fraction": 0.50, "purpose": "weekly dividends (hold)"},
        "BBHO": {"max_lp_fraction": 0.50, "purpose": "staking dividends"},
        "HSBIDAO": {"max_lp_fraction": 0.30},
        "PIXYDUST": {"max_lp_fraction": 0.30},
    }

    def pool_exists(self, token: str) -> bool:
        return self.POOL_EXISTS.get(token, True)

    def must_stake_before_lp(self, token: str) -> bool:
        """Hard rule: some tokens' dividends only pay when staked above threshold."""
        return token in self.GLOBAL_STAKING_THRESHOLDS

    def minimum_stake(self, token: str) -> float:
        """Returns the global threshold, or 0 if token has none."""
        return self.GLOBAL_STAKING_THRESHOLDS.get(token, 0.0)


if __name__ == "__main__":
    c = LPConstraints()
    print("CENT must stake:", c.must_stake_before_lp("CENT"), c.minimum_stake("CENT"))
    print("BBHO pool exists?", c.pool_exists("BBHO"), "threshold:", c.minimum_stake("BBHO"))
    print("BBH pool exists?", c.pool_exists("BBH"), "must stake first:", c.must_stake_before_lp("BBH"))
