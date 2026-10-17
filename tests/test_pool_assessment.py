import sys,unittest
from types import SimpleNamespace as N
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
from lp_pool_assessment import assess_pools
class AssessmentTests(unittest.TestCase):
    def setup_data(self):
        pe=N(pools={'A:B':N(base='A',quote='B',base_reserve=100,quote_reserve=100)},token_precisions={'A':8,'B':8})
        pf=N(positions={'A:B':N(shares=1,value_usd=None)},balances={'A':N(balance=.000001,staked=1000),'B':N(balance=.000001,staked=1000)})
        return pe,pf
    def test_tiny_pair_considered_without_price(self):
        pe,pf=self.setup_data(); r=assess_pools(pe,pf,{})[0]
        self.assertIsNotNone(r['direct_amounts']); self.assertEqual(r['decision'],'hold')
        self.assertEqual(r['fee_income'],'unknown'); self.assertEqual(r['net_advantage'],'unknown')
    def test_stake_never_spent(self):
        pe,pf=self.setup_data(); pf.balances['A'].balance=0
        self.assertIsNone(assess_pools(pe,pf,{})[0]['direct_amounts'])
    def test_all_pools_and_no_wallet_mutation(self):
        pe,pf=self.setup_data(); pe.pools['B:C']=N(base='B',quote='C',base_reserve=10,quote_reserve=20)
        rows=assess_pools(pe,pf,{})
        self.assertEqual(len(rows),2); self.assertEqual(pf.balances['B'].balance,.000001)
    def test_pepe_metadata_never_moves_without_evidence(self):
        pe,pf=self.setup_data(); rows=assess_pools(pe,pf,{'A:B':[{'mined_token':'PEPE','batch_id':1}]})
        self.assertTrue(rows[0]['pepe_related']); self.assertEqual(rows[0]['decision'],'hold')
if __name__=='__main__': unittest.main(verbosity=2)
