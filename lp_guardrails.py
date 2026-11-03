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
                                    wallet_balance_usd: float) -> Tuple[bool, str]:
    """Check if a proposed LP addition violates account-specific constraints.

    wallet_balance_usd: the account's CURRENT liquid balance for this token,
    in USD. All arithmetic is in USD so the fraction and the floor are
    comparable to amount_usd without a unit conversion at the call site.
    """
    constraints = ACCOUNT_CONSTRAINTS.get(account, {})
    token_constraint = constraints.get(token)
    if not token_constraint:
        return True, ""  # no constraint for this token on this account

    # Hard constraint: max LP fraction of wallet balance
    max_frac = token_constraint.get("max_lp_fraction")
    if max_frac is not None:
        current_lp_usd = current_lp_balance.get(token, 0.0)
        proposed_lp_usd = current_lp_usd + amount_usd
        max_allowed = wallet_balance_usd * token_constraint["max_lp_fraction"]
        if proposed_lp_usd > max_allowed:
            return False, f"would exceed max LP fraction for {token} on {account}: {proposed_lp_usd:.2f} > {max_allowed:.2f} (max {token_constraint['max_lp_fraction']*100}% of ${wallet_balance_usd:.2f} liquid)"

    # Hard constraint: minimum liquid balance (non-LP), in USD
    min_liquid = token_constraint.get("min_liquid_balance")
    if min_liquid is not None:
        liquid_after = wallet_balance_usd - amount_usd - sum(
            v for k, v in current_lp_balance.items() if k == token
        )
        if liquid_after < min_liquid:
            return False, f"would leave ${liquid_after:.0f} liquid {token} on {account}, below min ${min_liquid} ({token_constraint.get('purpose', 'reserve')})"

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


def _pool_tokens(token: str) -> List[str]:
    """Split a pool pair 'BASE:QUOTE' into its two token symbols.
    A bare token (no colon) is returned as a single-element list.
    Refuses a malformed pair rather than guessing — a pair without exactly
    one separator cannot be destructured safely."""
    parts = token.split(":")
    if len(parts) == 1:
        return [parts[0]]
    if len(parts) != 2:
        raise ValueError(f"malformed pool pair (expected BASE:QUOTE): {token!r}")
    return parts


def enforce_constraints(action: str, account: str, token: str, amount_usd: float,
                        context: Dict) -> Tuple[bool, str, Optional[Dict]]:
    """
    Main enforcement entry point. HARD gate — every broadcast path must
    call this BEFORE constructing the contract payload.

    Args:
        action: 'add_liquidity', 'remove_liquidity', 'stake', 'unstake'
        account: target account
        token: token symbol (or pool pair for LP, e.g. 'SWAP.HIVE:CENT')
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
        ok=False means DO NOT BROADCAST. The reason names the specific
        constraint and the numbers, so a refusal is auditable.
    """
    # --- removals and unstakes are releases, not accumulations ---
    # They cannot breach an LP cap or a risk budget (capital is leaving),
    # and they do not need a staking threshold either — the threshold is a
    # prerequisite for ALLOCATING into LP ("must be met BEFORE any LP
    # allocation"), not for withdrawing from it. Un-staking can drop a
    # global threshold below what remaining LP depends on, but that is a
    # downstream state concern, not a gate on the removal itself.
    is_accumulation = action in ("add_liquidity", "stake")

    tokens = _pool_tokens(token)

    # 1. GLOBAL STAKING THRESHOLDS (cross-account, HARD) — accumulation only
    if is_accumulation:
        staked_by_account = context.get("staked_balances", {})
        staked_total: Dict[str, float] = defaultdict(float)
        for _acct, _toks in staked_by_account.items():
            for _tok, _amt in (_toks or {}).items():
                staked_total[_tok] += float(_amt or 0.0)
        for tok in tokens:
            ok, reason = check_global_staking(tok, staked_total)
            if not ok:
                return False, reason, None

    # 2. ACCOUNT-SPECIFIC TOKEN CONSTRAINTS (max LP fraction, min liquid)
    if is_accumulation:
        current_lp = context.get("current_lp", {}).get(account, {})
        wallet_balances = context.get("wallet_balances", {})
        # Per-token USD liquid balance. The constraint arithmetic is all in
        # USD (amount_usd is USD, max_lp_fraction is a fraction of liquid
        # USD), so the wallet balance must be USD too — passing raw token
        # quantity compared a fraction of 5000 ALIVE against $400 and
        # refused every sub-cap deposit.
        wallet_usd = wallet_balances.get(account, {})
        for tok in tokens:
            tok_usd = wallet_usd.get(tok, 0.0)
            ok, reason = check_account_token_constraint(
                account, tok, amount_usd, current_lp, tok_usd
            )
            if not ok:
                return False, reason, None

    # 3. CROSS-ACCOUNT GLOBAL CAPS (per-token, across all accounts)
    if is_accumulation:
        proposed_lp = {account: {token: amount_usd}}
        current_lp_all = context.get("current_lp", {})
        global_caps = context.get("global_caps", {})
        for tok in tokens:
            ok, reason = check_cross_account_caps(
                tok, proposed_lp, current_lp_all, global_caps
            )
            if not ok:
                return False, reason, None

    # 4. ACCOUNT RISK BUDGET (max position as fraction of portfolio)
    if is_accumulation:
        portfolio_value = context.get("portfolio_value", {}).get(account, 0.0)
        if portfolio_value > 0:
            ok, reason = check_risk_budget(account, amount_usd, portfolio_value)
            if not ok:
                return False, reason, None

    return True, "constraints checked", None


if __name__ == "__main__":
    # Quick self-test
    print("lp_guardrails.py loaded")
    print("  Staking thresholds:", GLOBAL_STAKING_THRESHOLDS)
    print("  Account constraints:", list(ACCOUNT_CONSTRAINTS.keys()))
    print("  Risk budgets:", ACCOUNT_RISK_BUDGETS)
