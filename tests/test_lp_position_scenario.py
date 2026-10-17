"""Synthetic, hand-computable counterfactuals; NOT live earnings evidence."""
import unittest
from copy import deepcopy
from decimal import Decimal as D
from lp_position_scenario import position_scenario

DAY = 86400000

class PositionScenarioTest(unittest.TestCase):
    def setUp(self):
        self.pool = {'tokenPair': 'A:B', 'baseQuantity': '100', 'quoteQuantity': '100',
                     'totalShares': '100', 'precision': 8}
        self.rows = [{'account': a, 'tokenPair': 'A:B', 'shares': s, 'timeFactor': 0}
                     for a, s in [('ours', '10'), ('other', '90')]]
        self.batches = [{'_id': 1, 'strategy': 'pool', 'numTicksLeft': 1, 'active': True,
                         'lastTickTime': 9*DAY, 'tokenPair': 'A:B', 'excludeAccount': [],
                         'bonusCurve': {'numPeriods': '30', 'periodBonusPct': '10'},
                         'tokenBalances': [{'symbol': 'B', 'quantity': '100'}]}]

    def run_scenario(self):
        return position_scenario(self.pool, {'A': '10', 'B': '10'}, self.rows, self.batches,
                                 {'A': 8, 'B': 8, 'C': 8}, {'tradeFeeMul': '0.9975'},
                                 {'distTickHours': '24'}, 'ours', 10*DAY, 11*DAY,
                                 {'A': '10', 'B': '10'})

    def test_hand_calculated_age_and_both_rewards(self):
        before = deepcopy((self.pool, self.rows, self.batches))
        r = self.run_scenario()
        # Mint 10 shares; whole-position weighted timestamp = day 5.
        self.assertEqual(D(r['position_after']['shares']), D(20))
        self.assertEqual(r['position_after']['timeFactor'], 5*DAY)
        self.assertEqual(r['time_factor_delta_ms'], 5*DAY)
        # At day 10: keep = 20/(20+180); topup = 30/(30+180).
        self.assertEqual(r['stay']['our_rewards']['B'], '10')
        self.assertEqual(r['move']['our_rewards']['B'], '14.28571428')
        self.assertEqual(r['stay']['ticks'][0]['our_result']['bonus_multiplier'], '2')
        self.assertEqual(r['move']['ticks'][0]['our_result']['bonus_multiplier'], '1.5')
        self.assertEqual(r['stay']['withdrawal']['amounts'], {'A': '10', 'B': '10'})
        self.assertEqual(r['move']['withdrawal']['amounts'], {'A': '20', 'B': '20'})
        # Both branches leave reserves 90:90 and liquidate the same 20 A.
        self.assertEqual(r['stay']['terminal_quote'], r['move']['terminal_quote'])
        self.assertEqual(D(r['conditional_advantage']), D('4.28571428'))
        self.assertFalse(r['coverage_complete'])
        self.assertEqual((self.pool, self.rows, self.batches), before)

    def routed(self, routes, settle='B', max_hops=3):
        return position_scenario(self.pool, {'A':'10','B':'10'}, self.rows, self.batches,
            {'A':8,'B':8,'C':8}, {'tradeFeeMul':'0.9975'}, {'distTickHours':'24'},
            'ours', 10*DAY, 11*DAY, {'A':'10','B':'10'},
            routes=routes, terminal_unit=settle, max_hops=max_hops)

    def test_third_reward_routed_after_principal_with_shared_reserves(self):
        from fractions import Fraction as F
        from test_lp_liquidation import reference, pool
        self.batches[0]['tokenBalances'][0]['symbol'] = 'C'
        routes = {'C:A':pool('C:A','100','100')}
        before = deepcopy((self.pool, routes, self.rows, self.batches))
        r = self.routed(routes)
        for name, reward in [('stay','10'), ('move','14.28571428')]:
            branch = r[name]
            first, ri, ro = reference(pool('A:B','90','90'), 'A','20',8)
            via, _, _ = reference(routes['C:A'],'C',reward,8)
            second, _, _ = reference(pool('A:B',ri,ro),'A',via,8)
            self.assertEqual(F(branch['terminal']), F(20)+first+second)
            self.assertEqual(branch['liquidation']['sale_order'], ['A','B','C'])
            self.assertEqual(branch['liquidation']['sales']['C']['legs'][1]['amount_in'],
                             branch['liquidation']['sales']['C']['legs'][0]['amount_out'])
            self.assertEqual(branch['unliquidated_rewards'], {})
        self.assertTrue(r['liquidation_complete'])
        self.assertFalse(r['coverage_complete'])
        self.assertFalse(r['execution_allowed'])
        self.assertEqual(r['affected_pools'], ['A:B','C:A'])
        self.assertEqual((self.pool, routes, self.rows, self.batches), before)

    def test_common_unit_and_same_token_aggregation(self):
        from fractions import Fraction as F
        from test_lp_liquidation import reference, pool
        routes = {'B:C':pool('B:C','1000','1000')}
        r = self.routed(routes, settle='C')
        for name, reward in [('stay','10'), ('move','14.28571428')]:
            first,_,_ = reference(pool('A:B','90','90'),'A','20',8)
            via,ri,ro = reference(routes['B:C'],'B',first,8)
            second,_,_ = reference(pool('B:C',ri,ro),'B',F(20)+F(reward),8)
            self.assertEqual(F(r[name]['terminal']),via+second)
        self.assertEqual(r['terminal_unit'],'C')
        self.assertEqual(D(r['conditional_advantage']),D(r['move']['terminal'])-D(r['stay']['terminal']))

    def test_unroutable_principal_not_partial_total(self):
        r = self.routed({}, settle='C')
        for name in ('stay','move'):
            self.assertIsNone(r[name]['terminal'])
            self.assertTrue(r[name]['unliquidated_assets'])
        self.assertIsNone(r['conditional_advantage'])
        self.assertFalse(r['liquidation_complete'])

    def test_hop_bound_is_reported_and_blocks_long_route(self):
        from test_lp_liquidation import pool
        r = self.routed({'B:C':pool('B:C')}, settle='C', max_hops=1)
        self.assertIsNone(r['conditional_advantage'])
        self.assertEqual(r['stay']['liquidation']['unroutable'], ['A'])
        self.assertEqual(r['stay']['liquidation']['max_hops'],1)

    def test_no_rewards_has_no_invented_advantage(self):
        self.batches = []
        self.assertEqual(D(self.run_scenario()['conditional_advantage']), 0)

    def test_unfunded_active_batches_are_skipped(self):
        funded = deepcopy(self.batches)
        expected = self.run_scenario()
        for balances in ('missing', []):
            with self.subTest(balances=balances):
                unfunded = dict(deepcopy(funded[0]), _id=2)
                if balances == 'missing':
                    del unfunded['tokenBalances']
                else:
                    unfunded['tokenBalances'] = balances
                self.batches = funded + [unfunded]
                self.assertEqual(self.run_scenario(), expected)

    def test_third_token_blocks_comparison(self):
        self.batches[0]['tokenBalances'][0]['symbol'] = 'C'
        r = self.run_scenario()
        self.assertIsNone(r['conditional_advantage'])
        self.assertTrue(r['stay']['unliquidated_rewards'])
        self.assertTrue(r['move']['unliquidated_rewards'])

    def test_excluded_position_rewards_zero_both_branches(self):
        self.batches[0]['excludeAccount'] = ['ours']
        r = self.run_scenario()
        self.assertEqual(D(r['conditional_advantage']), 0)

    def test_invalid_participants_fail_closed(self):
        for rows in [[], self.rows + [self.rows[0]], [self.rows[1]],
                     [dict(self.rows[0], account='absent'), self.rows[1]]]:
            with self.subTest(rows=rows):
                original = self.rows
                self.rows = rows
                with self.assertRaises(ValueError): self.run_scenario()
                self.rows = original

    def test_multiple_batches_independent_budgets(self):
        self.batches.append(dict(deepcopy(self.batches[0]), _id=2))
        r = self.run_scenario()
        self.assertEqual(D(r['stay']['our_rewards']['B']), D(20))
        self.assertEqual(D(r['move']['our_rewards']['B']), D('28.57142856'))

    def test_multitick_budget_reserves_rounded_all_participant_payouts(self):
        self.batches[0]['numTicksLeft'] = 2
        r = self.run_scenario()
        ticks = r['move']['ticks']
        self.assertEqual(len(ticks), 2)
        first_reserve = D(ticks[0]['reserved']['B'])
        second_budget = D(ticks[1]['our_result']['entitlements'][0]['tick_budget'])
        self.assertEqual(second_budget, D(100)-first_reserve)

if __name__ == '__main__': unittest.main()
