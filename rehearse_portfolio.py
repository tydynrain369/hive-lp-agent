"""Rehearse joint portfolio scenarios on saved or live public inputs. Read-only."""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_DOWN, localcontext
from pathlib import Path
sys.path.insert(0, '/home/tydynrain/.hermes/scripts')
from lp_portfolio import compare_portfolios
from lp_position_scenario import EXPECTED

OUT = Path('/home/tydynrain/hive-agent-test/reports')


def validate(data):
    for name, expected in EXPECTED.items():
        if data['contracts'][name]['codeHash'] != expected:
            raise ValueError('unrecognized contract ' + name)


def paired_plan(data):
    """Explicit paired top-up sizes per position, sized from the shared wallet in order."""
    funds = {x['symbol']: D(x['balance']) for x in data['balances']}
    prec = {x['symbol']: x['precision'] for x in data['tokens']}
    pools = {x['tokenPair']: x for x in data['pools']}
    actions = []
    for pos in data['ours']:
        pair = pos['tokenPair']
        a, b = pair.split(':')
        pool = pools[pair]
        with localcontext() as ctx:
            ctx.prec = 100
            qa = min(funds.get(a, D(0)), funds.get(b, D(0)) * D(pool['baseQuantity'])
                     / D(pool['quoteQuantity']))
            qa = qa.quantize(D(1).scaleb(-prec[a]), rounding=ROUND_DOWN)
            qb = (qa * D(pool['quoteQuantity']) / D(pool['baseQuantity'])
                  ).quantize(D(1).scaleb(-prec[b]), rounding=ROUND_DOWN)
        if min(qa, qb) <= 0:
            continue
        actions.append({'pool': pair, 'amounts': {a: str(qa), b: str(qb)}})
        for s, q in ((a, qa), (b, qb)):
            funds[s] = funds.get(s, D(0)) - q
    return [{'id': 'paired-topups-in-snapshot-order', 'actions': actions}]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if args.replay:
        path = args.replay
        raw = path.read_bytes()
        data = json.loads(raw)
    else:
        from position_scenario_diagnostic import collect
        data = collect()
        path = OUT / ('position-inputs-' + stamp + '.json')
        raw = json.dumps(data, indent=2).encode()
        path.write_bytes(raw)
    validate(data)
    wallet = {x['symbol']: x['balance'] for x in data['balances']}
    prec = {x['symbol']: x['precision'] for x in data['tokens']}
    pools = {x['tokenPair']: x for x in data['pools']}
    now = data['captured_at_ms']
    result = compare_portfolios(pools, wallet, data['participants'],
        data['batches'], prec, data['marketpools_params'], data['distribution_params'],
        data['account'], now, now + 7 * 86400000, paired_plan(data),
        terminal_unit='SWAP.HIVE')
    result['provenance'] = {'input_path': str(path),
                            'input_sha256': hashlib.sha256(raw).hexdigest(),
                            'network_requests': 0 if args.replay else len(data['rpc_calls']),
                            'transactions_submitted': 0,
                            'inputs_status': 'saved public snapshot; sequential capture, not atomic'}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = OUT / ('portfolio-rehearsal-' + stamp + '.json')
    destination.write_text(json.dumps(result, indent=2))
    stay, comp = result['stay'], result['comparisons']
    print(json.dumps({'results': str(destination), 'network_requests':
          result['provenance']['network_requests'],
          'positions_in_scope': len(data['ours']),
          'stay_terminal': stay['terminal'], 'stay_unroutable': stay['liquidation']['unroutable'],
          'stay_terminal_assets': stay['terminal_assets'],
          'plans': [{'id': c['id'], 'advantage': c['conditional_advantage'],
                     'deposits': [d['pool'] for d in c.get('move', {}).get('deposits', [])],
                     'terminal': c.get('move', {}).get('terminal'),
                     'refusal': c.get('refusal')} for c in comp],
          'coverage_complete': result['coverage_complete'],
          'transactions_submitted': 0}, indent=2))
