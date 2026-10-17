"""Read-only marketpools v13 simulation. No signing, network or execution.
Reference deployed codeHash fd6d750418df6b23de96fa0ba8cb006828dcbc272d566c6847204b552a41004c.
Existing populated pools only. Quotes require fresh contract/params/state validation
before execution; arithmetic uses BigNumber-style 20 decimal-place division.
"""
from decimal import Decimal as D, localcontext, ROUND_DOWN, ROUND_HALF_UP, InvalidOperation
from copy import deepcopy
from functools import wraps

class QuoteError(ValueError): pass

def guarded(fn):
    @wraps(fn)
    def run(*a,**kw):
        try:
            with localcontext() as c:
                c.prec=100
                return fn(*a,**kw)
        except (KeyError,TypeError,InvalidOperation,ZeroDivisionError) as e:
            raise QuoteError(f'missing/invalid quote input: {e}') from e
    return run

def num(x):
    if isinstance(x,bool): raise QuoteError('boolean amount')
    v=D(str(x))
    if not v.is_finite() or v<0: raise QuoteError('invalid amount')
    return v

def text(x):
    s=format(x,'f'); return s.rstrip('0').rstrip('.') if '.' in s else s

def div(a,b):
    if b<=0: raise QuoteError('zero reserve/denominator')
    return (a/b).quantize(D('1e-20'),rounding=ROUND_HALF_UP)

def quant(x,p,mode=ROUND_DOWN):
    if isinstance(p,bool) or not isinstance(p,int) or not 0<=p<=8: raise QuoteError('invalid precision')
    return x.quantize(D(1).scaleb(-p),rounding=mode)

def input_qty(x,p):
    v=num(x)
    if v<=0 or quant(v,p)!=v: raise QuoteError('zero or excess-precision input')
    return v

def state(pool):
    a,b=pool['tokenPair'].split(':'); ra,rb,s=(num(pool[k]) for k in ['baseQuantity','quoteQuantity','totalShares'])
    if min(ra,rb,s)<=0: raise QuoteError('empty/new pool requires separate oracle validation')
    return a,b,ra,rb,s

def updated(pool,da,db,ds=D(0),volume=False):
    r=deepcopy(pool); p=r['precision']
    r['baseQuantity']=text(quant(num(r['baseQuantity'])+da,p,ROUND_HALF_UP))
    r['quoteQuantity']=text(quant(num(r['quoteQuantity'])+db,p,ROUND_HALF_UP))
    r['totalShares']=text(num(r['totalShares'])+ds)
    if volume:
        # Deployed updatePoolStats(swap=true): volumes accumulate |delta|, ROUND_DOWN.
        r['baseVolume']=text(quant(num(r.get('baseVolume',0))+abs(da),p,ROUND_DOWN))
        r['quoteVolume']=text(quant(num(r.get('quoteVolume',0))+abs(db),p,ROUND_DOWN))
    if num(r['baseQuantity'])>0 and num(r['quoteQuantity'])>0:
        r['basePrice']=text(quant(div(num(r['quoteQuantity']),num(r['baseQuantity'])),p))
        r['quotePrice']=text(quant(div(num(r['baseQuantity']),num(r['quoteQuantity'])),p))
    return r

@guarded
def swap_exact_in(pool,symbol,quantity,precisions,params):
    a,b,ra,rb,_=state(pool)
    if symbol not in (a,b): raise QuoteError('token not in pool')
    out=b if symbol==a else a; ri,ro=(ra,rb) if symbol==a else (rb,ra)
    amount=input_qty(quantity,precisions[symbol]); fee=num(params['tradeFeeMul'])
    if not 0<fee<=1: raise QuoteError('invalid tradeFeeMul')
    raw=div(amount*fee*ro,ri+amount*fee)
    received=quant(raw,precisions[out])
    if received<=0 or received>=ro: raise QuoteError('zero output or insufficient liquidity')
    # Contract updates reserves using pre-output-floor delta, rounded to pool precision.
    # Deployed updatePoolStats(swap=true) also accumulates volume counters from deltas.
    after=updated(pool,amount if symbol==a else -raw,-raw if symbol==a else amount,volume=True)
    spot=div(amount*ro,ri)
    return {'pool':pool['tokenPair'],'symbol_in':symbol,'symbol_out':out,
            'amount_in':text(amount),'amount_out':text(received),'pool_after':after,
            'fee_output_stat':text(quant(div(raw,fee)-raw,precisions[out],ROUND_HALF_UP)),
            'output_shortfall_vs_spot':text(spot-received),
            'price_impact_fraction':text(div(spot-received,spot)),
            'basis':'conditional exact-input simulation; not signed or guaranteed'}

@guarded
def add_liquidity(pool,base_qty,quote_qty,precisions,balances,at_ms,position=None,max_price_impact='1'):
    a,b,ra,rb,shares=state(pool)
    qa=input_qty(base_qty,precisions[a]); qb=input_qty(quote_qty,precisions[b])
    impact=num(max_price_impact)
    if impact<=0 or impact.as_tuple().exponent < -3: raise QuoteError('invalid maxPriceImpact')
    tol=div(impact,D(100)); oa,ob=qa,qb
    optimal=quant(div(qa*rb,ra),precisions[b],ROUND_HALF_UP)
    if optimal<=qb:
        ob=optimal
        if ob<qb*(1-tol): raise QuoteError('deposit ratio exceeds max price impact')
    else:
        oa=quant(div(qb*ra,rb),precisions[a],ROUND_HALF_UP)
        if oa>qa or oa<qa*(1-tol): raise QuoteError('deposit ratio exceeds max price impact')
    if min(oa,ob)<=0: raise QuoteError('deposit rounds to zero')
    if num(balances.get(a,0))<oa or num(balances.get(b,0))<ob: raise QuoteError('insufficient liquid balance')
    added=min(div(oa*shares,ra),div(ob*shares,rb))
    if added<=0: raise QuoteError('zero minted shares')
    now=num(at_ms); tf=now
    if position:
        old=num(position['shares']); old_tf=num(position['timeFactor'])
        if old_tf>now: raise QuoteError('position newer than snapshot')
        tf=min((old_tf+(now-old_tf)*div(added,old+added)).quantize(D(1),rounding=ROUND_HALF_UP),now)
    return {'pool':pool['tokenPair'],'amounts':{a:text(oa),b:text(ob)},'shares_added':text(added),
            'timeFactor':int(tf),'pool_after':updated(pool,oa,ob,added)}

@guarded
def remove_liquidity(pool,position_shares,percentage,precisions):
    a,b,ra,rb,total=state(pool); own=num(position_shares); pct=num(percentage)
    if own<=0 or own>total or not 0<pct<=100 or pct.as_tuple().exponent < -3: raise QuoteError('invalid shares/percentage')
    removed=div(own*pct,D(100)); qa=quant(div(removed*ra,total),precisions[a]); qb=quant(div(removed*rb,total),precisions[b])
    if min(qa,qb)<=0: raise QuoteError('withdrawal output rounds to zero')
    return {'pool':pool['tokenPair'],'sharesOut':text(pct),'shares_removed':text(removed),
            'amounts':{a:text(qa),b:text(qb)},'pool_after':updated(pool,-qa,-qb,-removed)}

@guarded
def best_route(pools,source,target,quantity,precisions,params,max_hops=3):
    """Enumerate simple paths within explicit computational hop bound, not all routes."""
    if source==target: return {'legs':[],'amount_out':text(num(quantity)),'pools_after':deepcopy(pools)}
    adjacency={}
    for pair,p in sorted(pools.items()):
        a,b=p['tokenPair'].split(':')
        adjacency.setdefault(a,[]).append((pair,b)); adjacency.setdefault(b,[]).append((pair,a))
    best=None
    def visit(sym,qty,legs,seen,states):
        nonlocal best
        if sym==target:
            if best is None or num(qty)>num(best['amount_out']):
                best={'legs':legs,'amount_out':qty,'pools_after':states,'max_hops':max_hops}
            return
        if len(legs)>=max_hops: return
        for pair,nxt in adjacency.get(sym,[]):
            if nxt in seen: continue
            try: q=swap_exact_in(states[pair],sym,qty,precisions,params)
            except QuoteError: continue
            new=states.copy(); new[pair]=q['pool_after']
            visit(nxt,q['amount_out'],legs+[q],seen|{nxt},new)
    visit(source,quantity,[],{source},deepcopy(pools))
    if best is None: raise QuoteError('no nonzero executable path within hop bound')
    return best
