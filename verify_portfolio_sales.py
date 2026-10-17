"""Independent Fraction check of successful sale legs from a saved rehearsal."""
import hashlib
import json
import sys
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path


def rounded(x, places, half=False):
    n, d = x.numerator * 10**places, x.denominator
    q, rem = divmod(n, d)
    return F(q + int(half and 2*rem >= d), 10**places)


def verify(path):
    result = json.loads(path.read_text())
    raw = Path(result['provenance']['input_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result['provenance']['input_sha256']
    data = json.loads(raw)
    prec = {x['symbol']: x['precision'] for x in data['tokens']}
    fee = F(data['marketpools_params']['tradeFeeMul'])
    count = 0
    branches = [result['stay']] + [c['move'] for c in result['comparisons'] if 'move' in c]
    for branch in branches:
        graph = {p['tokenPair']: deepcopy(p) for p in data['pools']}
        for w in branch['withdrawals']:
            graph[w['pool']] = w['pool_after']
        total = F(0)
        for symbol in branch['liquidation']['sale_order']:
            sale = branch['liquidation']['sales'].get(symbol)
            if sale is None:
                continue
            for leg in sale['legs']:
                p = graph[leg['pool']]
                forward = leg['symbol_in'] == p['tokenPair'].split(':')[0]
                ri, ro = F(p['baseQuantity']), F(p['quoteQuantity'])
                if not forward:
                    ri, ro = ro, ri
                amount = F(leg['amount_in'])
                raw_out = rounded(amount*fee*ro/(ri+amount*fee), 20, True)
                assert F(leg['amount_out']) == rounded(raw_out, prec[leg['symbol_out']])
                ri = rounded(ri+amount, p['precision'], True)
                ro = rounded(ro-raw_out, p['precision'], True)
                after = leg['pool_after']
                assert F(after['baseQuantity']) == (ri if forward else ro)
                assert F(after['quoteQuantity']) == (ro if forward else ri)
                graph[leg['pool']] = after
                count += 1
            total += F(sale['amount_out'])
        assert total == F(branch['liquidation']['known_subtotal'])
    print(json.dumps({'sale_legs_verified': count, 'input_hash_verified': True,
        'scope': 'successful sale arithmetic only; not routing optimality or complete valuation'}))


if __name__ == '__main__':
    verify(Path(sys.argv[1]))
