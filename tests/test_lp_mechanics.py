"""Synthetic fixtures, independently derived from deployed marketpools v13."""
import sys, unittest
from decimal import Decimal as D
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
from lp_mechanics import swap_exact_in, add_liquidity, remove_liquidity, best_route, QuoteError
P={'tokenPair':'A:B','baseQuantity':'100','quoteQuantity':'200','totalShares':'100','precision':8}
PREC={'A':8,'B':8,'C':8}
class MechanicsTests(unittest.TestCase):
    def test_fee_and_direction(self):
        q=swap_exact_in(P,'A','10',PREC,{'tradeFeeMul':'0.9975'})
        self.assertEqual(q['amount_out'],'18.14048647')
        self.assertEqual(q['symbol_out'],'B')
        r=swap_exact_in(P,'B','20',PREC,{'tradeFeeMul':'0.9975'})
        self.assertEqual(r['amount_out'],'9.07024323')
    def test_fee_required(self):
        with self.assertRaises(QuoteError): swap_exact_in(P,'A','1',PREC,{})
    def test_precision_zero_output_rejected(self):
        with self.assertRaises(QuoteError): swap_exact_in(P,'A','0.00000001',{'A':8,'B':0},{'tradeFeeMul':'0.9975'})
    def test_invalid_symbol_and_overprecision(self):
        for symbol,qty in [('C','1'),('A','0.000000001'),('A','NaN')]:
            with self.assertRaises(QuoteError): swap_exact_in(P,symbol,qty,PREC,{'tradeFeeMul':'0.9975'})
    def test_add_and_weighted_age(self):
        q=add_liquidity(P,'10','20',PREC,{'A':'10','B':'20'},1000,{'shares':'10','timeFactor':0})
        self.assertEqual(q['shares_added'],'10'); self.assertEqual(q['timeFactor'],500)
        self.assertEqual(q['pool_after']['totalShares'],'110')
    def test_add_price_impact_guard(self):
        with self.assertRaises(QuoteError): add_liquidity(P,'10','30',PREC,{'A':'10','B':'30'},1000)
    def test_add_funding_guard(self):
        with self.assertRaises(QuoteError): add_liquidity(P,'10','20',PREC,{'A':'9','B':'20'},1000)
    def test_remove_percentage_not_shares(self):
        q=remove_liquidity(P,'10','50',PREC)
        self.assertEqual(q['shares_removed'],'5'); self.assertEqual(q['amounts'],{'A':'5','B':'10'})
        self.assertEqual(q['pool_after']['totalShares'],'95')
    def test_remove_rounded_zero_fails(self):
        with self.assertRaises(QuoteError): remove_liquidity(P,'0.00000000001','100',PREC)
    def test_swap_updates_volume_counters(self):
        base=dict(P,baseVolume='100',quoteVolume='300')
        q=swap_exact_in(base,'A','10',PREC,{'tradeFeeMul':'0.9975'})
        after=q['pool_after']
        self.assertEqual(after['baseVolume'],'110')
        self.assertEqual(after['quoteVolume'],'318.14048647')

    def test_swap_other_direction_updates_volumes(self):
        base=dict(P,baseVolume='100',quoteVolume='300')
        q=swap_exact_in(base,'B','20',PREC,{'tradeFeeMul':'0.9975'})
        after=q['pool_after']
        self.assertEqual(after['baseVolume'],'109.07024323')
        self.assertEqual(after['quoteVolume'],'320')

    def test_swap_absent_volume_counters_start_from_zero(self):
        q=swap_exact_in(P,'A','10',PREC,{'tradeFeeMul':'0.9975'})
        after=q['pool_after']
        self.assertEqual(after['baseVolume'],'10')
        self.assertEqual(after['quoteVolume'],'18.14048647')

    def test_add_remove_leave_volumes_unchanged(self):
        base=dict(P,baseVolume='7',quoteVolume='14')
        q=add_liquidity(base,'10','20',PREC,{'A':'10','B':'20'},1000)
        self.assertEqual((q['pool_after']['baseVolume'],q['pool_after']['quoteVolume']),('7','14'))
        r=remove_liquidity(base,'10','50',PREC)
        self.assertEqual((r['pool_after']['baseVolume'],r['pool_after']['quoteVolume']),('7','14'))

    def test_route_two_hops_and_no_mutation(self):
        pools={'A:B':P,'B:C':dict(P,tokenPair='B:C')}
        q=best_route(pools,'A','C','1',PREC,{'tradeFeeMul':'0.9975'},max_hops=2)
        self.assertEqual(len(q['legs']),2); self.assertEqual(P['baseQuantity'],'100')
        self.assertEqual(q['legs'][0]['amount_out'],q['legs'][1]['amount_in'])
    def test_unknown_route(self):
        with self.assertRaises(QuoteError): best_route({'A:B':P},'A','C','1',PREC,{'tradeFeeMul':'0.9975'})
if __name__=='__main__': unittest.main(verbosity=2)
