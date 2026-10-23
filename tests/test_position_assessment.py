"""Adapter/cron integration tests using saved public inputs, not new earnings."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, '/home/tydynrain/.hermes/scripts')
from lp_position_assessment import assess_positions
from lp_position_scenario import decide_topup_vs_keep
import cron_lp_agent as cron

SNAPSHOT = Path('/home/tydynrain/hive-agent-test/reports/position-inputs-20260917T182830Z.json')

class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SNAPSHOT.read_text())
        self.calls = []

    def rpc(self, method, params):
        self.calls.append((method, params))
        self.assertIn(method, ('find', 'getContract'))
        if method == 'getContract':
            return self.data['contracts'][params['name']]
        table = params['table']; query = params['query']
        if table == 'liquidityPositions':
            rows = self.data['ours'] if 'account' in query else self.data['participants'][query['tokenPair']]
        elif table == 'params':
            rows = [self.data[params['contract'] + '_params']]
        else:
            rows = self.data[table]
        return rows[params['offset']:params['offset'] + params['limit']]

    def test_adapter_matches_pure_replay(self):
        with patch('lp_position_assessment.datetime') as clock:
            clock.now.return_value.timestamp.return_value = self.data['captured_at_ms'] / 1000
            result = assess_positions(self.rpc, self.data['account'])
        self.assertEqual(result['comparisons'], decide_topup_vs_keep(self.data)['comparisons'])
        self.assertEqual(result['provenance']['network_requests'], len(self.calls))
        self.assertEqual(result['transactions_submitted'], 0)

    def test_replay_passes_full_graph_and_explicit_common_unit(self):
        with patch('lp_position_scenario.position_scenario', return_value={'decision': 'observe'}) as scenario:
            result = decide_topup_vs_keep(self.data)
        self.assertGreater(scenario.call_count, 0)
        for call in scenario.call_args_list:
            self.assertEqual(call.kwargs['routes'], {p['tokenPair']: p for p in self.data['pools']})
            self.assertEqual(call.kwargs['terminal_unit'], 'SWAP.HIVE')
            self.assertEqual(call.kwargs['max_hops'], 3)
        self.assertFalse(result['execution_allowed'])
        self.assertEqual(result['terminal_unit'], 'SWAP.HIVE')

    def test_unknown_contract_stops_before_table_reads(self):
        self.data['contracts']['distribution']['codeHash'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'unrecognized contract'):
            assess_positions(self.rpc, self.data['account'])
        self.assertTrue(all(m == 'getContract' for m, _ in self.calls))

    def test_pagination_keeps_all_participants(self):
        self.data['participants']['BBH:SWAP.HIVE'] = self.data['participants']['BBH:SWAP.HIVE'] * 1001
        with patch('lp_position_assessment.decide_topup_vs_keep', return_value={'comparisons': []}) as decide:
            assess_positions(self.rpc, self.data['account'])
        self.assertEqual(decide.call_args.args[0]['participants'], self.data['participants'])
        self.assertTrue(any(p.get('offset', 0) >= 1000 for _, p in self.calls))

    def test_cron_reports_without_feeding_conditional_evidence_to_selector(self):
        agent = SimpleNamespace(pe=SimpleNamespace(token_precisions={}),
                                portfolio=SimpleNamespace(account='hermes-sandbox', balances={}, positions={}))
        report = {'comparisons': [{'pool':'A:B', 'decision':'observe', 'conditional_advantage':'1'}], 'transactions_submitted':0}
        with patch('lp_pool_assessment.assess_pools', return_value=[]), \
             patch('lp_candidate_screen.screen', return_value={'selection':[]}) as screen, \
             patch.object(cron, 'he', return_value={'codeHash':'fixture'}) as rpc, \
             patch('lp_position_assessment.assess_positions', return_value=report) as assess:
            self.assertEqual(cron.manage_positions(agent, {}), [])
        assess.assert_called_once_with(rpc, 'hermes-sandbox')
        self.assertIs(agent.position_assessment, report)
        self.assertNotIn('economic_evidence', screen.call_args.kwargs)

    def test_cron_assessment_failure_stays_observe(self):
        agent = SimpleNamespace(pe=SimpleNamespace(token_precisions={}),
                                portfolio=SimpleNamespace(account='hermes-sandbox', balances={}, positions={}))
        with patch('lp_pool_assessment.assess_pools', return_value=[]), \
             patch('lp_candidate_screen.screen', return_value={'selection':[]}), \
             patch.object(cron, 'he', return_value={'codeHash':'fixture'}), \
             patch('lp_position_assessment.assess_positions', side_effect=ValueError('invalid RPC page')):
            self.assertEqual(cron.manage_positions(agent, {}), [])
        self.assertEqual(agent.position_assessment['decision'], 'observe')
        self.assertIn('invalid RPC page', agent.position_assessment['error'])
        self.assertEqual(agent.position_assessment['transactions_submitted'], 0)

if __name__ == '__main__':
    unittest.main()
