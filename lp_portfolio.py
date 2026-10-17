"""Pure, fixed-state portfolio rehearsals. No network, keys, or execution.

Participant tables define the position scope and must be complete per pool.
All supplied wallet tokens are included. Plans contain ordered, explicit deposits;
there is no search over sizes, swaps to fund deposits, or future trading model.
"""
from copy import deepcopy
from decimal import Decimal as D
from lp_mechanics import add_liquidity, remove_liquidity, num, text, guarded
from lp_liquidation import value_rewards
from lp_position_scenario import _rewards


@guarded
def portfolio_scenario(pools, wallet, participants, batches, precisions,
                       market_params, distribution_params, account, at_ms, end_ms,
                       actions, *, terminal_unit, max_hops=3):
    """Commit deposits, accrue scoped positions, withdraw all, aggregate and sell.

    Missing scoped participant tables fail closed. Unroutable assets make the
    terminal value unknown. The caller must include every held position in scope;
    this pure function cannot discover omitted holdings from route-only pools.
    """
    if (not all(isinstance(t, int) and not isinstance(t, bool) for t in (at_ms, end_ms))
            or at_ms < 0 or end_ms <= at_ms):
        raise ValueError('invalid horizon')
    if not pools or not isinstance(terminal_unit, str) or not terminal_unit:
        raise ValueError('pool graph and terminal unit required')
    if not isinstance(actions, list):
        raise ValueError('ordered action list required')
    graph, rows = deepcopy(pools), deepcopy(participants)
    funds = {s: num(q) for s, q in wallet.items()}
    for pair, p in graph.items():
        if p['tokenPair'] != pair:
            raise ValueError('pool identity mismatch')
    for pair, table in rows.items():
        p = graph[pair]
        if any(r['tokenPair'] != pair for r in table):
            raise ValueError('mixed participants')
        if len({r['account'] for r in table}) != len(table):
            raise ValueError('duplicate participants')
        if sum((num(r['shares']) for r in table), D(0)) != num(p['totalShares']):
            raise ValueError('participant shares do not match pool')
        if any(num(r['timeFactor']) > at_ms for r in table):
            raise ValueError('participant newer than snapshot')
    if len({b['_id'] for b in batches}) != len(batches):
        raise ValueError('duplicate batches')
    deposits = []
    for action in actions:
        pair = action['pool']
        if pair not in rows:
            raise ValueError('complete participants required for deposit pool')
        a, b = pair.split(':')
        amounts = action['amounts']
        if set(amounts) != {a, b}:
            raise ValueError('deposit must specify exactly the pair tokens')
        own = next((r for r in rows[pair] if r['account'] == account), None)
        quote = add_liquidity(graph[pair], amounts[a], amounts[b], precisions,
                              funds, at_ms, own)
        for s, q in quote['amounts'].items():
            funds[s] = num(funds.get(s, D(0)) - num(q))
        graph[pair] = quote['pool_after']
        if own is None:
            own = dict(account=account, tokenPair=pair, shares='0', timeFactor=at_ms)
            rows[pair].append(own)
        own['shares'] = text(num(own['shares']) + num(quote['shares_added']))
        own['timeFactor'] = quote['timeFactor']
        deposits.append(quote)
    wallet_after = {s: text(q) for s, q in funds.items()}
    positions_after = deepcopy(rows)
    earnings, records, withdrawals = {}, {}, []
    # All horizon withdrawals precede ANY terminal sale: route pools may be held.
    for pair in sorted(rows):
        own = next((r for r in rows[pair] if r['account'] == account), None)
        if own is None or num(own['shares']) == 0:
            continue
        earned, ticks = _rewards(rows[pair], [b for b in batches if b['tokenPair'] == pair],
                                  distribution_params, precisions, account, at_ms, end_ms)
        records[pair] = ticks
        for s, q in earned.items():
            earnings[s] = earnings.get(s, D(0)) + q
            funds[s] = funds.get(s, D(0)) + q
        withdrawal = remove_liquidity(graph[pair], own['shares'], '100', precisions)
        graph[pair] = withdrawal['pool_after']
        for s, q in withdrawal['amounts'].items():
            funds[s] = funds.get(s, D(0)) + num(q)
        withdrawals.append(withdrawal)
    liquidation = value_rewards(graph[sorted(graph)[0]],
        [{'symbol': s, 'amount': text(q)} for s, q in funds.items()],
        graph, precisions, market_params, terminal_unit, max_hops=max_hops)
    affected = {q['pool'] for q in deposits + withdrawals}
    for sale in liquidation['sales'].values():
        affected.update(leg['pool'] for leg in sale['legs'])
    return {'deposits': deposits, 'wallet_after_deposits': wallet_after,
            'positions_after_deposits': positions_after, 'withdrawals': withdrawals,
            'our_rewards': {s: text(q) for s, q in earnings.items()}, 'ticks': records,
            'terminal_assets': {s: text(q) for s, q in funds.items()},
            'terminal': liquidation['total'], 'terminal_unit': terminal_unit,
            'liquidation': liquidation, 'liquidation_complete': liquidation['coverage_complete'],
            'affected_pools': sorted(affected), 'horizon_start_ms': at_ms, 'horizon_end_ms': end_ms,
            'coverage_complete': False, 'execution_allowed': False, 'transactions_submitted': 0,
            'scope': 'all supplied wallet tokens and scoped positions; specified deposits only',
            'assumptions': ['fixed external participants/reserves; future trading fees excluded',
                            'no new reward funding/edits; nominal ticks and eventual settlement',
                            'all deposits at snapshot; all withdrawals before terminal sales',
                            'sorted-symbol liquidation, bounded routes; not globally optimal',
                            'snapshot atomicity, RC, latency and price risk unverified']}


@guarded
def compare_portfolios(pools, wallet, participants, batches, precisions,
                       market_params, distribution_params, account, at_ms, end_ms,
                       plans, *, terminal_unit, max_hops=3):
    """Compare supplied deposit plans to hold, from identical copied inputs.

    No ranking of incomplete values and no production evidence/selection output.
    This is scenario evaluation, NOT continuous sizing or an allocation optimizer.
    """
    args = (pools, wallet, participants, batches, precisions, market_params,
            distribution_params, account, at_ms, end_ms)
    kwargs = dict(terminal_unit=terminal_unit, max_hops=max_hops)
    stay = portfolio_scenario(*args, [], **kwargs)
    comparisons = []
    for plan in plans:
        try:
            move = portfolio_scenario(*args, plan['actions'], **kwargs)
            complete = stay['liquidation_complete'] and move['liquidation_complete']
            comparisons.append({'id': plan['id'], 'move': move,
                'conditional_advantage': text(D(move['terminal']) - D(stay['terminal'])) if complete else None})
        except (ValueError, KeyError, TypeError) as exc:
            comparisons.append({'id': plan.get('id'), 'conditional_advantage': None, 'refusal': str(exc)})
    return {'stay': stay, 'comparisons': comparisons, 'terminal_unit': terminal_unit,
            'coverage_complete': False, 'execution_allowed': False, 'transactions_submitted': 0,
            'status': 'specified joint scenarios only; not economic evidence or an optimizer'}
