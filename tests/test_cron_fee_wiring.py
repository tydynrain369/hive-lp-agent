"""Cron fee-snapshot wiring: record then diff; fail closed; never fabricate."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def _load_cron(path):
    spec = importlib.util.spec_from_file_location('cron_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError:
        # heavy sibling imports unavailable in isolated test env — fine; we only
        # need the fee-snapshot section, which is self-contained below.
        pass
    return mod


CRON_PATHS = [Path('/home/tydynrain/hive-agent-test/cron_lp_agent.py'),
              Path('/home/tydynrain/.hermes/scripts/cron_lp_agent.py')]

POOL_A = {'_id': 'x', 'tokenPair': 'A:B', 'precision': 8,
          'baseQuantity': '1000', 'quoteQuantity': '2000',
          'totalShares': '100', 'baseVolume': '5', 'quoteVolume': '9'}
POOL_A_GROWN = dict(POOL_A, baseQuantity='1004', quoteQuantity='2006')


def make_rpc(pools, positions, fail=False):
    def rpc(contract, table, query):
        if fail:
            raise RuntimeError('rpc down')
        if table == 'pools':
            return [p for p in pools if p['tokenPair'] == query['tokenPair']]
        if table == 'liquidityPositions':
            return [p for p in positions if p['_account'] == query['account']]
        return []
    return rpc


class CronFeeWiringTest(unittest.TestCase):
    def _run(self, mod, **kw):
        fn = getattr(mod, 'record_fee_snapshots', None)
        self.assertIsNotNone(fn, 'record_fee_snapshots missing from cron')
        return fn(**kw)

    def test_first_run_records_baseline_only(self):
        for path in CRON_PATHS:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as td:
                store = Path(td) / 'snap.jsonl'
                mod = _load_cron(path)
                rpc = make_rpc([POOL_A], [{'_account': 'acct', 'tokenPair': 'A:B', 'shares': '10'}])
                out = self._run(mod, rpc=rpc, account='acct',
                                positions={'A:B': object()}, store_path=store,
                                at_ms=1000, contract_hash=None)
                self.assertEqual(out['results']['A:B']['status'], 'snapshot_recorded')
                self.assertEqual(len(store.read_text().strip().splitlines()), 1)

    def test_second_run_measures_exact_growth(self):
        for path in CRON_PATHS:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as td:
                store = Path(td) / 'snap.jsonl'
                mod = _load_cron(path)
                positions = [{'_account': 'acct', 'tokenPair': 'A:B', 'shares': '10'}]
                self._run(mod, rpc=make_rpc([POOL_A], positions), account='acct',
                          positions={'A:B': object()}, store_path=store, at_ms=1000)
                out = self._run(mod, rpc=make_rpc([POOL_A_GROWN], positions), account='acct',
                                positions={'A:B': object()}, store_path=store, at_ms=2000)
                diff = out['results']['A:B']['diff']
                self.assertEqual(diff['status'], 'measured')
                # share = 10/100 = 1/10; growth = 4 A + 6 B -> 0.4 A, 0.6 B
                self.assertEqual(diff['attributable_fees'], {'A': '0.4', 'B': '0.6'})

    def test_rpc_failure_recorded_not_raised(self):
        for path in CRON_PATHS:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as td:
                mod = _load_cron(path)
                out = self._run(mod, rpc=make_rpc([], [], fail=True), account='acct',
                                positions={'A:B': object()}, store_path=Path(td) / 's.jsonl',
                                at_ms=1)
                self.assertEqual(out['results']['A:B']['status'], 'unavailable')

    def test_no_positions_no_snapshots(self):
        for path in CRON_PATHS:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as td:
                store = Path(td) / 's.jsonl'
                mod = _load_cron(path)
                out = self._run(mod, rpc=make_rpc([POOL_A], []), account='acct',
                                positions={}, store_path=store, at_ms=1)
                self.assertEqual(out['results'], {})
                self.assertFalse(store.exists())

    def test_unverified_contract_hash_skips(self):
        for path in CRON_PATHS:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as td:
                store = Path(td) / 's.jsonl'
                mod = _load_cron(path)
                out = self._run(mod, rpc=make_rpc([POOL_A], []), account='acct',
                                positions={'A:B': object()}, store_path=store, at_ms=1,
                                contract_hash='deadbeef' * 8)
                self.assertEqual(out['results']['A:B']['status'], 'contract_unverified')


if __name__ == '__main__':
    unittest.main()
