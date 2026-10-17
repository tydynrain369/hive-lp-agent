"""Synthetic fixture tests for the direct-deposit scenario builder.
These fixtures are NOT live earnings evidence; they only pin scenario mechanics.
"""
import unittest
from copy import deepcopy
from lp_scenario import direct_scenario

POOL = {'_id': 1, 'tokenPair': 'TST:QTD', 'baseQuantity': '1000',
        'quoteQuantity': '500', 'totalShares': '100', 'precision': 8, 'creator': 'x'}
PARTICIPANTS = [{'account': 'alice', 'tokenPair': 'TST:QTD',
                 'shares': '100', 'timeFactor': 1000000}]
BATCHES = [{'_id': 7, 'strategy': 'pool', 'numTicks': 3, 'numTicksLeft': 3,
            'active': True, 'lastTickTime': 2000000, 'tokenPair': 'TST:QTD',
            'excludeAccount': [], 'bonusCurve': {},
            'tokenBalances': [{'symbol': 'QTD', 'quantity': '30'}]}]
WALLET = {'TST': '10', 'QTD': '5'}
PREC = {'TST': 8, 'QTD': 8}
MP = {'tradeFeeMul': '0.9975'}
DP = {'distTickHours': '24'}


class ScenarioTest(unittest.TestCase):
    def setUp(self):
        self.now = 100000000

    def test_scenario_mechanics(self):
        result = direct_scenario(deepcopy(POOL), dict(WALLET), deepcopy(PARTICIPANTS),
                                 deepcopy(BATCHES), PREC, MP, DP, 'hermes-sandbox',
                                 self.now, self.now + 3 * 86400000)
        self.assertEqual(result['decision'], 'observe; conditional scenario only')
        self.assertFalse(result['coverage_complete'])
        self.assertGreater(result['deposit']['shares_added'], '0')
        # Inputs must never be mutated by the scenario.
        self.assertEqual(BATCHES[0]['tokenBalances'][0]['quantity'], '30')
        self.assertEqual(BATCHES[0]['numTicksLeft'], 3)
        # Hold-age delay: first nominal tick ineligible, a later tick pays.
        first, second = result['ticks'][0], result['ticks'][1]
        self.assertFalse(first['our_result']['eligible'])
        self.assertEqual(first['our_result']['entitlements'][0]['payout_share'], '0')
        self.assertTrue(second['our_result']['eligible'])
        self.assertGreater(float(second['our_result']['entitlements'][0]['payout_share']), 0)
        # Alice alone reserves the whole first tick; budgets decay tick by tick.
        self.assertEqual(first['reserved']['QTD'], '10')
        self.assertEqual(second['reserved']['QTD'], '9.99999999')
        earned = result['our_rewards']['QTD']
        self.assertGreater(float(earned), 0)
        advantage = float(result['conditional_advantage'])
        self.assertGreater(advantage, 0)
        self.assertAlmostEqual(advantage,
                               float(result['move_terminal']) - float(result['stay_terminal']),
                               places=12)
        # Reward math alone cannot equal the conditional advantage; the gap is
        # rounding and liquidation effects and must be reported, never hidden.
        self.assertLess(abs(advantage - float(earned)), 1e-6)
        self.assertEqual(result['terminal_unit'], 'QTD')

    def test_refuses_existing_position(self):
        with self.assertRaises(ValueError):
            direct_scenario(deepcopy(POOL), dict(WALLET), deepcopy(PARTICIPANTS),
                            deepcopy(BATCHES), PREC, MP, DP, 'alice',
                            self.now, self.now + 86400000)

    def test_refuses_incomplete_snapshot(self):
        with self.assertRaises(ValueError):
            direct_scenario(deepcopy(POOL), dict(WALLET), [], deepcopy(BATCHES),
                            PREC, MP, DP, 'hermes-sandbox', self.now, self.now + 86400000)

    def test_refuses_invalid_horizon(self):
        with self.assertRaises(ValueError):
            direct_scenario(deepcopy(POOL), dict(WALLET), deepcopy(PARTICIPANTS),
                            deepcopy(BATCHES), PREC, MP, DP, 'hermes-sandbox',
                            self.now, self.now)


if __name__ == '__main__':
    unittest.main()
