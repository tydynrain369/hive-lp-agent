"""Pure direct-deposit counterfactual; not a forecast or execution instruction.
All batches and complete participants must come from one pool. Simulate fixed
reserves/participants, successful settlement, no new funding and nominal ticks.
Terminal liquidation uses the pool's quote token, never indicative USD marks.
"""
from copy import deepcopy
from decimal import Decimal as D, ROUND_DOWN, localcontext
from lp_mechanics import add_liquidity, remove_liquidity, swap_exact_in, num
from lp_reward_math import estimate_batch


def direct_scenario(pool, wallet, participants, batches, precisions, market_params,
                    distribution_params, account, at_ms, end_ms):
    with localcontext() as ctx:
        ctx.prec = 100
        return _direct(pool, wallet, participants, batches, precisions, market_params,
                       distribution_params, account, at_ms, end_ms)


def _direct(pool, wallet, participants, batches, precisions, mp, dp, account, now, end):
    if end <= now:
        raise ValueError('horizon must end after snapshot')
    pair = pool['tokenPair']; a, b = pair.split(':')
    if any(p['tokenPair'] != pair for p in participants):
        raise ValueError('mixed participants')
    if any(x['tokenPair'] != pair for x in batches):
        raise ValueError('mixed batches')
    if len({p['account'] for p in participants}) != len(participants):
        raise ValueError('duplicate participants')
    if sum(num(p['shares']) for p in participants) != num(pool['totalShares']):
        raise ValueError('participant shares do not match pool; incomplete or drifting snapshot')
    if any(p['account'] == account for p in participants):
        raise ValueError('new-position scenario only; existing position requires stay-position valuation')
    funds = {s:num(wallet.get(s, 0)) for s in (a, b)}
    qa = min(funds[a], funds[b]*num(pool['baseQuantity'])/num(pool['quoteQuantity']))
    qa = qa.quantize(D(1).scaleb(-precisions[a]), rounding=ROUND_DOWN)
    qb = (qa*num(pool['quoteQuantity'])/num(pool['baseQuantity'])).quantize(D(1).scaleb(-precisions[b]), rounding=ROUND_DOWN)
    deposit = add_liquidity(pool, str(qa), str(qb), precisions, funds, now)
    rows = deepcopy(participants) + [{'account':account, 'tokenPair':pair,
        'shares':deposit['shares_added'], 'timeFactor':now}]
    tick = num(dp['distTickHours'])*3600000
    if tick <= 0 or tick != tick.to_integral_value():
        raise ValueError('invalid tick interval')
    tick = int(tick); records=[]; earnings={}
    active=[deepcopy(x) for x in batches if x['active'] and x['numTicksLeft'] > 0]
    if len({x['_id'] for x in active}) != len(active):
        raise ValueError('duplicate batches')
    for batch in active:
        when=max(now, int(num(batch['lastTickTime']))+tick)
        count=0
        while when <= end and batch['numTicksLeft'] > 0:
            count += 1
            if count > 400: raise ValueError('explicit 400-tick simulation bound exceeded')
            own = estimate_batch(batch, rows, dp, precisions, account, when)
            if own['status'] != 'ok': raise ValueError(own['reason'])
            reserved={}
            # Reserve every participant payout, including queued payouts, from the
            # next tick's budget. Never simply subtract the unrounded whole-pool rate.
            for row in rows:
                ent = estimate_batch(batch, rows, dp, precisions, row['account'], when)
                if ent['status'] != 'ok': raise ValueError(ent['reason'])
                for payout in ent['entitlements']:
                    s=payout['symbol']; reserved[s]=reserved.get(s,D(0))+num(payout['payout_share'])
            records.append({'batch_id':batch['_id'], 'at_ms':when, 'our_result':own,
                            'reserved':{s:str(q) for s,q in reserved.items()}})
            for payout in own['entitlements']:
                s=payout['symbol']; earnings[s]=earnings.get(s,D(0))+num(payout['payout_share'])
            for token in batch['tokenBalances']:
                token['quantity']=str(num(token['quantity'])-reserved.get(token['symbol'],D(0)))
                num(token['quantity'])
            batch['numTicksLeft']-=1; batch['lastTickTime']=when; when+=tick
    withdrawal=remove_liquidity(deposit['pool_after'],deposit['shares_added'],'100',precisions)
    moved={s:funds[s]-num(deposit['amounts'][s])+num(withdrawal['amounts'][s]) for s in funds}
    for s,q in earnings.items(): moved[s]=moved.get(s,D(0))+q
    def liquidate(state, amounts):
        quote=None; total=amounts.get(b,D(0))
        if amounts.get(a,D(0)) > 0:
            quote=swap_exact_in(state,a,str(amounts[a]),precisions,mp)
            total+=num(quote['amount_out'])
        return total,quote
    stay,stay_quote=liquidate(pool,funds)
    move,move_quote=liquidate(withdrawal['pool_after'],moved)
    unpriced={s:str(q) for s,q in moved.items() if s not in (a,b) and q>0}
    return {'pool':pair,'horizon_start_ms':now,'horizon_end_ms':end,
        'deposit':deposit,'withdrawal':withdrawal,'ticks':records,
        'our_rewards':{s:str(q) for s,q in earnings.items()},
        'stay_terminal_quote':stay_quote,'move_terminal_quote':move_quote,
        'terminal_unit':b,'stay_terminal':str(stay),'move_terminal':str(move),
        'unliquidated_rewards':unpriced,
        'conditional_advantage':str(move-stay) if not unpriced else None,
        'coverage_complete':False,'decision':'observe; conditional scenario only',
        'assumptions':['no external trades or reserve-price changes; no future fees',
            'no participant changes, new funding or batch edits',
            'nominal ticks; successful eventual settlement including pending payments',
            'constant contract parameters; no execution latency or failures',
            'same terminal liquidation of both branches; no token-charge modeled for add/remove',
            'RC capacity, future price risk and snapshot atomicity not verified'],
        'scope':'direct idle-balance deposit into a new position; no opportunity-cost comparison to other pools'}
