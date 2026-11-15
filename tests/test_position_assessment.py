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
        # 2026-11-15 (round 7, C-10): the adapter no longer sends an offset —
        # he() OWNS pagination and returns the whole table. The fake stands
        # in for he(), so no offset in the params means the full table; an
        # explicit offset (he()'s own internal paging, exercised by
        # TestAssessThroughRealHe against the real he()) still slices.
        if 'offset' not in params:
            return rows
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
        # 2026-11-15 (round 7, C-10): the ADAPTER no longer sends offsets —
        # he() owns pagination and keeps one table on one node. The WIRE-level
        # pin (offsets advance 0/1000/2000, no offset=0 loop) lives in
        # TestAssessThroughRealHe, which drives the REAL he() over a 2500-row
        # fake transport.

    def test_cron_wires_joint_allocation_evidence_into_the_selector(self):
        """2026-11-03: the evidence pipeline was dormant. assess_positions
        already produces {'comparisons': [...]} in exactly the shape
        lp_evidence.joint_allocation consumes; the cron was calling screen()
        with economic_evidence=None, so every candidate carried
        missing_evidence=[...] and the selector had nothing to select on.
        This asserts the handoff is live: a qualifying comparison produces
        economic_evidence and screen() receives it.
        """
        agent = SimpleNamespace(pe=SimpleNamespace(token_precisions={}),
                                portfolio=SimpleNamespace(account='hermes-sandbox',
                                    balances={'A': SimpleNamespace(balance='100'),
                                              'B': SimpleNamespace(balance='100')},
                                    positions={}))
        # A comparison that passes lp_evidence.promote's gates:
        # liquidation_complete + coverage_complete + explicit economic_evidence
        # dict with all required flags + valid horizon + non-degenerate deposit.
        _cmp = {
            'pool': 'A:B', 'decision': 'observe',
            'liquidation_complete': True, 'coverage_complete': True,
            'terminal_unit': 'B', 'horizon_start_ms': 1000, 'horizon_end_ms': 2000,
            'deposit': {'shares_added': '100', 'amounts': {'A': '10', 'B': '10'}},
            'affected_pools': ['A:B'],
            'move': {'terminal': '100'}, 'stay': {'terminal': '100'},
            'economic_evidence': {
                'mechanics_verified': True, 'quotes_fresh': True,
                'coverage_complete': True, 'pool': 'A:B', 'terminal_unit': 'B',
                'horizon_start_ms': 1000, 'horizon_end_ms': 2000,
                'move_earnings_lower': '100', 'stay_earnings_upper': '50',
                'switching_cost_upper': '0', 'deposit': {'shares_added': '100',
                                                          'amounts': {'A': '10', 'B': '10'}},
                'affected_pools': ['A:B'], 'captured_at_ms': 1000,
            },
        }
        report = {'comparisons': [_cmp], 'transactions_submitted': 0,
                  'captured_at_ms': 1000}
        with patch('lp_pool_assessment.assess_pools', return_value=[]), \
             patch('lp_candidate_screen.screen', return_value={'selection':[]}) as screen, \
             patch.object(cron, 'he', return_value={'codeHash':'fixture'}) as rpc, \
             patch('lp_position_assessment.assess_positions', return_value=report) as assess, \
             patch('time.time', return_value=1000.0):
            self.assertEqual(cron.manage_positions(agent, {}), [])
        assess.assert_called_once_with(rpc, 'hermes-sandbox')
        self.assertIs(agent.position_assessment, report)
        self.assertIn('economic_evidence', screen.call_args.kwargs)
        self.assertIn('A:B', screen.call_args.kwargs['economic_evidence'])

    def test_cron_degrades_cleanly_when_evidence_pipeline_unavailable(self):
        """The evidence pipeline is non-fatal: if joint_allocation raises,
        screen() is still called WITHOUT economic_evidence, and the tick
        still returns a (possibly empty) selection. A failure here must
        never kill the whole main (2026-10-22: the first live run crashed
        the entire tick through exactly this path).
        """
        agent = SimpleNamespace(pe=SimpleNamespace(token_precisions={}),
                                portfolio=SimpleNamespace(account='hermes-sandbox',
                                    balances={'A': SimpleNamespace(balance='100'),
                                              'B': SimpleNamespace(balance='100')},
                                    positions={}))
        report = {'comparisons': [], 'transactions_submitted': 0}
        with patch('lp_pool_assessment.assess_pools', return_value=[]), \
             patch('lp_candidate_screen.screen', return_value={'selection':[]}) as screen, \
             patch.object(cron, 'he', return_value={'codeHash':'fixture'}) as rpc, \
             patch('lp_position_assessment.assess_positions', return_value=report), \
             patch('lp_evidence.joint_allocation', side_effect=TypeError('boom')):
            self.assertEqual(cron.manage_positions(agent, {}), [])
        self.assertNotIn('economic_evidence', screen.call_args.kwargs)

    def test_cron_assessment_failure_stays_observe(self):
        agent = SimpleNamespace(pe=SimpleNamespace(token_precisions={}),
                                portfolio=SimpleNamespace(account='hermes-sandbox',
                                    balances={'A': SimpleNamespace(balance='100'),
                                              'B': SimpleNamespace(balance='100')},
                                    positions={}))
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
