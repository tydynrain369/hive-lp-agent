"""Wire contract mechanics to assessments and conservative selection; no I/O.
This screen does NOT fabricate multi-tick earnings/cost bounds. It creates
candidate records with explicit unknown evidence so the selector can report
exactly what blocks an allocation. Mechanics are local conditional simulation.
"""
from copy import deepcopy
from lp_mechanics import add_liquidity,remove_liquidity,QuoteError
from lp_decisions import choose_joint

MARKETPOOLS_HASH='fd6d750418df6b23de96fa0ba8cb006828dcbc272d566c6847204b552a41004c'

def screen(rows,raw_pools,precisions,balances,positions,at_ms,code_hash,*,economic_evidence=None):
    pools={p['tokenPair']:p for p in raw_pools}; wallet={s:str(b.balance) for s,b in balances.items()}
    candidates=[]; withdrawals=[]
    verified=code_hash==MARKETPOOLS_HASH
    for row in rows:
        pair=row['pool']; candidate={'id':pair,'mechanics_verified':False,'quotes_fresh':False,
            'coverage_complete':False,'move_earnings_lower':None,'stay_earnings_upper':None,
            'switching_cost_upper':None,'spend':{},'conflicts':[pair],
            'horizon_start_ms':at_ms,'horizon_end_ms':None,
            'missing_evidence':['common reward-schedule horizon','candidate reward entitlement',
                                'fee/risk bounds','switching-cost bound','pre-execution quote refresh']}
        if not verified:
            candidate['mechanics_reason']='deployed contract hash not recognized'
        elif row['direct_amounts'] is None:
            candidate['mechanics_reason']=row['mechanics_status']
        else:
            try:
                pool=pools[pair];a,b=pair.split(':');pos=positions.get(pair)
                prior={'shares':str(pos.shares),'timeFactor':pos.time_factor} if pos else None
                quote=add_liquidity(pool,row['direct_amounts'][a],row['direct_amounts'][b],
                                    precisions,wallet,at_ms,prior)
                candidate.update(mechanics_verified=True,quote=quote,spend=quote['amounts'],
                                 mechanics_reason='conditional deposit simulation passed')
                # Trusted caller boundary, NOT certification of future profitability.
                # Never copy arbitrary keys that could replace locally verified mechanics.
                evidence=(economic_evidence or {}).get(pair)
                if evidence is not None:
                    if not isinstance(evidence,dict):
                        raise ValueError('economic evidence must be a mapping')
                    if (evidence.get('snapshot_at_ms')!=at_ms or
                        evidence.get('contract_hash')!=code_hash or
                        evidence.get('position_before')!=prior or
                        evidence.get('deposit_quote')!=quote or
                        evidence.get('horizon_start_ms')!=at_ms or
                        evidence.get('terminal_unit')!=b):
                        raise ValueError('economic evidence identity mismatch')
                    conflicts=evidence.get('conflicts')
                    if (not isinstance(conflicts,list) or pair not in conflicts or
                        not all(isinstance(x,str) and x for x in conflicts)):
                        raise ValueError('economic evidence must identify every affected pool')
                    if evidence.get('missing_evidence')!=[]:
                        raise ValueError('economic evidence has unresolved gaps')
                    for key in ('quotes_fresh','coverage_complete','move_earnings_lower',
                                'stay_earnings_upper','switching_cost_upper','horizon_end_ms',
                                'terminal_unit','conflicts','missing_evidence'):
                        candidate[key]=deepcopy(evidence.get(key))
            except (QuoteError,KeyError,ValueError) as exc:
                candidate['mechanics_reason']=str(exc)
        candidates.append(candidate)
    for pair,pos in positions.items():
        result={'pool':pair,'decision':'review_only'}
        try:
            if not verified:raise QuoteError('deployed contract hash not recognized')
            result['quote']=remove_liquidity(pools[pair],str(pos.shares),'100',precisions)
        except (QuoteError,KeyError) as exc:result['reason']=str(exc)
        withdrawals.append(result)
    # Selector cap applies to eligible candidate enumeration, not unverified observations.
    selection=choose_joint(candidates,wallet)
    return {'candidates':candidates,'withdrawals':withdrawals,'selection':selection,
            'contract_hash':code_hash,'snapshot_at_ms':at_ms,
            'status':'screening_only; economic scenario construction incomplete'}
