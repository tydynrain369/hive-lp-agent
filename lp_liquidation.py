"""Conditional reward liquidation; no network, signing or execution.

Reuse amount-aware simple-path enumeration from lp_mechanics. Sales share
projected reserves and use deterministic symbol order (NOT order optimization).
The hop bound is computational, not a claim of global routing optimality.
Unroutable assets remain unknown, never silently receive a zero valuation.
"""
from copy import deepcopy
from decimal import Decimal as D
from lp_mechanics import best_route, guarded, num, text, quant, QuoteError


@guarded
def simulate_sale(pools, precisions, params, token, amount, settle, max_hops=3, trace=None):
    """Commit projected pool changes only after a complete route succeeds.

    Returns (Decimal proceeds, hop count), or (None, 0) when no executable
    route is found within the hop bound. Never partially commits a failed sale.
    """
    amount = num(amount)
    for symbol in (token, settle):
        if symbol not in precisions:
            raise QuoteError('missing precision: ' + symbol)
    if quant(amount, precisions[token]) != amount:
        raise QuoteError('excess-precision sale amount')
    if token == settle or amount == 0:
        if trace is not None:
            trace.update(amount_in=text(amount), amount_out=text(amount), legs=[])
        return amount, 0
    try:
        route = best_route(pools, token, settle, text(amount), precisions,
                           params, max_hops=max_hops)
    except QuoteError:
        return None, 0
    pools.update(route['pools_after'])
    if trace is not None:
        trace.update(amount_in=text(amount), amount_out=route['amount_out'],
                     legs=deepcopy(route['legs']))
    return num(route['amount_out']), len(route['legs'])


@guarded
def value_rewards(pool, rewards, routes, precisions, params, settle, max_hops=3):
    """Return a conditional common-unit value, with explicit incomplete coverage.

    'total' is None unless every positive reward is routable. 'known_subtotal'
    is diagnostic only, not the value of the full portfolio. Inputs are copied.
    'projected_pools' permits subsequent simulated operations on shared reserves.
    """
    if isinstance(max_hops, bool) or not isinstance(max_hops, int) or not 1 <= max_hops <= 4:
        raise QuoteError('max_hops must be an integer from 1 to 4')
    quant(D(0), precisions[settle])
    pools = deepcopy(routes or {})
    pools[pool['tokenPair']] = deepcopy(pool)
    amounts = {}
    for reward in rewards or []:
        amount = num(reward['amount'])
        if amount:
            symbol = reward['symbol']
            amounts[symbol] = amounts.get(symbol, D(0)) + amount
    per, unroutable, sales = {}, [], {}
    for symbol in sorted(amounts):
        trace = {}
        got, _ = simulate_sale(pools, precisions, params, symbol,
                               amounts[symbol], settle, max_hops=max_hops, trace=trace)
        if got is None:
            unroutable.append(symbol)
        else:
            per[symbol] = text(got)
            sales[symbol] = trace
    subtotal = text(sum((num(v) for v in per.values()), D(0)))
    return {'total': None if unroutable else subtotal,
            'known_subtotal': subtotal, 'per_token': per, 'sales': sales,
            'projected_pool': pools[pool['tokenPair']], 'projected_pools': pools,
            'unroutable': unroutable, 'coverage_complete': not unroutable,
            'sale_order': sorted(amounts), 'max_hops': max_hops,
            'basis': 'fixed-state sequential liquidation; no future trading or order optimization',
            'execution_allowed': False}
