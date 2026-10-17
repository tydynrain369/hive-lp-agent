"""Bounded allocation search regressions; synthetic, no broadcasts."""
import unittest
from copy import deepcopy
from fractions import Fraction as F
from lp_alloc_search import search_portfolios
import test_lp_position_scenario as fixture


class SearchBase(unittest.TestCase):
    def setUp(self):
        f = fixture.PositionScenarioTest(); f.setUp()
        self.pools = {'A:B': f.pool}
        self.wallet = {'A': '20', 'B': '10'}
        self.rows = {'A:B': f.rows}
        self.batches = f.batches
        self.prec = {'A': 8, 'B': 8, 'C': 8, 'X': 8}
        self.now = 10 * fixture.DAY
        self.args = (self.pools, self.wallet, self.rows, self.batches, self.prec,
                     {'tradeFeeMul': '0.9975'}, {'distTickHours': '24'},
                     'ours', self.now, self.now + 86400000)

    def add_pool_AC(self):
        from test_lp_liquidation import pool
        self.pools['A:C'] = pool('A:C', '100', '100')
        self.rows['A:C'] = [dict(r, tokenPair='A:C') for r in self.rows['A:B']]
        self.wallet['C'] = '10'
        self.batches.append(dict(deepcopy(self.batches[0]), _id=2, tokenPair='A:C'))

    def search(self, **kw):
        return search_portfolios(*self.args, terminal_unit='B', **kw)


class SearchTest(SearchBase):
    def test_ranks_complete_candidates_by_advantage_and_quarantines_rest(self):
        self.add_pool_AC()
        before = deepcopy(self.args)
        r = self.search(multiples=('1',), max_actions=2)
        self.assertEqual(r['transactions_submitted'], 0)
        self.assertEqual(r['status'], 'conditional-simulation')
        self.assertEqual(self.args, before)
        ranked, rest = r['ranked'], r['not_rankable']
        self.assertTrue(ranked)
        advs = [F(c['conditional_advantage']) for c in ranked]
        self.assertEqual(advs, sorted(advs, reverse=True))
        for c in ranked:
            self.assertIsNotNone(c['terminal'])
            self.assertIsNone(c.get('refusal'))
            self.assertFalse(c['execution_allowed'])
        for c in rest:
            self.assertTrue('refusal' in c or c['terminal'] is None)
        self.assertTrue(all(c['terminal'] is None or 'refusal' in c for c in rest))
        # The oversized plan (both pools x2) must be refused, never ranked.
        self.assertNotIn('both-full-x2', [c['id'] for c in ranked])

    def test_unknown_valuation_cannot_rank_above_complete(self):
        self.add_pool_AC()
        self.wallet['X'] = '1'
        r = self.search(multiples=('1',), max_actions=2)
        self.assertEqual(r['ranked'], [])
        self.assertTrue(all(c['terminal'] is None for c in r['not_rankable']))

    def test_bounded_evaluation_budget(self):
        self.add_pool_AC()
        from test_lp_liquidation import pool
        for pair in ('A:D', 'A:E'):
            self.pools[pair] = pool(pair, '100', '100')
            self.rows[pair] = [dict(r, tokenPair=pair) for r in self.rows['A:B']]
            self.wallet[pair.split(':')[1]] = '10'
            self.prec[pair.split(':')[1]] = 8
        r = self.search(multiples=('0.25', '1'), max_actions=4, budget=30)
        self.assertLessEqual(r['evaluated'], 30)
        self.assertTrue(r['truncated'])
        self.assertLessEqual(len(r['ranked']) + len(r['not_rankable']), 30)

    def test_candidate_amounts_respect_token_precision(self):
        self.prec['B'] = 0
        r = self.search(multiples=('0.25', '0.5', '1'))
        for c in r['ranked'] + r['not_rankable']:
            for act in c.get('actions', []):
                for sym, qty in act['amounts'].items():
                    self.assertEqual((F(qty) * 10 ** self.prec[sym]).denominator, 1)

    def test_orderings_only_within_budget_and_single_pool_default(self):
        self.add_pool_AC()
        r = self.search(multiples=('1',), max_actions=1)
        ids = {c['id'] for c in r['ranked'] + r['not_rankable']}
        self.assertTrue(any('A:B' in i for i in ids) and any('A:C' in i for i in ids))


if __name__ == '__main__':
    unittest.main()
