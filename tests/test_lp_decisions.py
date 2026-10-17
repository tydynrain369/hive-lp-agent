import sys,unittest
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
from lp_decisions import compare,choose_joint

def candidate(id,gain,spend,pool=None):
    return dict(id=id,mechanics_verified=True,quotes_fresh=True,coverage_complete=True,
                terminal_unit='B',horizon_start_ms=1,horizon_end_ms=2,move_earnings_lower=str(gain),
                stay_earnings_upper='0',switching_cost_upper='0',spend={'A':str(spend)},conflicts=[pool or id])
class DecisionsTests(unittest.TestCase):
    def test_mixed_terminal_units_are_not_additive(self):
        a=candidate('a',10,1); a['terminal_unit']='A'
        b=candidate('b',10,1); b['terminal_unit']='B'
        with self.assertRaisesRegex(ValueError,'terminal units'):
            choose_joint([a,b],{'A':2})
    def test_missing_terminal_unit_blocks(self):
        c=candidate('a',10,1); c.pop('terminal_unit',None)
        self.assertEqual(compare(c)['decision'],'observe')
    def test_unknown_blocks(self):
        c=candidate('x',10,1); c['switching_cost_upper']=None
        self.assertEqual(compare(c)['decision'],'observe')
    def test_no_dollar_minimum(self):
        self.assertEqual(compare(candidate('tiny','.00000001','.00001'))['decision'],'candidate')
    def test_net_cost_and_hold(self):
        c=candidate('x',2,1);c['stay_earnings_upper']='1';c['switching_cost_upper']='1'
        self.assertEqual(compare(c)['decision'],'hold')
    def test_joint_beats_greedy_and_reserves_once(self):
        r=choose_joint([candidate('a',10,10),candidate('b',6,5),candidate('c',6,5)],{'A':'10'})
        self.assertEqual(r['selected'],['b','c']);self.assertEqual(r['reserved'],{'A':'10'})
    def test_shared_pool_conflicts(self):
        r=choose_joint([candidate('a',4,1,'route'),candidate('b',5,1,'route')],{'A':'2'})
        self.assertEqual(r['selected'],['b'])
    def test_malformed_conflicts_rejected(self):
        for conflicts in ('route', [''], [1], {'route': True}):
            with self.subTest(conflicts=conflicts):
                c=candidate('a',4,1); c['conflicts']=conflicts
                with self.assertRaisesRegex(ValueError,'conflicts'):
                    choose_joint([c],{'A':2})
    def test_horizons_must_match(self):
        a=candidate('a',1,1);b=candidate('b',1,1);b['horizon_end_ms']=3
        with self.assertRaises(ValueError):choose_joint([a,b],{'A':2})
    def test_missing_fees_not_zero(self):
        c=candidate('x',1,1);c['coverage_complete']=False
        self.assertEqual(choose_joint([c],{'A':2})['selected'],[])
if __name__=='__main__':unittest.main(verbosity=2)
