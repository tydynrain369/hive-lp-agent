import json, urllib.request

req = urllib.request.Request(
    'https://api.hive.blog',
    data=json.dumps({
        'jsonrpc': '2.0',
        'method': 'condenser_api.get_account_history',
        'params': ['hermes-sandbox', -1, 200],
        'id': 1
    }).encode(),
    headers={'Content-Type': 'application/json'}
)
with urllib.request.urlopen(req, timeout=30) as r:
    resp = json.load(r)

result = resp.get('result')
print('Recent marketpools transactions:')
for h in result:
    op = h[1]['op']
    if op[0] == 'custom_json':
        op1 = op[1]
        if isinstance(op1, list):
            print(f'  op1 is LIST: {op1}')
            continue
        id_val = op1.get('id')
        if id_val == 'ssc-mainnet-hive':
            json_str = op1['json']
            payload = json.loads(json_str)
            if isinstance(payload, list):
                print(f'  payload is LIST: {payload}')
                continue
            if payload.get('contractName') == 'marketpools':
                cp = payload['contractPayload']
                action = payload.get('contractAction')
                tp = cp.get('tokenPair', 'N/A')
                bq = cp.get('baseQuantity', 'N/A')
                qq = cp.get('quoteQuantity', 'N/A')
                print(f'  Block {h[1]["block"]}: {action} - {tp} base={bq} quote={qq}')