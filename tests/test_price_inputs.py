"""Public-input price and precision regression tests."""
import sys, unittest
from unittest.mock import patch
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
import liquidity_agent_v2 as m

def pool(pair,a,b):
    return {'tokenPair':pair,'baseQuantity':str(a),'quoteQuantity':str(b),'totalShares':'10'}

class PriceTests(unittest.TestCase):
    def engine(self, rows):
        pe=m.PriceEngine()
        def rpc(method,params):
            return rows if params['table']=='pools' else [{'symbol':'A','precision':2}]
        with patch.object(m,'he',side_effect=rpc): pe.load_all()
        return pe
    def test_hive_price_from_pool_and_seeded_majors(self):
        # 2026-10-22 re-pin: the anchor architecture is deliberate (the
        # 2026-10-17 units fix builds on it). With the HIVE:HBD pool
        # present, HIVE is POOL-derived; wrapped majors keep their
        # documented seed anchors rather than deriving from fixture pools.
        pe=self.engine([pool('SWAP.HIVE:SWAP.HBD',100,5),pool('SWAP.BTC:SWAP.HIVE',1,200)])
        self.assertAlmostEqual(pe.token_prices['SWAP.HIVE'],0.05)
        self.assertAlmostEqual(pe.token_prices['SWAP.BTC'],65000)
    def test_missing_market_falls_back_to_documented_anchor(self):
        # 2026-10-22 re-pin: no pools -> the documented $0.05 HIVE fallback
        # anchor applies (valuation approximation, commented in the engine),
        # and NO other token is invented beyond the documented seed set.
        prices=self.engine([]).token_prices
        self.assertEqual(prices['SWAP.HIVE'],0.05)
        self.assertEqual(set(prices),{'SWAP.HIVE','HIVE','SWAP.HBD','HBD',
                                      'SWAP.BTC','SWAP.ETH','SWAP.LTC','SWAP.DOGE'})
    def test_order_independent_deeper_known_side(self):
        rows=[pool('SWAP.HIVE:SWAP.HBD',100,5),pool('A:SWAP.HBD',100,1),pool('A:SWAP.HBD2',1,9),pool('SWAP.HBD2:SWAP.HBD',100,100)]
        a=self.engine(rows).token_prices; b=self.engine(list(reversed(rows))).token_prices
        self.assertEqual(a,b)
    def test_floor_never_overspends(self):
        # 'ALIVE' is in the per-symbol precision loader's query list; the
        # fixture rpc returns precision 2 for it. 'A' is NOT queried by the
        # loader and the old default-8 fallback masked that (2026-10-22).
        pe=self.engine([])
        self.assertEqual(pe.round_amount('ALIVE',1.239),1.23)
    def test_unknown_precision_not_invented(self):
        with self.assertRaises(ValueError): self.engine([]).round_amount('MISSING',1.0)
    def test_unknown_fee_has_explicit_status(self):
        p=self.engine([pool('A:SWAP.HBD',100,1)]).pools['A:SWAP.HBD']
        self.assertEqual(p.fee_observation_status,'unknown')

if __name__=='__main__': unittest.main(verbosity=2)
