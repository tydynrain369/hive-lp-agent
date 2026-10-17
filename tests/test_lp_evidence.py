import sys,unittest
from copy import deepcopy
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
from lp_evidence import promote,candidates_from_assessment,joint_allocation

def scenario(pool='A:B',terminal_unit='B',move='5',stay='4',affected=None,complete=True):
    c = {'pool':pool,'horizon_start_ms':1000,'horizon_end_ms':605000,
            # Synthetic complete-evidence fixture, NOT a real position projection.
            'terminal_unit':terminal_unit,'liquidation_complete':complete,
            'coverage_complete':True,
            'affected_pools':affected or [pool],
            'deposit':{'shares_added':'1','amounts':{'A':'2','B':'2'}},
            'move':{'terminal':move},'stay':{'terminal':stay}}
    c['economic_evidence'] = {k:deepcopy(c[k]) for k in
        ('pool','terminal_unit','horizon_start_ms','horizon_end_ms','deposit','affected_pools')}
    c['economic_evidence'].update(mechanics_verified=True,quotes_fresh=True,
        coverage_complete=True,captured_at_ms=1000,move_earnings_lower=move,
        stay_earnings_upper=stay,switching_cost_upper='0')
    return c
FRESH=dict(captured_at_ms=1000,now_ms=2000,max_staleness_ms=60000)
class PromoteTests(unittest.TestCase):
    def test_complete_scenario_promotes_with_exact_bounds(self):
        c=promote(scenario(),**FRESH)
        self.assertEqual(c['id'],'A:B')
        self.assertEqual(c['move_earnings_lower'],'5')
        self.assertEqual(c['stay_earnings_upper'],'4')
        self.assertEqual(c['spend'],{'A':'2','B':'2'})
        self.assertEqual(c['conflicts'],['A:B'])
        self.assertTrue(c['mechanics_verified'] and c['quotes_fresh'] and c['coverage_complete'])
        self.assertIn('provenance',c)
    def test_no_bounds_invented_from_branch_terminals(self):
        c=scenario(move='900',stay='1')
        c['economic_evidence'].update(move_earnings_lower='3',stay_earnings_upper='4',switching_cost_upper='2')
        e=promote(c,**FRESH)
        self.assertEqual(e['move_earnings_lower'],'3')
        self.assertEqual(e['switching_cost_upper'],'2')
        self.assertEqual(joint_allocation({'comparisons':[c]},{'A':'4','B':'4'},**FRESH)['selection']['selected'],[])

    def test_missing_evidence_rejected_even_with_coverage_flag(self):
        c=scenario(); del c['economic_evidence']
        with self.assertRaisesRegex(ValueError,'explicit verified'):
            promote(c,**FRESH)

    def test_mismatched_action_rejected(self):
        c=scenario(); c['deposit']['amounts']['A']='3'
        with self.assertRaisesRegex(ValueError,'action mismatch'):
            promote(c,**FRESH)

    def test_conditional_projection_cannot_be_promoted(self):
        c=scenario(); c['coverage_complete']=False
        with self.assertRaisesRegex(ValueError,'coverage incomplete'):
            promote(c,**FRESH)

    def test_missing_coverage_cannot_be_promoted(self):
        c=scenario(); del c['coverage_complete']
        with self.assertRaisesRegex(ValueError,'coverage incomplete'):
            promote(c,**FRESH)

    def test_incomplete_liquidation_is_unknown_not_zero(self):
        with self.assertRaisesRegex(ValueError,'unknown'):
            promote(scenario(complete=False),**FRESH)
    def test_missing_terminal_unit_rejected(self):
        c=scenario(); c['terminal_unit']=''; c['economic_evidence']['terminal_unit']=''
        with self.assertRaisesRegex(ValueError,'terminal unit'):
            promote(c,**FRESH)
    def test_stale_snapshot_rejected(self):
        with self.assertRaisesRegex(ValueError,'not fresh'):
            promote(scenario(),captured_at_ms=1000,now_ms=62000,max_staleness_ms=60000)
    def test_non_integer_horizon_rejected(self):
        c=scenario(); c['horizon_end_ms']='later'; c['economic_evidence']['horizon_end_ms']='later'
        with self.assertRaisesRegex(ValueError,'horizon'):
            promote(c,**FRESH)
    def test_degenerate_deposit_rejected(self):
        c=scenario(); c['deposit']={'shares_added':'0','amounts':{}}; c['economic_evidence']['deposit']=deepcopy(c['deposit'])
        with self.assertRaisesRegex(ValueError,'degenerate'):
            promote(c,**FRESH)
    def test_conflicts_carry_route_pools(self):
        c=promote(scenario(affected=['A:B','C:D','B:E']),**FRESH)
        self.assertEqual(c['conflicts'],['A:B','B:E','C:D'])
class AssessmentTests(unittest.TestCase):
    def test_split_keeps_rejections_with_reasons(self):
        good=scenario(); bad=scenario(pool='X:Y',complete=False)
        cands,rej=candidates_from_assessment({'comparisons':[good,bad]},**FRESH)
        self.assertEqual([x['id'] for x in cands],['A:B'])
        self.assertEqual(rej,[{'pool':'X:Y','reason':rej[0]['reason']}])
        self.assertIn('unknown',rej[0]['reason'])
class JointAllocationTests(unittest.TestCase):
    def test_selects_positive_candidates_and_reserves_shared_wallet(self):
        r=joint_allocation({'comparisons':[scenario(move='9'),scenario(pool='C:D')]},
                           {'A':'3','B':'3'},**FRESH)
        self.assertEqual(r['selection']['selected'],['A:B'])
        self.assertEqual(r['selection']['reserved'],{'A':'2','B':'2'})
        self.assertTrue(r['conditional'] and r['transactions_submitted']==0)
        self.assertEqual(r['rejected'],[])
    def test_shared_route_pool_forces_single_pick(self):
        r=joint_allocation({'comparisons':[scenario(move='9'),
                                           scenario(pool='C:D',affected=['A:B','C:D'])]},
                           {'A':'4','B':'4'},**FRESH)
        self.assertEqual(r['selection']['selected'],['A:B'])
    def test_mixed_terminal_units_refused(self):
        with self.assertRaisesRegex(ValueError,'terminal units'):
            joint_allocation({'comparisons':[scenario(),scenario(pool='C:D',terminal_unit='D')]},
                             {'A':'9','B':'9'},**FRESH)
    def test_incomplete_comparisons_rejected_not_selected(self):
        r=joint_allocation({'comparisons':[scenario(complete=False)]},{'A':'4','B':'4'},**FRESH)
        self.assertEqual(r['selection']['selected'],[])
        self.assertEqual(len(r['rejected']),1)
        self.assertEqual(r['candidate_count'],0)
if __name__=='__main__':unittest.main(verbosity=2)
