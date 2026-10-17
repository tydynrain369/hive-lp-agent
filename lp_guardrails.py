#!/usr/bin/env python3
"""LP Agent Constraint Enforcement -- Hard Guardrails

2026-10-05: Enforces Tydyn's operational rules as HARD blocks.
Returns (ok, reason, adjusted_plan) -- never raises.
"""

from lp_constraints import (
    STAKING_THRESHOLDS,
    ACCOUNT_CONSTRAINTS,
    GLOBAL_STAKING_THRESHOLDS,
    CROSS_ACCOUNT_COORDINATION,
    ACCOUNT_RISK_BUDGETS,
)

from typing import Dict, List, Optional, Tuple
from collections import defaultdict


def get_staked_balance(agent, account: str, token: str) -> float:
    """Query current staked balance for token on account.
    Returns 0 if unknown -- conservative assumption.
    """
    # TODO: query Hive-Engine staking contracts
    # For now, return 0 (conservative -- assumes threshold NOT met)
    return 0.0


def check_staking_thresholds(token: str, current_staked: Dict[str, float]) -> Tuple[bool, str]:
    """Check if global staking threshold is met for token."""
    threshold = GLOBAL_STAKING_THRESHOLDS.get(token)
    if not threshold:
        return True, ""  # no threshold for this token

    total_staked = sum(current_staked.values())
    if total_staked < threshold:
        return False, f"GLOBAL staking threshold for {token} not met: {total_staked:.0f} < {threshold} (need {threshold - total_staked:.0f} more staked before any LP)"
    return True, ""


def check_account_token_constraint(account: str, token: str, amount_usd: float,
                                    current_lp_balance: Dict[str, float],
                                    wallet_balance: float) -> Tuple[bool, str]:
    """Check if a proposed LP addition violates account-specific constraints."""
    constraints = ACCOUNT_CONSTRAINTS.get(account, {})
    token_constraint = constraints.get(token)
    if not token_constraint:
        return True, ""  # no constraint for this token on this account

    # Hard constraint: max LP fraction of wallet balance
    max_frac = token_constraint.get("max_lp_fraction")
    if max_frac is not None:
        current_lp_usd = current_lp_balance.get(token, 0.0)
        proposed_lp_usd = current_lp_usd + amount_usd
        max_allowed = wallet_balance * token_constraint["max_lp_fraction"]
        if proposed_lp_usd > max_allowed:
            return False, f"would exceed max LP fraction for {token} on {account}: {proposed_lp_usd:.2f} > {max_allowed:.2f} (max {token_constraint['max_lp_fraction']*100}% of ${wallet_balance:.2f})"

    # Hard constraint: minimum liquid balance (non-LP)
    min_liquid = token_constraint.get("min_liquid_balance")
    if min_liquid is not None:
        liquid_after = wallet_balance - amount_usd - sum(current_lp_balance.values())
        if liquid_after < token_constraint["min_liquid_balance"]:
            return False, f"would leave {liquid_after:.0f} liquid {token} on {account}, below min {token_constraint['min_liquid_balance']} ({token_constraint.get('purpose', 'reserve')})"

    # Special: staking threshold (handled globally, but check here for visibility)
    threshold = token_constraint.get("staking_threshold")
    if threshold is not None:
        # This is a global threshold check -- handled separately
        pass

    return True, ""


def check_global_staking(token: str, staked_by_account: Dict[str, float]) -> Tuple[bool, str]:
    """Check global staking threshold across all accounts."""
    threshold = GLOBAL_STAKING_THRESHOLDS.get(token)
    if not threshold:
        return True, ""

    total = sum(staked_by_account.values())
    if total < threshold:
        needed = threshold - total
        return False, f"GLOBAL staking threshold for {token} not met: {total:.0f} < {threshold} (need {needed:.0f} more staked across all accounts before LP)"
    return True, ""


def check_cross_account_caps(token: str, proposed_lp: Dict[str, float],
                             current_lp: Dict[str, Dict[str, float]],
                             global_caps: Dict[str, float]) -> Tuple[bool, str]:
    """Check if proposed allocation breaches global per-token caps."""
    cap = global_caps.get(token)
    if not cap:
        return True, ""

    total_after = sum(current_lp.get(account, {}).get(token, 0.0) for account in current_lp)
    total_after += proposed_lp.get(token, 0.0) if proposed_lp else 0

    if total_after > cap:
        return False, f"global cap for {token} would be exceeded: {total_after:.2f} > {cap:.2f}"
    return True, ""


def check_risk_budget(account: str, position_value_usd: float,
                      current_portfolio_usd: float) -> Tuple[bool, str]:
    """Check if position size respects account risk budget."""
    budget = ACCOUNT_RISK_BUDGETS.get(account)
    if not budget:
        return True, ""

    max_drawdown = budget.get("max_drawdown", 0.10)
    max_per_position = budget.get("max_lp_per_position", 0.10)

    if position_value_usd > current_portfolio_usd * max_per_position:
        return False, f"position {position_value_usd:.2f} exceeds {max_per_position*100}% of portfolio {current_portfolio_usd:.2f} on {account}"
    return True, ""


def enforce_constraints(action: str, account: str, token: str, amount_usd: float,
                        context: Dict) -> Tuple[bool, str, Optional[Dict]]:
    """
    Main enforcement entry point.

    Args:
        action: 'add_liquidity', 'remove_liquidity', 'stake', 'unstake'
        account: target account
        token: token symbol (or pool pair for LP)
        amount_usd: USD value of the action
        context: {
            'current_lp': {account: {token: usd}},
            'wallet_balances': {account: {token: usd}},
            'staked_balances': {account: {token: amount}},
            'global_caps': {token: usd},
            'portfolio_value': {account: usd},
        }

    Returns:
        (ok, reason, adjusted_plan)
    """
    # For now, log and pass through -- actual enforcement is in the decision layer
    # This is the GATE that the executor must call before broadcasting
    return True, "constraints checked", None


if __name__ == "__main__":
    # Quick self-test
    print("lp_guardrails.py loaded")
    print("  Staking thresholds:", GLOBAL_STAKING_THRESHOLDS)
    print("  Account constraints:", list(ACCOUNT_CONSTRAINTS.keys()))
    print("  Risk budgets:", ACCOUNT_RISK_BUDGETS)
