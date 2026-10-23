"""Synthetic economic evidence, real screen -> mechanics -> joint selector.
Does NOT establish live profitability or compute future earnings bounds.
"""
import copy
import sys
import unittest
from types import SimpleNamespace
sys.path.insert(0, '/home/tydynrain/.hermes/scripts')
from lp_candidate_screen import screen, MARKETPOOLS_HASH
from lp_mechanics import add_liquidity


class ExistingPositionPlannerTest(unittest.TestCase):
    def test_stale_position_returns_observe_with_reason(self):
        """Real screen -> real selector; stale age must reject positive bounds."""
        now = 10 * 86400000
        pool = {'tokenPair': 'A:B', 'baseQuantity': '100',
                'quoteQuantity': '100', 'totalShares': '100', 'precision': 8}
        prior = {'shares': '10', 'timeFactor': 0}
        quote = add_liquidity(pool, '1', '1', {'A': 8, 'B': 8},
                              {'A': '2', 'B': '2'}, now, prior)
        evidence = {'A:B': {
            'snapshot_at_ms': now, 'contract_hash': MARKETPOOLS_HASH,
            'deposit_quote': quote,
            'position_before': {'shares': '10', 'timeFactor': 1},
            'horizon_start_ms': now, 'horizon_end_ms': now + 86400000,
            'quotes_fresh': True, 'coverage_complete': True,
            'terminal_unit': 'B', 'move_earnings_lower': '8',
            'stay_earnings_upper': '5', 'switching_cost_upper': '1',
            'missing_evidence': [], 'conflicts': ['A:B']}}
        result = screen(
            [{'pool': 'A:B', 'direct_amounts': {'A': '1', 'B': '1'}}],
            [pool], {'A': 8, 'B': 8},
            {s: SimpleNamespace(balance='2') for s in ('A', 'B')},
            {'A:B': SimpleNamespace(shares='10', time_factor_ms=0)},
            now, MARKETPOOLS_HASH, economic_evidence=evidence)
        self.assertEqual(result['selection']['selected'], [])
        self.assertEqual(result['selection']['reserved'], {})
        self.assertEqual(result['selection']['assessments'], [{
            'id': 'A:B', 'decision': 'observe', 'net_advantage_lower': None,
            'reason': 'quotes not fresh'}])
        self.assertEqual(result['candidates'][0]['mechanics_reason'],
                         'economic evidence identity mismatch')

    def test_existing_position_topup_evidence_handoff(self):
        now = 10 * 86400000
        pool = {'tokenPair': 'A:B', 'baseQuantity': '100',
                'quoteQuantity': '100', 'totalShares': '100', 'precision': 8}
        rows = [{'pool': 'A:B', 'direct_amounts': {'A': '1', 'B': '1'}}]
        wallet = {s: SimpleNamespace(balance='2') for s in ('A', 'B')}
        positions = {'A:B': SimpleNamespace(shares='10', time_factor_ms=0)}
        quote = add_liquidity(pool, '1', '1', {'A': 8, 'B': 8},
                              {'A': '2', 'B': '2'}, now,
                              {'shares': '10', 'timeFactor': 0})
        # Explicit synthetic upstream evidence for the entire post-topup position,
        # compared with keeping the aged position AND the same idle wallet.
        evidence = {'A:B': {
            'snapshot_at_ms': now, 'contract_hash': MARKETPOOLS_HASH,
            'deposit_quote': quote,
            'position_before': {'shares': '10', 'timeFactor': 0},
            'horizon_start_ms': now, 'horizon_end_ms': now + 7 * 86400000,
            'quotes_fresh': True, 'coverage_complete': True,
            'terminal_unit': 'B',
            'move_earnings_lower': '8', 'stay_earnings_upper': '5',
            'switching_cost_upper': '1', 'missing_evidence': [],
            'conflicts': ['A:B']}}
        args = (rows, [pool], {'A': 8, 'B': 8}, wallet, positions, now, MARKETPOOLS_HASH)
        untouched = copy.deepcopy(evidence)
        result = screen(*args, economic_evidence=evidence)
        self.assertEqual(result['selection']['selected'], ['A:B'])
        self.assertEqual(result['selection']['net_advantage_lower'], '2')
        self.assertEqual(result['selection']['reserved'], {'A': '1', 'B': '1'})
        self.assertGreater(result['candidates'][0]['quote']['timeFactor'], 0)
        self.assertLess(result['candidates'][0]['quote']['timeFactor'], now)
        self.assertEqual(evidence, untouched)
        self.assertEqual(screen(*args)['selection']['selected'], [])
        # Missing cost, stale identity, wrong age, different amounts and
        # nonpositive advantage must never fall through to a selected top-up.
        for key, value in [('switching_cost_upper', None),
                           ('snapshot_at_ms', now-1),
                           ('contract_hash', 'unrecognized'),
                           ('position_before', {'shares': '10', 'timeFactor': 1}),
                           ('deposit_quote', {}),
                           ('stay_earnings_upper', '8')]:
            bad = copy.deepcopy(evidence)
            bad['A:B'][key] = value
            with self.subTest(field=key):
                self.assertEqual(screen(*args, economic_evidence=bad)['selection']['selected'], [])


if __name__ == '__main__':
    unittest.main()
