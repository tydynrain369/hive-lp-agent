"""Read-only feasibility triage, not a trade optimizer. No dollar gates or I/O."""
from decimal import Decimal, ROUND_DOWN, localcontext


def assess_pools(pe, portfolio, rewards):
    """Independent alternatives against the SAME wallet, never an additive plan.

    Direct amounts are reserve-ratio/precision screening only, not validated
    contract quotes. Every move requires downstream route/eligibility/cost
    comparison. PEPE support is visible metadata, not a return multiplier.
    """
    results=[]
    with localcontext() as ctx:
        ctx.prec=80
        for pair in sorted(set(pe.pools) | set(portfolio.positions)):
            pool=pe.pools.get(pair); held=portfolio.positions.get(pair)
            infos=rewards.get(pair,[])
            r={'pool':pair,'held':held is not None,'decision':'hold' if held else 'observe',
               'direct_amounts':None,'mechanics_status':'unknown',
               'fee_income':'unknown','switching_cost':'unknown','net_advantage':'unknown',
               'reward_batch_ids':sorted({i['batch_id'] for i in infos if 'batch_id' in i}),
               'pepe_related':'PEPE' in pair.split(':') or any(i.get('mined_token')=='PEPE' for i in infos),
               'reason':'route quotes, reward entitlement and net-advantage comparison required',
               'budget_semantics':'independent alternative; cannot combine these rows without reserving funds'}
            if not infos:
                r['reward_status']='no funded distribution/mining discovered; fees still unknown'
            else:
                r['reward_status']='funded budgets discovered; not account earnings'
            if pool is None:
                r['reason']='missing pool snapshot'; results.append(r); continue
            a,b=pool.base,pool.quote
            pa,pb=pe.token_precisions.get(a),pe.token_precisions.get(b)
            if pa is None or pb is None:
                r['reason']='missing token precision'; results.append(r); continue
            ra,rb=Decimal(str(pool.base_reserve)),Decimal(str(pool.quote_reserve))
            if not ra.is_finite() or not rb.is_finite() or min(ra,rb)<=0:
                r['reason']='invalid pool reserves'; results.append(r); continue
            ba=portfolio.balances.get(a); bb=portfolio.balances.get(b)
            qa=Decimal(str(ba.balance)) if ba else Decimal(0)
            qb=Decimal(str(bb.balance)) if bb else Decimal(0)
            if not qa.is_finite() or not qb.is_finite() or min(qa,qb)<0:
                r['reason']='invalid liquid balances'; results.append(r); continue
            fraction=min(qa/ra,qb/rb)
            take_a=(ra*fraction).quantize(Decimal(1).scaleb(-pa),rounding=ROUND_DOWN)
            take_b=(rb*fraction).quantize(Decimal(1).scaleb(-pb),rounding=ROUND_DOWN)
            if take_a>0 and take_b>0:
                r['direct_amounts']={a:format(take_a,'f'),b:format(take_b,'f')}
                r['mechanics_status']='nonzero paired amounts; contract deviation/share checks pending'
            else:
                r['mechanics_status']='conversion or additional liquid assets required'
            results.append(r)
    return results
