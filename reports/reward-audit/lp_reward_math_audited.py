"""Pure pool-strategy distribution math, ONE conditional tick, no I/O.
Reference: https://raw.githubusercontent.com/hive-engine/hivesmartcontracts/master/contracts/distribution.js
getEffectiveShares/getPoolRecipients/runDistribution inspected directly.
Not deployed-code parity proof. Caller MUST provide complete, same-pool participant
rows and verify pool existence/positive totalShares. at_ms is chain evaluation time.
No scheduled payment guarantee, multi-tick forecast, fee income or mining model.
Division models bignumber.js default 20 DECIMAL PLACES (not significant digits),
ROUND_HALF_UP; deployment configuration still needs independent verification.
"""
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, localcontext

UPSTREAM_SOURCE = {'file':'contracts/distribution.js',
    'url':'https://raw.githubusercontent.com/hive-engine/hivesmartcontracts/master/contracts/distribution.js',
    'limitation':'Public upstream source, not verified deployed parity; complete participant snapshot required'}

def number(value):
    if value is None or isinstance(value, bool): raise ValueError('missing/invalid number')
    n=Decimal(str(value))
    if not n.is_finite() or n < 0: raise ValueError('invalid nonnegative number')
    return n

def text(n):
    s=format(n,'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s

def divide(a,b):
    if b <= 0: raise ValueError('zero denominator')
    return (a/b).quantize(Decimal('1e-20'),rounding=ROUND_HALF_UP)

def estimate_batch(batch, positions, params, precisions, account, at_ms):
    """Return JSON-compatible status/entitlements for a hypothetical tick at at_ms.

    positions must be the COMPLETE list from marketpools.liquidityPositions for
    batch.tokenPair, not just account positions. Missing/invalid rows invalidate
    the denominator. timeFactor may be fractional after top-ups. Top-ups alter
    the weighted age of the entire position; do not reuse the previous age.
    """
    out={'status':'unknown','batch_id':batch.get('_id') if isinstance(batch,dict) else None,
         'account':account,'eligible':None,'entitlements':None,'reason':None,
         'source':UPSTREAM_SOURCE,'basis':'conditional single-tick estimate, not observed earnings'}
    try:
        with localcontext() as ctx:
            ctx.prec=100
            if not isinstance(batch,dict) or not isinstance(params,dict) or not isinstance(precisions,dict):
                raise ValueError('invalid containers')
            if not isinstance(account,str) or not account: raise ValueError('account required')
            now=number(at_ms)
            if now != now.to_integral_value(): raise ValueError('integral chain timestamp required')
            if batch['strategy'] != 'pool': raise ValueError('unsupported reward strategy')
            if not isinstance(batch['active'],bool): raise ValueError('invalid active flag')
            if not batch['active']:
                return dict(out,status='inactive',eligible=False,reason='batch inactive')
            ticks=number(batch['numTicksLeft'])
            if ticks != ticks.to_integral_value(): raise ValueError('invalid remaining ticks')
            if ticks == 0: return dict(out,status='exhausted',eligible=False,reason='no ticks left')
            tick_ms=number(params['distTickHours'])*3600000
            if tick_ms <= 0: raise ValueError('invalid tick interval')
            pair=batch['tokenPair']
            excluded=batch['excludeAccount']; curve=batch['bonusCurve']
            if not isinstance(excluded,list) or not all(isinstance(x,str) for x in excluded):
                raise ValueError('invalid exclusion list')
            if not isinstance(curve,dict): raise ValueError('invalid bonusCurve')
            if not isinstance(positions,list): raise ValueError('complete participant list required')
            total=Decimal(0); own=Decimal(0); own_mult=None; seen=set()
            for row in positions:
                if not isinstance(row,dict) or row['tokenPair'] != pair:
                    raise ValueError('invalid/mixed-pool participant snapshot')
                name=row['account']
                if not isinstance(name,str) or not name or name in seen: raise ValueError('invalid/duplicate participant')
                seen.add(name)
                if name in excluded: continue
                age=now-number(row['timeFactor'])
                shares=number(row['shares'])
                if age < 0: raise ValueError('participant newer than snapshot')
                if age < tick_ms: continue
                mult=Decimal(1)
                # Upstream only enables the curve for STRING numPeriods.
                if age > tick_ms and isinstance(curve.get('numPeriods'),str):
                    periods=number(curve['numPeriods']); pct=number(curve['periodBonusPct'])
                    elapsed=divide(age,tick_ms).to_integral_value(rounding=ROUND_DOWN)
                    mult += divide(pct,Decimal(100))*min(elapsed,periods)
                effective=shares*mult
                total += effective
                if name == account: own=effective; own_mult=mult
            budgets=batch['tokenBalances']
            if not isinstance(budgets,list): raise ValueError('invalid tokenBalances')
            payouts=[]; tokens=set()
            for token in budgets:
                symbol=token['symbol']; quantity=number(token['quantity'])
                if symbol in tokens: raise ValueError('duplicate budget token')
                tokens.add(symbol)
                if quantity == 0: continue
                precision=precisions[symbol]
                if isinstance(precision,bool) or not isinstance(precision,int) or not 0 <= precision <= 8:
                    raise ValueError('invalid token precision')
                if total <= 0: continue
                share=divide(own,total)
                amount=divide(quantity*share,ticks)
                rounded=amount.quantize(Decimal(1).scaleb(-precision),rounding=ROUND_DOWN)
                payouts.append({'symbol':symbol,'payout_share':text(rounded),
                    'unfloored_estimate':text(amount),'rounded_zero':rounded==0 and amount>0,
                    'share_total':text(total),'tick_budget':text(divide(quantity,ticks))})
            return dict(out,status='ok',eligible=own>0,entitlements=payouts,
                        effective_shares=text(own),share_total=text(total),
                        bonus_multiplier=text(own_mult) if own_mult is not None else None,
                        ticks_remaining=int(ticks),tick_interval_ms=text(tick_ms),
                        reason='eligible' if own>0 else 'excluded, immature, absent, or no eligible shares')
    except (ValueError,KeyError,TypeError,InvalidOperation,OverflowError) as exc:
        return dict(out,reason=f'incomplete or invalid data: {exc}')
