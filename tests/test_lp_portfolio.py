"""Minimal shared-wallet regression; synthetic, not earnings evidence."""
import unittest
from copy import deepcopy
from fractions import Fraction as F
from lp_portfolio import portfolio_scenario
from test_lp_liquidation import pool, reference


class PortfolioTest(unittest.TestCase):
    def test_ordered_deposits_aggregate_before_shared_route_sales(self):
        pools = {pair: pool(pair, '100', '100') for pair in ('A:B', 'A:C')}
        participants = {pair: [dict(account=a, tokenPair=pair, shares=s, timeFactor=0)
                              for a, s in [('ours', '10'), ('other', '90')]]
                        for pair in pools}
        wallet = {'A': '12', 'B': '5', 'C': '7'}
        actions = [{'pool': 'A:B', 'amounts': {'A': '5', 'B': '5'}},
                   {'pool': 'A:C', 'amounts': {'A': '7', 'C': '7'}}]
        original = deepcopy((pools, participants, wallet, actions))
        result = portfolio_scenario(
            pools, wallet, participants, [], {'A': 8, 'B': 8, 'C': 8},
            {'tradeFeeMul': '0.9975'}, {'distTickHours': '24'},
            'ours', 86400000, 172800000, actions, terminal_unit='B')
        self.assertEqual([x['pool'] for x in result['deposits']], ['A:B', 'A:C'])
        self.assertEqual({s: F(q) for s, q in result['wallet_after_deposits'].items()},
                         {'A': F(0), 'B': F(0), 'C': F(0)})
        # Withdraw 15 A + 15 B and 17 A + 17 C; aggregate A BEFORE selling.
        self.assertEqual({s: F(q) for s, q in result['terminal_assets'].items()},
                         {'A': F(32), 'B': F(15), 'C': F(17)})
        sales = result['liquidation']['sales']
        self.assertEqual(F(sales['A']['legs'][0]['amount_in']), F(32))
        first, ri, ro = reference(pool('A:B', '90', '90'), 'A', '32', 8)
        via, _, _ = reference(pool('A:C', '90', '90'), 'C', '17', 8)
        second, _, _ = reference(pool('A:B', ri, ro), 'A', via, 8)
        self.assertEqual(F(result['terminal']), F(15) + first + second)
        self.assertFalse(result['coverage_complete'])
        self.assertFalse(result['execution_allowed'])
        self.assertEqual((pools, participants, wallet, actions), original)


class PortfolioBoundaries(unittest.TestCase):
    def setUp(self):
        import test_lp_position_scenario as fixture
        f = fixture.PositionScenarioTest(); f.setUp()
        self.pools = {'A:B': f.pool}
        self.wallet = {'A': '10', 'B': '10'}
        self.rows = {'A:B': f.rows}
        self.batches = f.batches
        self.prec = {'A': 8, 'B': 8, 'C': 8, 'X': 8}
        self.plan = [{'pool': 'A:B', 'amounts': {'A': '10', 'B': '10'}}]
        self.now = 10 * fixture.DAY

    def args(self):
        return (self.pools, self.wallet, self.rows, self.batches, self.prec,
                {'tradeFeeMul': '0.9975'}, {'distTickHours': '24'},
                'ours', self.now, self.now + 86400000)

    def run_plan(self, plan=None):
        return portfolio_scenario(*self.args(), self.plan if plan is None else plan,
                                  terminal_unit='B')

    def test_single_position_equivalence_both_branches(self):
        from lp_position_scenario import position_scenario
        old = position_scenario(self.pools['A:B'], self.wallet, self.rows['A:B'],
            self.batches, self.prec, {'tradeFeeMul': '0.9975'}, {'distTickHours': '24'},
            'ours', self.now, self.now + 86400000, self.plan[0]['amounts'])
        for name, plan in [('stay', []), ('move', self.plan)]:
            with self.subTest(name=name):
                result = self.run_plan(plan)
                for key in ('terminal', 'terminal_assets', 'our_rewards', 'liquidation'):
                    self.assertEqual(result[key], old[name][key])
                self.assertEqual(result['ticks']['A:B'], old[name]['ticks'])

    def test_shared_wallet_overspend_rejected_without_mutation(self):
        self.pools['A:C'] = pool('A:C', '100', '100')
        self.rows['A:C'] = [dict(r, tokenPair='A:C') for r in self.rows['A:B']]
        self.wallet['C'] = '10'
        before = deepcopy(self.args())
        with self.assertRaisesRegex(ValueError, 'insufficient liquid balance'):
            self.run_plan(self.plan + [{'pool': 'A:C', 'amounts': {'A': '1', 'C': '1'}}])
        self.assertEqual(self.args(), before)

    def test_repeated_deposits_commit_shares_and_wallet(self):
        self.batches = []
        actions = [{'pool': 'A:B', 'amounts': {'A': '5', 'B': '5'}}] * 2
        r = self.run_plan(actions)
        self.assertEqual(F(r['deposits'][1]['pool_after']['totalShares']), F(110))
        self.assertEqual(F(r['positions_after_deposits']['A:B'][0]['shares']), F(20))
        self.assertEqual(F(r['wallet_after_deposits']['A']), 0)
        self.assertEqual(F(r['terminal_assets']['A']), 20)

    def test_new_position_requires_complete_participants(self):
        self.rows['A:B'][0]['account'] = 'another'
        r = self.run_plan()
        own = next(x for x in r['positions_after_deposits']['A:B'] if x['account'] == 'ours')
        self.assertEqual(F(own['shares']), F(10))
        self.assertEqual(own['timeFactor'], self.now)
        self.rows = {}
        with self.assertRaisesRegex(ValueError, 'complete participants'):
            self.run_plan()

    def test_participant_validation(self):
        for mode in ('duplicate', 'sum', 'mixed', 'future'):
            with self.subTest(mode=mode):
                rows = deepcopy(self.rows)
                if mode == 'duplicate': self.rows['A:B'].append(deepcopy(self.rows['A:B'][0]))
                if mode == 'sum': self.rows['A:B'][0]['shares'] = '9'
                if mode == 'mixed': self.rows['A:B'][0]['tokenPair'] = 'A:C'
                if mode == 'future': self.rows['A:B'][0]['timeFactor'] = self.now + 1
                with self.assertRaises(ValueError): self.run_plan()
                self.rows = rows

    def test_all_scoped_rewards_and_idle_assets_aggregate(self):
        self.pools['A:C'] = pool('A:C', '100', '100')
        self.rows['A:C'] = [dict(r, tokenPair='A:C') for r in self.rows['A:B']]
        self.batches.append(dict(deepcopy(self.batches[0]), _id=2, tokenPair='A:C'))
        r = self.run_plan([])
        self.assertEqual(F(r['our_rewards']['B']), 20)
        self.assertEqual(F(r['terminal_assets']['B']), 40)  # idle + principal + BOTH batches
        self.assertEqual(F(r['terminal_assets']['A']), 30)
        self.assertEqual(set(r['ticks']), {'A:B', 'A:C'})

    def test_unknown_wallet_or_rewards_suppress_advantage(self):
        from lp_portfolio import compare_portfolios
        self.wallet['X'] = '1'
        r = compare_portfolios(*self.args(), [{'id': 'topup', 'actions': self.plan}], terminal_unit='B')
        self.assertIsNone(r['stay']['terminal'])
        self.assertEqual(r['stay']['liquidation']['unroutable'], ['X'])
        self.assertIsNone(r['comparisons'][0]['conditional_advantage'])
        self.assertFalse(r['coverage_complete'])
        self.assertFalse(r['execution_allowed'])

    def test_explicit_sizing_and_invalid_plan_isolation(self):
        from lp_portfolio import compare_portfolios
        before = deepcopy(self.args())
        r = compare_portfolios(*self.args(), [
            {'id': 'half', 'actions': [{'pool': 'A:B', 'amounts': {'A': '5', 'B': '5'}}]},
            {'id': 'full', 'actions': self.plan},
            {'id': 'excess', 'actions': self.plan * 2}], terminal_unit='B')
        self.assertGreater(F(r['comparisons'][1]['conditional_advantage']),
                           F(r['comparisons'][0]['conditional_advantage']))
        self.assertEqual(F(r['comparisons'][1]['conditional_advantage']), F('4.28571428'))
        self.assertIn('refusal', r['comparisons'][2])
        self.assertEqual(self.args(), before)
        self.assertEqual(r['transactions_submitted'], 0)

    def test_duplicate_batch_and_bad_horizon_rejected(self):
        self.batches *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate batches'): self.run_plan()
        self.batches = []
        self.now = True
        with self.assertRaisesRegex(ValueError, 'invalid horizon'): self.run_plan()


if __name__ == '__main__':
    unittest.main()
