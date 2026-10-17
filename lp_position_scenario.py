"""Read-only top-up versus keep comparison. Conditional, never verified bounds.

Both branches start with the same existing position and idle pair-token wallet.
Both withdraw the entire position at the horizon and liquidate into an explicit
terminal token (pair quote by default) using branch-specific shared reserves. No I/O, signing, or execution.
"""
from copy import deepcopy
from decimal import Decimal as D, localcontext
from lp_mechanics import add_liquidity, remove_liquidity, num
from lp_liquidation import value_rewards
from lp_reward_math import estimate_batch


def _rewards(rows, batches, dp, precisions, account, now, end):
    tick = num(dp['distTickHours']) * 3600000
    if tick <= 0 or tick != tick.to_integral_value():
        raise ValueError('invalid tick interval')
    tick = int(tick)
    # Deployed scheduler requires tokenBalances.0 to exist; unfunded batches
    # do not consume ticks. New funding is outside this fixed-state scenario.
    active = [deepcopy(b) for b in batches if b['active'] and b['numTicksLeft'] > 0
              and b.get('tokenBalances') != [] and 'tokenBalances' in b]
    if len({b['_id'] for b in active}) != len(active):
        raise ValueError('duplicate batches')
    earnings, records = {}, []
    for batch in active:
        when = max(now, int(num(batch['lastTickTime'])) + tick)
        count = 0
        while when <= end and batch['numTicksLeft'] > 0:
            count += 1
            if count > 400:
                raise ValueError('explicit 400-tick simulation bound exceeded')
            own = estimate_batch(batch, rows, dp, precisions, account, when)
            if own['status'] != 'ok':
                raise ValueError(own['reason'])
            reserved = {}
            for row in rows:
                ent = estimate_batch(batch, rows, dp, precisions, row['account'], when)
                if ent['status'] != 'ok':
                    raise ValueError(ent['reason'])
                for payout in ent['entitlements']:
                    s = payout['symbol']
                    reserved[s] = reserved.get(s, D(0)) + num(payout['payout_share'])
            records.append({'batch_id': batch['_id'], 'at_ms': when, 'our_result': own,
                            'reserved': {s: str(q) for s, q in reserved.items()}})
            for payout in own['entitlements']:
                s = payout['symbol']
                earnings[s] = earnings.get(s, D(0)) + num(payout['payout_share'])
            for token in batch['tokenBalances']:
                token['quantity'] = str(num(token['quantity']) - reserved.get(token['symbol'], D(0)))
                num(token['quantity'])
            batch['numTicksLeft'] -= 1
            batch['lastTickTime'] = when
            when += tick
    return earnings, records


def position_scenario(pool, wallet, participants, batches, precisions, market_params,
                      distribution_params, account, at_ms, end_ms, deposit_amounts,
                      *, routes=None, terminal_unit=None, max_hops=3):
    """Compare a specified top-up with keeping position + idle funds unchanged.

    Requires complete same-pool participants and a positive existing position.
    All quotes are returned verbatim from mechanics, not reconstructed from marks.
    Any unroutable terminal asset in either branch suppresses the advantage.
    Only the pair-token idle wallet is included; unrelated idle funds are outside scope.
    """
    with localcontext() as ctx:
        ctx.prec = 100
        if end_ms <= at_ms:
            raise ValueError('horizon must end after snapshot')
        pair = pool['tokenPair']
        a, b = pair.split(':')
        settle = b if terminal_unit is None else terminal_unit
        if any(p['tokenPair'] != pair for p in participants):
            raise ValueError('mixed participants')
        if any(x['tokenPair'] != pair for x in batches):
            raise ValueError('mixed batches')
        if len({p['account'] for p in participants}) != len(participants):
            raise ValueError('duplicate participants')
        if sum(num(p['shares']) for p in participants) != num(pool['totalShares']):
            raise ValueError('participant shares do not match pool')
        existing = next((p for p in participants if p['account'] == account), None)
        if existing is None or num(existing['shares']) <= 0:
            raise ValueError('positive existing position required')
        prior = {'shares': str(existing['shares']), 'timeFactor': existing['timeFactor']}
        funds = {s: num(wallet.get(s, 0)) for s in (a, b)}
        deposit = add_liquidity(pool, deposit_amounts[a], deposit_amounts[b], precisions,
                                funds, at_ms, prior)
        moved_rows = deepcopy(participants)
        moved_own = next(p for p in moved_rows if p['account'] == account)
        moved_own['shares'] = str(num(existing['shares']) + num(deposit['shares_added']))
        moved_own['timeFactor'] = deposit['timeFactor']

        def branch(state, rows, own_shares, spend):
            earnings, ticks = _rewards(rows, batches, distribution_params, precisions,
                                      account, at_ms, end_ms)
            withdrawal = remove_liquidity(state, own_shares, '100', precisions)
            amounts = {s: funds[s] - num(spend.get(s, 0)) + num(withdrawal['amounts'][s])
                       for s in (a, b)}
            for s, q in earnings.items():
                amounts[s] = amounts.get(s, D(0)) + q
            liquidation = value_rewards(withdrawal['pool_after'],
                [{'symbol': s, 'amount': str(q)} for s, q in amounts.items()],
                routes, precisions, market_params, settle, max_hops=max_hops)
            missing = liquidation['unroutable']
            legs = liquidation['sales'].get(a, {}).get('legs', [])
            # Compatibility alias only for the historical single-hop base sale.
            terminal_quote = legs[0] if settle == b and len(legs) == 1 else None
            return {'withdrawal': withdrawal, 'terminal_quote': terminal_quote,
                    'terminal': liquidation['total'], 'liquidation': liquidation,
                    'terminal_assets': {s: str(q) for s, q in amounts.items()},
                    'ticks': ticks,
                    'our_rewards': {s: str(q) for s, q in earnings.items()},
                    'unliquidated_assets': {s: str(amounts[s]) for s in missing},
                    'unliquidated_rewards': {s: str(q) for s, q in earnings.items()
                                            if s in missing and q > 0}}

        stay = branch(pool, participants, existing['shares'], {})
        move = branch(deposit['pool_after'], moved_rows, moved_own['shares'], deposit['amounts'])
        complete_liquidation = all(x['liquidation']['coverage_complete'] for x in (stay, move))
        affected = {pair}
        for result in (stay, move):
            for sale in result['liquidation']['sales'].values():
                affected.update(leg['pool'] for leg in sale['legs'])
        return {'pool': pair, 'horizon_start_ms': at_ms, 'horizon_end_ms': end_ms,
                'position_before': deepcopy(prior), 'position_after': deepcopy(moved_own),
                'time_factor_delta_ms': deposit['timeFactor'] - prior['timeFactor'],
                'deposit': deposit, 'stay': stay, 'move': move, 'terminal_unit': settle,
                'liquidation_complete': complete_liquidation,
                'affected_pools': sorted(affected), 'execution_allowed': False,
                'conditional_advantage': str(num(move['terminal']) - num(stay['terminal']))
                    if complete_liquidation else None,
                'coverage_complete': False, 'decision': 'observe; conditional scenario only',
                'assumptions': ['fixed external participants and reserves; future fees unknown and excluded',
                                'no new funding or batch edits; nominal ticks; eventual successful settlement',
                                'constant parameters; no execution latency or failures',
                                'sorted-symbol sequential liquidation; bounded simple paths, not globally optimal',
                                'snapshot atomicity, RC capacity and future price risk unverified'],
                'scope': 'specified top-up versus keep; no cross-pool opportunity-cost optimization'}


# Recognized deployed contract versions; unknown versions fail closed.
EXPECTED = {'marketpools': 'fd6d750418df6b23de96fa0ba8cb006828dcbc272d566c6847204b552a41004c', 'distribution': '6bd095db17d7c2b76ce534b8235b9fcf05c4f9c033bc2d1d87ede518c5dd4178'}

from decimal import ROUND_DOWN

def decide_topup_vs_keep(data, *, terminal_unit='SWAP.HIVE', max_hops=3):
    for name, expected in EXPECTED.items():
        if data['contracts'][name]['codeHash'] != expected:
            raise ValueError('unrecognized contract '+name)
    funds = {x['symbol']:x['balance'] for x in data['balances']}
    prec = {x['symbol']:x['precision'] for x in data['tokens']}
    pools = {x['tokenPair']:x for x in data['pools']}
    now = data['captured_at_ms']; comparisons = []
    for pos in data['ours']:
        pair = pos['tokenPair']; a,b = pair.split(':'); pool = pools[pair]
        try:
            with localcontext() as ctx:
                ctx.prec=100
                qa = min(D(funds.get(a,'0')),D(funds.get(b,'0'))*D(pool['baseQuantity'])/D(pool['quoteQuantity']))
                qa = qa.quantize(D(1).scaleb(-prec[a]),rounding=ROUND_DOWN)
                qb = (qa*D(pool['quoteQuantity'])/D(pool['baseQuantity'])).quantize(D(1).scaleb(-prec[b]),rounding=ROUND_DOWN)
            if min(qa,qb)<=0:
                comparisons.append({'pool':pair,'decision':'observe','reason':'paired deposit rounds to zero'})
                continue
            result = position_scenario(pool,funds,data['participants'][pair],
                      [x for x in data['batches'] if x['tokenPair']==pair],prec,
                      data['marketpools_params'],data['distribution_params'],data['account'],
                      now,now+7*86400000,{a:str(qa),b:str(qb)},
                      routes=pools, terminal_unit=terminal_unit, max_hops=max_hops)
            comparisons.append(result)
        except (ValueError,KeyError) as exc:
            comparisons.append({'pool':pair,'decision':'observe','reason':str(exc)})
    return {'comparisons':comparisons,'transactions_submitted':0,
            'terminal_unit':terminal_unit, 'max_hops':max_hops, 'execution_allowed':False,
            'status':'independent conditional topups; shared wallet NOT jointly allocated'}

