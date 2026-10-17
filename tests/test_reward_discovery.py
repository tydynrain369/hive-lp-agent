import sys, unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
import cron_lp_agent as c

class DiscoveryTests(unittest.TestCase):
    def test_batches_not_merged_or_bonus_inflated(self):
        rows=[{'_id':i,'active':True,'strategy':'pool','tokenPair':'A:B','numTicksLeft':10,
               'tokenBalances':[{'symbol':'R','quantity':'100'}],
               'bonusCurve':{'numPeriods':30,'periodBonusPct':'1'}} for i in [1,2]]
        def rpc(method,params):
            return [{'distTickHours':'12'}] if params['table']=='params' else rows if params['table']=='batches' else []
        with patch.object(c,'he',side_effect=rpc): r=c.discover_rewards(NS(),NS())['A:B']
        self.assertEqual(len(r),2)
        self.assertEqual([x['batch_id'] for x in r],[1,2])
        self.assertTrue(all(x['per_tick_pool_budget']==10 for x in r))
        self.assertTrue(all(x['daily_reward']==20 for x in r))
        self.assertTrue(all(x['entitlement_status']=='not_calculated' for x in r))
        self.assertEqual(r[0]['batch']['bonusCurve'],rows[0]['bonusCurve'])
    def test_rpc_failure_not_empty_landscape(self):
        with patch.object(c,'he',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError): c.discover_rewards(NS(),NS())

if __name__=='__main__': unittest.main(verbosity=2)
