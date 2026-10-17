"""Shared-wallet evidence adapter; pure, no network or execution.

Conditional position projections are NOT economic evidence. Require separately
supplied identity-bound economic bounds and explicit verification flags. This
adapter validates their shape; it does not independently certify their truth.
Freshness is only an age gate, never protection against reserve or price drift.
"""
from lp_mechanics import num


def promote(comparison, *, captured_at_ms, now_ms, max_staleness_ms):
    """Raise ValueError unless the scenario qualifies as joint evidence."""
    pair = comparison['pool']
    if comparison.get('liquidation_complete') is not True:
        raise ValueError('incomplete liquidation coverage: advantage unknown')
    if comparison.get('coverage_complete') is not True:
        raise ValueError('reward/fee/risk coverage incomplete: conditional projection is not evidence')
    evidence = comparison.get('economic_evidence')
    if not isinstance(evidence, dict):
        raise ValueError('explicit verified economic evidence required')
    for flag in ('mechanics_verified', 'quotes_fresh', 'coverage_complete'):
        if evidence.get(flag) is not True:
            raise ValueError('economic evidence gate failed: ' + flag)
    for key in ('pool', 'terminal_unit', 'horizon_start_ms', 'horizon_end_ms'):
        if evidence.get(key) != comparison.get(key):
            raise ValueError('economic evidence identity mismatch: ' + key)
    if (evidence.get('deposit') != comparison.get('deposit') or
            evidence.get('affected_pools') != comparison.get('affected_pools')):
        raise ValueError('economic evidence action mismatch')
    if evidence.get('captured_at_ms') != captured_at_ms:
        raise ValueError('economic evidence snapshot mismatch')
    from lp_decisions import d
    bounds = {k: str(d(evidence[k])) for k in
              ('move_earnings_lower', 'stay_earnings_upper', 'switching_cost_upper')}
    unit = comparison.get('terminal_unit')
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError('terminal unit missing')
    start, end = comparison['horizon_start_ms'], comparison['horizon_end_ms']
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (start, end)) or end <= start:
        raise ValueError('invalid shared horizon')
    if (not all(isinstance(x, int) and not isinstance(x, bool) for x in
                (captured_at_ms, now_ms, max_staleness_ms)) or
            captured_at_ms <= 0 or max_staleness_ms < 0 or now_ms < captured_at_ms or
            now_ms - captured_at_ms > max_staleness_ms):
        raise ValueError('quotes not fresh: snapshot age unknown or beyond explicit staleness bound')
    for branch, field in ((comparison.get('move'), 'move'), (comparison.get('stay'), 'stay')):
        if not isinstance(branch, dict) or 'terminal' not in branch:
            raise ValueError('missing %s branch terminal' % field)
        num(branch['terminal'])
    deposit = comparison.get('deposit') or {}
    spend = {s: d(q) for s, q in (deposit.get('amounts') or {}).items()}
    if num(deposit.get('shares_added', '0')) <= 0 or not spend:
        raise ValueError('degenerate deposit: not a real top-up')
    affected = comparison['affected_pools']
    if (not isinstance(affected, list) or not affected or
            not all(isinstance(x, str) and x.strip() for x in affected)):
        raise ValueError('invalid affected pools')
    return {
        'id': pair,
        'mechanics_verified': True,
        'quotes_fresh': True,
        'coverage_complete': True,
        'terminal_unit': unit,
        'horizon_start_ms': start,
        'horizon_end_ms': end,
        **bounds,
        'spend': {s: str(q) for s, q in spend.items()},
        'conflicts': sorted(set(affected) | {pair}),
        'execution_allowed': False,
        'provenance': {'source': 'caller-supplied economic evidence; not certified by adapter',
                       'captured_at_ms': captured_at_ms, 'checked_at_ms': now_ms},
    }


def candidates_from_assessment(assessment, *, captured_at_ms, now_ms, max_staleness_ms):
    """Split a decide_topup_vs_keep result into qualifying candidates and rejections."""
    candidates, rejected = [], []
    for comparison in assessment['comparisons']:
        try:
            candidates.append(promote(comparison, captured_at_ms=captured_at_ms,
                                      now_ms=now_ms, max_staleness_ms=max_staleness_ms))
        except (KeyError, ValueError, TypeError) as exc:
            rejected.append({'pool': comparison.get('pool', '?'), 'reason': str(exc)})
    return candidates, rejected


def joint_allocation(assessment, balances, *, captured_at_ms, now_ms, max_staleness_ms,
                     max_candidates=18):
    """Shared-wallet subset selection over qualifying conditional scenarios.

    Separate stage: per-position comparisons remain the primary report; this
    adds the cross-pool view. All results are conditional; nothing executes.
    """
    from lp_decisions import choose_joint
    candidates, rejected = candidates_from_assessment(assessment, captured_at_ms=captured_at_ms,
                                                      now_ms=now_ms,
                                                      max_staleness_ms=max_staleness_ms)
    selection = choose_joint(candidates, balances, max_candidates=max_candidates)
    return {'selection': selection, 'rejected': rejected, 'candidate_count': len(candidates),
            'conditional': True, 'execution_allowed': False, 'transactions_submitted': 0,
            'scope': 'shared-wallet subset of supplied scenarios; reserves not resimulated '
                     'across selected moves'}
