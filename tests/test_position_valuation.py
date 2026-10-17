"""Production portfolio regression tests; mocked public RPC, never keys/executors."""
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
sys.path.insert(0, '/home/tydynrain/.hermes/scripts')
import liquidity_agent_v2 as m

class ValuationTests(unittest.TestCase):
    def portfolio(self, prices=None):
        pool = NS(base='A', quote='B', base_quantity=200, quote_quantity=20000,
                  total_shares=100, base_price=100, quote_price=.01, apr=0)
        return m.PortfolioManager(NS(token_prices=prices if prices is not None else {'A':.5,'B':.005},
                                    get_pool=lambda a,b:pool))

    def load(self, pm):
        with patch.object(m,'he',return_value=[{'tokenPair':'A:B','shares':'1','timeFactor':123}]), \
             patch('builtins.open',side_effect=AssertionError('No keys for public reads')):
            pm.load_positions()
        return pm.positions['A:B']

    def test_use_usd_prices_not_exchange_ratios(self):
        self.assertAlmostEqual(self.load(self.portfolio()).value_usd, 2)

    def test_missing_price_is_unknown(self):
        self.assertIsNone(self.load(self.portfolio({'A':.5})).value_usd)

    def test_liquid_excludes_stake(self):
        pm=self.portfolio()
        with patch.object(m,'he',return_value=[{'symbol':'A','balance':'2','stake':'100'}]):
            pm.load_balances()
        self.assertEqual(pm.balances['A'].value_usd,1)
        self.assertEqual(pm.balances['A'].staked_value_usd,50)

    def test_dust_is_not_filtered_from_idle(self):
        pm=self.portfolio()
        with patch.object(m,'he',return_value=[{'symbol':'A','balance':'0.00001','stake':'0'}]):
            pm.load_balances()
        self.assertAlmostEqual(pm.idle_value(),.000005)

    def test_reload_removes_stale_positions(self):
        pm=self.portfolio(); self.load(pm)
        with patch.object(m,'he',return_value=[]): pm.load_positions()
        self.assertEqual(pm.positions,{})

    def test_unknown_total_is_not_complete_value(self):
        pm=self.portfolio({'A':.5}); self.load(pm)
        self.assertIsNone(pm.total_value())
        self.assertEqual(pm.valuation_summary()['unpriced_positions'], ['A:B'])

    def test_invalid_prices_unknown(self):
        for p in [-1,0,float('nan'),float('inf')]:
            self.assertIsNone(self.load(self.portfolio({'A':p,'B':.005})).value_usd)

if __name__=='__main__': unittest.main(verbosity=2)
