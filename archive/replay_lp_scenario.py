"""Replay a public-data snapshot without network access or signing capabilities."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, '/home/tydynrain/.hermes/scripts')
from lp_scenario import direct_scenario

EXPECTED = {
    'marketpools': 'fd6d750418df6b23de96fa0ba8cb006828dcbc272d566c6847204b552a41004c',
    'distribution': '6bd095db17d7c2b76ce534b8235b9fcf05c4f9c033bc2d1d87ede518c5dd4178',
}


def replay(path, days=7, account='hermes-sandbox'):
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    for contract, expected in EXPECTED.items():
        if data.get(contract + '_reference_hash') != expected:
            raise ValueError('unrecognized contract reference: ' + contract)
    if days <= 0:
        raise ValueError('days must be positive')
    result = direct_scenario(
        data['pool'], {x['symbol']: x['balance'] for x in data['balances']},
        data['participants'], data['batches'],
        {x['symbol']: x['precision'] for x in data['tokens']},
        data['marketpools_params'], data['distribution_params'], account,
        data['captured_at_ms'], data['captured_at_ms'] + days * 86400000)
    result['provenance'] = {
        'input_path': str(Path(path).resolve()),
        'input_sha256': hashlib.sha256(raw).hexdigest(),
        'reference_hashes': EXPECTED,
        'reference_status': 'snapshot-provided reference hashes; not a fresh on-chain verification',
        'days': days, 'account': account, 'network_requests': 0,
        'transactions_submitted': 0,
    }
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--account', default='hermes-sandbox')
    args = parser.parse_args()
    print(json.dumps(replay(args.snapshot, args.days, args.account), indent=2))
