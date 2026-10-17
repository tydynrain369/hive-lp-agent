"""Capture/replay public top-up inputs. No wallet, private keys, or broadcasts."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_DOWN, localcontext
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen
sys.path.insert(0, '/home/tydynrain/.hermes/scripts')
from lp_position_scenario import decide_topup_vs_keep
from lp_evidence import joint_allocation
from replay_lp_scenario import EXPECTED

OUT = Path('/home/tydynrain/hive-agent-test/reports')
RPC = 'https://api.hive-engine.com/rpc/contracts'


def collect():
    calls = []
    def rpc(method, params):
        if method not in ('find', 'getContract'):
            raise ValueError('read-only methods only')
        req = Request(RPC, data=json.dumps({'jsonrpc':'2.0', 'id':1, 'method':method,
                      'params':params}).encode(), headers={'Content-Type':'application/json'})
        with urlopen(req, timeout=45) as response:
            envelope = json.load(response)
        if 'error' in envelope: raise RuntimeError(envelope['error'])
        result = envelope['result']
        calls.append({'method':method, 'params':params, 'result':result})
        return result
    def rows(contract, table, query=None):
        result = []
        while True:
            page = rpc('find', {'contract':contract,'table':table,'query':query or {},
                       'limit':1000,'offset':len(result),
                       'indexes':[{'index':'_id','descending':False}]})
            if not isinstance(page, list): raise ValueError('invalid page')
            result.extend(page)
            if len(page) < 1000: return result
    refs = {name:rpc('getContract', {'name':name}) for name in EXPECTED}
    for name, expected in EXPECTED.items():
        if refs[name]['codeHash'] != expected: raise ValueError('unrecognized contract '+name)
    ours = rows('marketpools','liquidityPositions', {'account':'hermes-sandbox'})
    pools = rows('marketpools','pools')
    balances = rows('tokens','balances', {'account':'hermes-sandbox'})
    tokens = rows('tokens','tokens')
    batches = rows('distribution','batches', {'active':True,'strategy':'pool'})
    mp = rows('marketpools','params')[0]
    dp = rows('distribution','params')[0]
    participants = {p['tokenPair']:rows('marketpools','liquidityPositions',
                                      {'tokenPair':p['tokenPair']}) for p in ours}
    return {'captured_at_ms':int(datetime.now(timezone.utc).timestamp()*1000),
            'account':'hermes-sandbox', 'contracts':refs,'ours':ours,'pools':pools,
            'balances':balances,'tokens':tokens,'batches':batches,'marketpools_params':mp,
            'distribution_params':dp,'participants':participants, 'rpc_calls':calls,
            'snapshot_status':'sequential public RPC, NOT atomic block state'}


def replay(data):
    return decide_topup_vs_keep(data)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay',type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if args.replay:
        path = args.replay
        raw = path.read_bytes(); data = json.loads(raw)
    else:
        data = collect(); path = OUT / ('position-inputs-'+stamp+'.json')
        raw = json.dumps(data,indent=2).encode(); path.write_bytes(raw)
    result = replay(data)
    result['provenance'] = {'input_path':str(path), 'input_sha256':hashlib.sha256(raw).hexdigest(),
                            'network_requests':0 if args.replay else len(data['rpc_calls'])}
    wallet = {x['symbol']:x['balance'] for x in data['balances']}
    try:
        result['joint_allocation'] = joint_allocation(
            result, wallet, captured_at_ms=data['captured_at_ms'],
            now_ms=int(datetime.now(timezone.utc).timestamp()*1000),
            max_staleness_ms=0)
        result['joint_allocation']['staleness_bound'] = '0 ms; actual evaluation clock, no freshness exemption for replay'
    except ValueError as exc:
        result['joint_allocation'] = {'selection': {'selected': [], 'reserved': {}},
                                      'rejected': [], 'candidate_count': 0,
                                      'refusal': str(exc), 'conditional': True, 'execution_allowed': False,
                                      'transactions_submitted': 0}
    destination = OUT / ('position-results-'+stamp+'.json')
    destination.write_text(json.dumps(result,indent=2))
    print(json.dumps({'inputs':str(path),'results':str(destination),
          'network_requests':result['provenance']['network_requests'],
          'comparisons':[{'pool':r['pool'],'advantage':r.get('conditional_advantage'),
                         'reason':r.get('reason'),'decision':r['decision']} for r in result['comparisons']],
          'joint':result['joint_allocation'].get('selection',{}).get('selected',[]),
          'transactions_submitted':0},indent=2))
