"""Independent Fraction check of saved BBH full-position branches; no network.
Checks a specified deposit and selected direct sale, not sizing/route optimality.
"""
import hashlib
import json
from fractions import Fraction as F
from pathlib import Path

ROOT = Path('/home/tydynrain/hive-agent-test/reports')
source = ROOT / 'position-inputs-20260917T201637Z.json'
raw = source.read_bytes()
data = json.loads(raw)
result = json.loads((ROOT / 'position-results-20260917T201637Z.json').read_text())
assert hashlib.sha256(raw).hexdigest() == result['provenance']['input_sha256']
r = next(x for x in result['comparisons'] if x['pool'] == 'BBH:SWAP.HIVE')
p = next(x for x in data['pools'] if x['tokenPair'] == r['pool'])
pos = next(x for x in data['ours'] if x['tokenPair'] == r['pool'])
prec = {x['symbol']: x['precision'] for x in data['tokens']}
wallet = {x['symbol']: F(x['balance']) for x in data['balances']}
a, b = r['pool'].split(':')

def rnd(x, places, half=False):
    n, rem = divmod(x.numerator * 10**places, x.denominator)
    return F(n + int(half and rem * 2 >= x.denominator), 10**places)

def div(x, y):
    return rnd(x / y, 20, True)

def display(x):
    return f'{float(x):.8f}'  # display only, assertions use exact Fractions

ra, rb, total = (F(p[k]) for k in ('baseQuantity', 'quoteQuantity', 'totalShares'))
qa, qb = (F(r['deposit']['amounts'][s]) for s in (a, b))
added = min(div(qa * total, ra), div(qb * total, rb))
assert added == F(r['deposit']['shares_added'])
terminals = {}
for name, topup in [('stay', False), ('move', True)]:
    branch = r[name]
    assert branch['our_rewards'] == {}, 'This fixture validates no-reward branches only'
    ba, bb = ra + (qa if topup else 0), rb + (qb if topup else 0)
    shares = F(pos['shares']) + (added if topup else 0)
    supply = total + (added if topup else 0)
    wa = rnd(div(shares * ba, supply), prec[a])
    wb = rnd(div(shares * bb, supply), prec[b])
    assert wa == F(branch['withdrawal']['amounts'][a])
    assert wb == F(branch['withdrawal']['amounts'][b])
    ri, ro = rnd(ba-wa, p['precision'], True), rnd(bb-wb, p['precision'], True)
    amounts = {a: wallet[a] - (qa if topup else 0) + wa,
               b: wallet[b] - (qb if topup else 0) + wb}
    assert amounts == {s: F(q) for s, q in branch['terminal_assets'].items()}
    fee = F(data['marketpools_params']['tradeFeeMul'])
    raw_out = div(amounts[a] * fee * ro, ri + amounts[a] * fee)
    out = rnd(raw_out, prec[b])
    legs = branch['liquidation']['sales'][a]['legs']
    assert len(legs) == 1 and legs[0]['pool'] == r['pool']
    assert out == F(legs[0]['amount_out'])
    assert rnd(ri+amounts[a], p['precision'], True) == F(legs[0]['pool_after']['baseQuantity'])
    assert rnd(ro-raw_out, p['precision'], True) == F(legs[0]['pool_after']['quoteQuantity'])
    terminals[name] = amounts[b] + out
    assert terminals[name] == F(branch['terminal'])
assert terminals['move'] - terminals['stay'] == F(r['conditional_advantage'])
print(json.dumps({'verified': True, 'pool': r['pool'], 'input_sha256': hashlib.sha256(raw).hexdigest(),
                  'terminal_unit': b, 'terminals': {k: display(v) for k,v in terminals.items()},
                  'conditional_advantage': display(terminals['move']-terminals['stay']),
                  'scope': 'specified deposit, withdrawal, wallet aggregation, direct swap and updated reserves; not optimality or profits'}, indent=2))
