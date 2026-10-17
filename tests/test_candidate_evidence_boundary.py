"""Synthetic screen-to-selector evidence boundary; not live earnings evidence."""
import copy
import sys
import unittest
from types import SimpleNamespace
sys.path.insert(0, '/home/tydynrain/.hermes/scripts')
from lp_candidate_screen import screen, MARKETPOOLS_HASH
from lp_decisions import choose_joint


class EvidenceBoundaryTests(unittest.TestCase):
    def fixture(self):
        return screen(
            [{'pool': 'A:B', 'direct_amounts': {'A': '1', 'B': '1'}}],
            [{'tokenPair': 'A:B', 'baseQuantity': '100', 'quoteQuantity': '100', 'totalShares': '100', 'precision': 8}],
            {'A': 8, 'B': 8},
            {'A': SimpleNamespace(balance='2'), 'B': SimpleNamespace(balance='2')},
            {}, 1000, MARKETPOOLS_HASH)

    def test_mechanics_pass_is_not_economic_evidence(self):
        result = self.fixture()
        self.assertTrue(result['candidates'][0]['mechanics_verified'])
        self.assertEqual(result['selection']['selected'], [])
        self.assertEqual(result['selection']['assessments'][0]['reason'], 'quotes not fresh')

    def test_complete_synthetic_positive_evidence_can_select(self):
        original = self.fixture()['candidates'][0]
        c = copy.deepcopy(original)
        # Deliberately synthetic bounds, never attached to a live candidate.
        c.update(terminal_unit='B', quotes_fresh=True, coverage_complete=True, horizon_end_ms=2000,
                 move_earnings_lower='0.00000004', stay_earnings_upper='0.00000001',
                 switching_cost_upper='0.00000001', missing_evidence=[])
        result = choose_joint([c], {'A': '2', 'B': '2'})
        self.assertEqual(result['selected'], ['A:B'])
        self.assertEqual(result['net_advantage_lower'], '2E-8')
        self.assertFalse(original['quotes_fresh'])
        c['switching_cost_upper'] = None
        self.assertEqual(choose_joint([c], {'A': '2', 'B': '2'})['selected'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
