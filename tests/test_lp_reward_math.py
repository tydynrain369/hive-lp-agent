"""Synthetic independent expectations from upstream; no RPC/broadcasts.
Replaces unfinished delegated fixtures whose denominators/exclusions conflicted.
"""
import sys, unittest, copy, json
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
from lp_reward_math import estimate_batch
DAY=86400000
NOW=100*DAY

def batch():
    return {'_id':1,'strategy':'pool','tokenPair':'A:B','active':True,'numTicksLeft':8,
            'tokenBalances':[{'symbol':'R','quantity':'10'}],
            'excludeAccount':[],'bonusCurve':{}}

def row(name='me',shares='500',days=2):
    return {'account':name,'tokenPair':'A:B','shares':shares,'timeFactor':NOW-days*DAY}

class RewardTests(unittest.TestCase):
    def estimate(self,b=None,rows=None,prec=None,now=NOW):
        return estimate_batch(b if b is not None else batch(),rows if rows is not None else [row(),row('other')],
                              {'distTickHours':'24'},prec if prec is not None else {'R':8},'me',now)
    def test_half_budget(self):
        r=self.estimate(); self.assertEqual(r['status'],'ok')
        self.assertEqual(r['entitlements'][0]['payout_share'],'0.625')
    def test_single_gets_budget(self):
        self.assertEqual(self.estimate(rows=[row()])['entitlements'][0]['payout_share'],'1.25')
    def test_bonus_denominator_all_eligible(self):
        b=batch(); b['bonusCurve']={'numPeriods':'4','periodBonusPct':'50'}
        # me age 2: 500*2=1000; other age 8 capped:500*3=1500. 10/8*1000/2500=.5
        r=self.estimate(b,[row(),row('other',days=8)])
        self.assertEqual(r['share_total'],'2500'); self.assertEqual(r['bonus_multiplier'],'2')
        self.assertEqual(r['entitlements'][0]['payout_share'],'0.5')
    def test_exclusion_changes_denominator(self):
        b=batch(); b['excludeAccount']=['other']
        self.assertEqual(self.estimate(b)['entitlements'][0]['payout_share'],'1.25')
    def test_immature_no_payout(self):
        self.assertFalse(self.estimate(rows=[row(days=.5),row('other')])['eligible'])
    def test_exact_one_tick_raw_no_bonus(self):
        b=batch(); b['bonusCurve']={'numPeriods':'30','periodBonusPct':'100'}
        r=self.estimate(b,[row(days=1),row('other',days=1)])
        self.assertEqual(r['bonus_multiplier'],'1'); self.assertEqual(r['effective_shares'],'500')
    def test_integer_periods_do_not_enable_bonus_upstream(self):
        b=batch(); b['bonusCurve']={'numPeriods':4,'periodBonusPct':'50'}
        self.assertEqual(self.estimate(b)['bonus_multiplier'],'1')
    def test_fractional_time_factor(self):
        r=row(); r['timeFactor']=str(NOW-2*DAY+0.5)
        self.assertEqual(self.estimate(rows=[r])['status'],'ok')
    def test_last_tick(self):
        b=batch(); b['numTicksLeft']=1
        self.assertEqual(self.estimate(b)['entitlements'][0]['payout_share'],'5')
    def test_tiny_rounded_zero(self):
        b=batch(); b['tokenBalances'][0]['quantity']='0.00000001'
        e=self.estimate(b)['entitlements'][0]
        self.assertEqual(e['payout_share'],'0'); self.assertTrue(e['rounded_zero'])
    def test_zero_precision(self):
        self.assertEqual(self.estimate(prec={'R':0})['entitlements'][0]['payout_share'],'0')
    def test_unknown_not_partial_denominator(self):
        r=row('other'); del r['shares']
        result=self.estimate(rows=[row(),r]); self.assertEqual(result['status'],'unknown')
        self.assertIsNone(result['entitlements'])
    def test_missing_precision(self): self.assertEqual(self.estimate(prec={})['status'],'unknown')
    def test_missing_exclusions(self):
        b=batch(); del b['excludeAccount']; self.assertEqual(self.estimate(b)['status'],'unknown')
    def test_duplicate_and_mixed_rows_unknown(self):
        self.assertEqual(self.estimate(rows=[row(),row()])['status'],'unknown')
        r=row(); r['tokenPair']='X:Y'; self.assertEqual(self.estimate(rows=[r])['status'],'unknown')
    def test_bad_budget(self):
        for q in ['oops','NaN','-1']:
            b=batch(); b['tokenBalances'][0]['quantity']=q
            self.assertEqual(self.estimate(b)['status'],'unknown')
    def test_inactive_exhausted_and_mining(self):
        b=batch(); b['active']=False; self.assertEqual(self.estimate(b)['status'],'inactive')
        b=batch(); b['numTicksLeft']=0; self.assertEqual(self.estimate(b)['status'],'exhausted')
        b=batch(); b['strategy']='fixed'; self.assertEqual(self.estimate(b)['status'],'unknown')
    def test_input_immutable_json_output(self):
        b=batch(); before=copy.deepcopy(b); json.dumps(self.estimate(b)); self.assertEqual(b,before)

if __name__=='__main__': unittest.main(verbosity=2)
