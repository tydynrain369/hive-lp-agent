"""Public-read adapter for conditional position assessment. No executor or keys."""
from datetime import datetime, timezone
from lp_position_scenario import EXPECTED, decide_topup_vs_keep


def assess_positions(rpc, account):
    calls = 0
    def read(method, params):
        nonlocal calls
        if method not in ('find', 'getContract'):
            raise ValueError('public reads only')
        calls += 1
        return rpc(method, params)

    def rows(contract, table, query=None):
        # 2026-11-15 (Fable round 7, C-10): the adapter's own pagination is
        # GONE. he() owns pagination when the caller passes no offset — and
        # keeps every page of one table on ONE node (restarting from offset 0
        # on rotation), so pages can never stitch across nodes with different
        # sync heads. The old adapter loop called he() once per page, and
        # each call rotated independently.
        return read('find', {'contract': contract, 'table': table,
                             'query': query or {}, 'limit': 1000})

    refs = {name: read('getContract', {'name': name}) for name in EXPECTED}
    for name, expected in EXPECTED.items():
        if refs[name]['codeHash'] != expected:
            raise ValueError('unrecognized contract ' + name)
    ours = rows('marketpools', 'liquidityPositions', {'account': account})
    data = {'account': account, 'contracts': refs, 'ours': ours,
            'pools': rows('marketpools', 'pools'),
            'balances': rows('tokens', 'balances', {'account': account}),
            'tokens': rows('tokens', 'tokens'),
            'batches': rows('distribution', 'batches', {'active': True, 'strategy': 'pool'}),
            'marketpools_params': rows('marketpools', 'params')[0],
            'distribution_params': rows('distribution', 'params')[0],
            'participants': {p['tokenPair']: rows('marketpools', 'liquidityPositions',
                             {'tokenPair': p['tokenPair']}) for p in ours},
            'snapshot_status': 'sequential public RPC, NOT atomic block state'}
    data['captured_at_ms'] = int(datetime.now(timezone.utc).timestamp() * 1000)
    result = decide_topup_vs_keep(data)
    result['provenance'] = {'network_requests': calls,
                            'snapshot_status': data['snapshot_status'],
                            'captured_at_ms': data['captured_at_ms']}
    return result
