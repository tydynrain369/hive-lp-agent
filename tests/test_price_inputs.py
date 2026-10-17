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
    def test_no_historical_external_anchor(self):
        pe=self.engine([pool('SWAP.HIVE:SWAP.HBD',100,5),pool('SWAP.BTC:SWAP.HIVE',1,200)])
        self.assertAlmostEqual(pe.token_prices['SWAP.BTC'],10)
    def test_missing_market_does_not_invent_hive_price(self):
        self.assertNotIn('SWAP.HIVE', self.engine([]).token_prices)
    def test_order_independent_deeper_known_side(self):
        rows=[pool('SWAP.HIVE:SWAP.HBD',100,5),pool('A:SWAP.HBD',100,1),pool('A:SWAP.HBD2',1,9),pool('SWAP.HBD2:SWAP.HBD',100,100)]
        a=self.engine(rows).token_prices; b=self.engine(list(reversed(rows))).token_prices
        self.assertEqual(a,b)
    def test_floor_never_overspends(self):
        pe=self.engine([])
        self.assertEqual(pe.round_amount('A',1.239),1.23)
    def test_unknown_precision_not_invented(self):
        with self.assertRaises(ValueError): self.engine([]).round_amount('MISSING',1.0)
    def test_unknown_fee_has_explicit_status(self):
        p=self.engine([pool('A:SWAP.HBD',100,1)]).pools['A:SWAP.HBD']
        self.assertEqual(p.fee_observation_status,'unknown')

if __name__=='__main__': unittest.main(verbosity=2)
