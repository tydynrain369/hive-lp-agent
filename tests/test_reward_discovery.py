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
            # findOne on the REAL node returns a single OBJECT, not a list —
            # the old broad try/except masked this wrong fixture shape
            # (2026-10-22). rows for 'find' stay lists.
            if params['table']=='params': return {'distTickHours':'12'}
            return rows if params['table']=='batches' else []
        # portfolio must expose .positions (our_time_factor lookup reads it)
        with patch.object(c,'he',side_effect=rpc): r=c.discover_rewards(NS(),NS(positions={}))['A:B']
        self.assertEqual(len(r),2)
        # batch_id is deliberately STRINGIFIED (2026-10-08 batch-scoped
        # identity: str(b.get('_id',''))) — pin the string form.
        self.assertEqual([x['batch_id'] for x in r],['1','2'])
        # 2026-10-22 re-pin: the 2026-09-30 denominator redesign (Tydyn-
        # confirmed: figures are whole-batch tokenBalances / numTicks —
        # per-TICK whole-pool amounts, NOT per-day and NOT our share)
        # replaced per_tick_pool_budget/entitlement_status. Pin the CURRENT
        # contract: numTicks defaults to 1 for these rows, so the per-tick
        # whole-pool figure is the full 100 R; the denominator is explicit.
        for x in r:
            self.assertEqual(x['num_ticks'],1)
            self.assertEqual(x['daily_reward'],100.0)
            self.assertEqual(x['whole_batch_quantity'],100.0)
            self.assertIn('numTicks',x['denominator'])
            self.assertFalse(x['units_comparable_across_tokens'])
        self.assertEqual(r[0]['bonus_curve'],rows[0]['bonusCurve'])
        self.assertEqual(r[1]['bonus_curve'],rows[1]['bonusCurve'])
    def test_rpc_failure_not_empty_landscape(self):
        with patch.object(c,'he',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError): c.discover_rewards(NS(),NS())

if __name__=='__main__': unittest.main(verbosity=2)
