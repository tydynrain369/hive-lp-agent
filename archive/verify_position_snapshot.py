"""Independent Fraction audit of every tick in the saved live-input comparisons.
Does not import the scenario or reward-math implementations. No network calls.
"""
from fractions import Fraction as F
import json
from pathlib import Path

ROOT = Path('/home/tydynrain/hive-agent-test/reports')

def rounded(x, places):
    scale = 10**places
    v = x*scale
    return F((2*v.numerator + v.denominator)//(2*v.denominator), scale)

def divide(a,b): return rounded(a/b,20)
def floor_token(x,p): return F((x*10**p).numerator//(x*10**p).denominator,10**p)

def verify(inputs, results):
    data=json.loads(Path(inputs).read_text()); output=json.loads(Path(results).read_text())
    precisions={x['symbol']:x['precision'] for x in data['tokens']}
    tick=F(data['distribution_params']['distTickHours'])*3600000
    checks=0
    for scenario in output['comparisons']:
        if 'deposit' not in scenario: continue
        pair=scenario['pool']; prior=scenario['position_before']; deposit=scenario['deposit']
        now=F(data['captured_at_ms']); old=F(prior['shares']); added=F(deposit['shares_added'])
        tf=min(rounded(F(prior['timeFactor'])+(now-F(prior['timeFactor']))*divide(added,old+added),0),now)
        assert tf==scenario['position_after']['timeFactor']
        pool=next(x for x in data['pools'] if x['tokenPair']==pair)
        a,b=pair.split(':')
        minted=min(divide(F(deposit['amounts'][a])*F(pool['totalShares']),F(pool['baseQuantity'])),
                   divide(F(deposit['amounts'][b])*F(pool['totalShares']),F(pool['quoteQuantity'])))
        assert minted==added
        for branch in ('stay','move'):
            rows=[dict(x) for x in data['participants'][pair]]
            if branch=='move':
                own=next(x for x in rows if x['account']==data['account'])
                own.update(shares=str(old+added),timeFactor=int(tf))
            budgets={x['_id']:{t['symbol']:F(t['quantity']) for t in x['tokenBalances']}
                     for x in data['batches'] if x['tokenPair']==pair}
            ticks_left={x['_id']:int(x['numTicksLeft']) for x in data['batches'] if x['tokenPair']==pair}
            batches={x['_id']:x for x in data['batches'] if x['tokenPair']==pair}
            earned={}
            for rec in scenario[branch]['ticks']:
                batch=batches[rec['batch_id']]; ident=batch['_id']; effective={}
                curve=batch['bonusCurve']
                for row in rows:
                    age=F(rec['at_ms'])-F(row['timeFactor'])
                    if row['account'] in batch['excludeAccount'] or age<tick: continue
                    mult=F(1)
                    if age>tick and isinstance(curve.get('numPeriods'),str):
                        elapsed=divide(age,tick).__floor__()
                        mult+=divide(F(curve['periodBonusPct']),F(100))*min(elapsed,F(curve['numPeriods']))
                    effective[row['account']]=F(row['shares'])*mult
                total=sum(effective.values(),F(0)); own_eff=effective.get(data['account'],F(0))
                assert F(rec['our_result']['effective_shares'])==own_eff
                assert F(rec['our_result']['share_total'])==total
                expected_reserve={}; expected_own={}
                for sym,qty in budgets[ident].items():
                    if qty==0 or total==0: continue
                    payouts={acct:floor_token(divide(qty*divide(eff,total),F(ticks_left[ident])),precisions[sym])
                             for acct,eff in effective.items()}
                    expected_reserve[sym]=sum(payouts.values(),F(0))
                    expected_own[sym]=payouts.get(data['account'],F(0))
                    budgets[ident][sym]-=expected_reserve[sym]
                    earned[sym]=earned.get(sym,F(0))+expected_own[sym]
                assert {s:F(q) for s,q in rec['reserved'].items()}==expected_reserve
                assert {x['symbol']:F(x['payout_share']) for x in rec['our_result']['entitlements']}==expected_own
                ticks_left[ident]-=1; checks+=1
            assert {s:F(q) for s,q in scenario[branch]['our_rewards'].items()}==earned
    return {'independently_verified_tick_records':checks,'network_requests':0,
            'method':'Fraction with explicit 20-place HALF_UP and token-floor arithmetic',
            'scope':'share mint, weighted timestamp, effective shares, payouts, all-participant budget decay; not forecasts'}

if __name__=='__main__':
    result=verify(ROOT/'position-inputs-20260917T182830Z.json', ROOT/'position-results-20260917T182830Z.json')
    print(json.dumps(result,indent=2))
