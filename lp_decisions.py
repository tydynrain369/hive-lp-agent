"""Conservative evidence-driven joint selection. Pure, no execution.
Candidates carry shared-horizon lower/upper earnings bounds and switching cost
upper bounds from caller's verified scenario model. Missing bounds block moves.
No forecasts or dollar thresholds invented here. Exhaustive subset search is
bounded explicitly; over-limit inputs fail instead of claiming global optimality.
"""
from decimal import Decimal
from itertools import combinations


def d(value):
    if value is None or isinstance(value,bool): raise ValueError('unknown numeric evidence')
    value=Decimal(str(value))
    if not value.is_finite() or value<0: raise ValueError('invalid nonnegative evidence')
    return value


def compare(candidate):
    result={'id':candidate['id'],'decision':'observe','net_advantage_lower':None,'reason':None}
    try:
        if candidate.get('mechanics_verified') is not True: raise ValueError('mechanics not verified')
        if candidate.get('quotes_fresh') is not True: raise ValueError('quotes not fresh')
        if candidate.get('coverage_complete') is not True: raise ValueError('reward/fee/risk coverage incomplete')
        if not isinstance(candidate.get('terminal_unit'),str) or not candidate['terminal_unit'].strip():
            raise ValueError('terminal unit missing')
        start,end=candidate['horizon_start_ms'],candidate['horizon_end_ms']
        if end<=start: raise ValueError('invalid shared horizon')
        gain=d(candidate['move_earnings_lower'])-d(candidate['stay_earnings_upper'])-d(candidate['switching_cost_upper'])
        result['net_advantage_lower']=str(gain)
        result['decision']='candidate' if gain>0 else 'hold'
        result['reason']='positive conservative advantage' if gain>0 else 'no demonstrated after-cost improvement'
    except (KeyError,ValueError,TypeError) as exc: result['reason']=str(exc)
    return result


def choose_joint(candidates,balances,max_candidates=18):
    """Exact subset selection ONLY for independent, fixed-cost candidates.

    Conflicts contain every affected pool/position; shared routes must conflict.
    This deliberately refuses combining shared-pool quotes: reserves, entitlements
    and fees would require resimulation. Spend is initial-wallet token quantities;
    withdrawals/proceeds must be included inside a complete candidate scenario,
    never credited globally before that scenario is selected.
    """
    assessments=[compare(c) for c in candidates]
    valid=[c for c,r in zip(candidates,assessments) if r['decision']=='candidate']
    if len(valid)>max_candidates: raise ValueError('enumeration bound exceeded; no global optimum claimed')
    ids=[c['id'] for c in candidates]
    if len(ids)!=len(set(ids)): raise ValueError('duplicate candidate id')
    if len({(c['horizon_start_ms'],c['horizon_end_ms']) for c in valid})>1: raise ValueError('incomparable horizons')
    if len({c['terminal_unit'] for c in valid})>1: raise ValueError('incomparable terminal units')
    for c in valid:
        conflicts=c.get('conflicts')
        if (not isinstance(conflicts,list) or not conflicts or
                not all(isinstance(x,str) and x.strip() for x in conflicts) or
                not isinstance(c.get('spend'),dict)):
            raise ValueError('explicit spend/conflicts required: conflicts must be nonempty string list')
    best=[]; best_gain=Decimal(0); best_spend={}
    for size in range(1,len(valid)+1):
        for subset in combinations(valid,size):
            used=set(); spend={}; gain=Decimal(0); feasible=True
            for c in subset:
                conflicts=set(c['conflicts'])
                if used & conflicts: feasible=False; break
                used |= conflicts
                for symbol,qty in c['spend'].items(): spend[symbol]=spend.get(symbol,Decimal(0))+d(qty)
                gain+=Decimal(compare(c)['net_advantage_lower'])
            if not feasible or any(q>d(balances.get(s,0)) for s,q in spend.items()): continue
            if gain>best_gain:
                best=list(subset); best_gain=gain; best_spend=spend
    return {'selected':[c['id'] for c in best],'net_advantage_lower':str(best_gain),
            'reserved':{s:str(q) for s,q in best_spend.items()},'assessments':assessments,
            'scope':'best subset of supplied independent verified scenarios; not all possible allocations'}
