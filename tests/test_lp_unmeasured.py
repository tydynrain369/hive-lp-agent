"""Unknown earnings must not graduate or penalize productivity tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

# Exercise both maintained copies explicitly, regardless of sys.path order.
PATHS = [Path('/home/tydynrain/hive-agent-test/lp_test_tracker.py'),
         Path('/home/tydynrain/.hermes/scripts/lp_test_tracker.py')]

class UnmeasuredTests(unittest.TestCase):
    def test_missing_fee_data_preserves_test_state(self):
        for path in PATHS:
            with self.subTest(path=str(path)), tempfile.TemporaryDirectory() as tmp:
                spec = importlib.util.spec_from_file_location('tracker_under_test', path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                tracker = mod.LPTestTracker(db_path=str(Path(tmp) / 'test.db'))
                self.addCleanup(tracker.conn.close)
                test_id = tracker.start_test('A:B', 1, baseline={})
                row = dict(tracker.conn.execute('SELECT * FROM lp_tests WHERE id=?', (test_id,)).fetchone())
                agent = SimpleNamespace(pe=SimpleNamespace(pools={'A:B': object()}),
                                        portfolio=SimpleNamespace(balances={}))
                earned, details = mod.measure_lp_test_earnings(agent, row)
                self.assertIsNone(earned)
                self.assertEqual(details['measurement_status'], 'unmeasured')
                result = tracker.record_result(test_id, earned, details)
                self.assertEqual(result['outcome'], 'unmeasured')
                after = dict(tracker.conn.execute('SELECT * FROM lp_tests WHERE id=?', (test_id,)).fetchone())
                self.assertEqual(after['outcome'], 'testing')
                self.assertEqual(after['fail_streak'], 0)
                self.assertIsNone(after['checked_at'])

    def test_legacy_positive_counters_are_not_attributed_income(self):
        for path in PATHS:
            with self.subTest(path=str(path)):
                spec = importlib.util.spec_from_file_location('tracker_under_test', path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                agent = SimpleNamespace(
                    pe=SimpleNamespace(pools={'A:B': SimpleNamespace(accumulated_fees=lambda: (100, 200))}),
                    portfolio=SimpleNamespace(balances={'R': SimpleNamespace(amount=999)}))
                earned, details = mod.measure_lp_test_earnings(agent, {
                    'pool': 'A:B', 'baseline_json': json.dumps({
                        'pool_fees_base': 0, 'pool_fees_quote': 0,
                        'reward_balances': {'R': 0}})})
                self.assertIsNone(earned)
                self.assertIsNone(details['fees_earned_base'])
                self.assertIsNone(details['rewards_earned'])

    def test_baseline_null_not_zero_in_both_cron_copies(self):
        # Extract this pure seam without importing legacy cron dependencies.
        import ast
        for path in PATHS:
            cron = path.with_name('cron_lp_agent.py')
            tree = ast.parse(cron.read_text())
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == 'snapshot_lp_baseline')
            namespace = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(cron), 'exec'), namespace)
            agent = SimpleNamespace(portfolio=SimpleNamespace(load_balances=lambda: None, balances={}))
            baseline = namespace['snapshot_lp_baseline'](agent, 'A:B')
            self.assertIsNone(baseline['pool_fees_base'])
            self.assertIsNone(baseline['pool_fees_quote'])
            self.assertEqual(baseline['measurement_status'], 'unmeasured')

    def test_none_cannot_count_as_failure(self):
        for path in PATHS:
            with self.subTest(path=str(path)), tempfile.TemporaryDirectory() as tmp:
                spec = importlib.util.spec_from_file_location('tracker_under_test', path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                tracker = mod.LPTestTracker(db_path=str(Path(tmp) / 'test.db'))
                self.addCleanup(tracker.conn.close)
                test_id = tracker.start_test('A:B', 1)
                result = tracker.record_result(test_id, None, {'measurement_status': 'unmeasured'})
                self.assertEqual(result['outcome'], 'unmeasured')
                self.assertEqual(tracker.pool_state('A:B')['status'], 'testing')

if __name__ == '__main__':
    unittest.main()
