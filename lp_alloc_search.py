"""Bounded portfolio allocation search: generate candidates, evaluate, rank.

Search is a *simulation* layer over compare_portfolios: it never submits
transactions, never mutates its inputs, and never ranks a candidate whose
joint valuation is incomplete (hold AND plan branches must both liquidate
completely, or the candidate is quarantined as not_rankable).

Deposit sequences are sized from LIQUID wallet funds only: scoped position
principal is withdrawn by the engine at horizon end and is never treated as
spendable here. The balanced size for one pool is bounded by the scarcer leg
at the snapshot ratio, then floored exactly (integer Fraction arithmetic, no
Decimal) to each token's recorded precision (0..8, per token), so a candidate
can never overspend through rounding.

Candidate generation is bounded: sequences are enumerated shortest-first
(singles, then pairs, ...) over distinct (pool, multiple) keys and the
enumeration stops at the evaluation budget; the stay branch counts as one
evaluation. The whole candidate set is evaluated in ONE compare_portfolios
call, so the stay scenario is simulated once, not once per candidate.
"""
from fractions import Fraction as F
from itertools import islice, permutations

from lp_portfolio import compare_portfolios

_STATUS = 'conditional-simulation'


def _frac(q):
    return q if isinstance(q, F) else F(str(q))


def _floor_to(value, precision):
    """Exact floor of a nonnegative Fraction at `precision` decimals."""
    q, _ = divmod(value.numerator * 10 ** precision, value.denominator)
    return F(q, 10 ** precision)


def _fmt(value, precision):
    """Exact plain decimal string for a Fraction at `precision` decimals."""
    n = value.numerator * 10 ** precision // value.denominator
    sign = '-' if n < 0 else ''
    digits = str(abs(n)).rjust(precision + 1, '0')
    if precision:
        digits = digits[:-precision] + '.' + digits[-precision:]
        digits = digits.rstrip('0').rstrip('.')
    return sign + digits


def _balanced_amounts(pair, multiple, pools, funds, precisions):
    """Largest balanced deposit for one pool at `multiple` of liquid funds.

    The balanced size is bounded by the scarcer leg at the snapshot ratio
    (base-equivalent), then floored to each token's recorded precision.
    Returns None when a leg rounds to zero (candidate infeasible).
    """
    if pair not in pools:
        raise ValueError('unknown pool in search scope: ' + pair)
    p = pools[pair]
    a, b = pair.split(':')
    base_q, quote_q = _frac(p['baseQuantity']), _frac(p['quoteQuantity'])
    if base_q <= 0 or quote_q <= 0:
        return None
    ratio = quote_q / base_q  # quote units per base unit
    fund_a, fund_b = _frac(funds.get(a, '0')), _frac(funds.get(b, '0'))
    cap_a = min(fund_a, fund_b / ratio)
    qa = _floor_to(cap_a * _frac(multiple), precisions[a])
    if qa <= 0:
        return None
    qb = _floor_to(qa * ratio, precisions[b])
    if qb <= 0:
        return None
    return {a: _fmt(qa, precisions[a]), b: _fmt(qb, precisions[b])}


def _viable_keys(pools, wallet, precisions, pairs, multiples):
    """(pair, multiple) combos feasible against the FULL liquid wallet.

    Later actions in a sequence can only spend less, so full-wallet viability
    is a sound filter for sequence generation.
    """
    keys = []
    for pair in sorted(pairs):
        for mult in multiples:
            if _balanced_amounts(pair, mult, pools, wallet, precisions) is not None:
                keys.append((pair, mult))
    return keys


def _bounded_candidates(keys, max_actions, cap):
    """Up to `cap` candidate sequences, shortest-first; overflow flag.

    Depth n is enumerated only after depth n-1 is exhausted; when the budget
    is hit, enumeration stops and truncated=True (deeper sequences exist).
    """
    plans = []
    for n in range(1, min(max_actions, len(keys)) + 1):
        room = cap - len(plans)
        if room <= 0:
            return plans, bool(keys)
        batch = list(islice(permutations(keys, n), room + 1))
        if len(batch) > room:
            return plans, True
        plans.extend(batch)
    return plans, False


def _sequence_plan(combo, pools, wallet, precisions):
    """Recompute amounts sequentially so each action spends remaining funds.

    Returns (id, actions) or None when a leg becomes infeasible mid-sequence.
    """
    funds = {s: _frac(q) for s, q in wallet.items()}
    actions, parts = [], []
    for pair, mult in combo:
        amounts = _balanced_amounts(pair, mult, pools, funds, precisions)
        if amounts is None:
            return None
        for sym, qty in amounts.items():
            funds[sym] -= _frac(qty)
        actions.append({'pool': pair, 'amounts': amounts})
        parts.append(f'{pair}@{mult}')
    return '+'.join(parts), actions


def search_portfolios(pools, wallet, participants, batches, precisions,
                      market_params, distribution_params, account, at_ms,
                      end_ms, *, terminal_unit, multiples=('0.25', '0.5', '1'),
                      pairs=None, max_actions=1, budget=400, max_hops=3):
    """Evaluate a bounded candidate set with compare_portfolios.

    Candidates: ordered deposit sequences up to max_actions distinct
    (pool, multiple) keys, sized from the liquid wallet only. Ranked
    candidates have COMPLETE joint coverage (stay and move branches both
    liquidate fully) and are ordered by conditional_advantage descending.
    Refusals and incomplete joint valuations are quarantined in
    not_rankable with terminal=None; unknown values never rank.
    """
    if pairs is None:
        pairs = sorted(pools)
    multiples = tuple(multiples)
    if not multiples or any(_frac(m) <= 0 for m in multiples):
        raise ValueError('multiples must be positive')
    if budget <= 0 or max_actions < 1 or not isinstance(max_actions, int):
        raise ValueError('invalid search bounds')
    unknown = [p for p in pairs if p not in pools]
    if unknown:
        raise ValueError('pairs outside pool graph: ' + ','.join(sorted(unknown)))

    keys = _viable_keys(pools, wallet, precisions, pairs, multiples)
    combos, truncated = _bounded_candidates(keys, max_actions,
                                            max(0, budget - 1))
    plans, plan_actions = [], {}
    for combo in combos:
        built = _sequence_plan(combo, pools, wallet, precisions)
        if built is None:
            continue
        cid, actions = built
        if cid in plan_actions:
            continue
        plan_actions[cid] = actions
        plans.append({'id': cid, 'actions': actions})

    result = compare_portfolios(
        pools, wallet, participants, batches, precisions, market_params,
        distribution_params, account, at_ms, end_ms, plans,
        terminal_unit=terminal_unit, max_hops=max_hops)

    ranked, not_rankable = [], []
    for row in result['comparisons']:
        cid = row['id']
        actions = plan_actions.get(cid, [])
        if row['conditional_advantage'] is not None:
            entry = dict(row['move'])
            entry.update({'id': cid, 'actions': actions,
                          'conditional_advantage': row['conditional_advantage']})
            ranked.append(entry)
        else:
            # Refusal OR incomplete joint coverage: unknown cannot rank.
            not_rankable.append({
                'id': cid, 'actions': actions, 'terminal': None,
                'conditional_advantage': None,
                'refusal': row.get('refusal')
                or 'joint valuation incomplete: stay or move branch did not '
                   'liquidate completely; unknown cannot rank',
                'execution_allowed': False})

    ranked.sort(key=lambda c: (-F(c['conditional_advantage']), c['id']))
    return {'status': _STATUS, 'ranked': ranked, 'not_rankable': not_rankable,
            'stay': result['stay'],
            'evaluated': 1 + len(result['comparisons']),
            'truncated': truncated,
            'transactions_submitted': 0,
            'provenance': {'multiples': list(multiples), 'pairs': list(pairs),
                           'max_actions': max_actions, 'budget': budget,
                           'max_hops': max_hops}}
