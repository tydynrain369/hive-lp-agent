import sys,unittest
from fractions import Fraction
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
from lp_fee_evidence import (snapshot_fee_state,diff_fee_snapshots,
                             collect_fee_evidence,KNOWN_CONTRACT_HASHES,
                             _fmt_amount)

BASE={'tokenPair':'AAA:BBB','baseSymbol':'AAA','quoteSymbol':'BBB',
      'baseQuantity':'1000','quoteQuantity':'2000','totalShares':'100',
      'baseVolume':'50','quoteVolume':'80','precision':8}

def posr(shares): return {'shares':shares}

def fake_rpc(pool=BASE,position=None,fail=False):
    def rpc(contract,table,query):
        if fail: raise RuntimeError('rpc down')
        if table=='pools':
            return [pool] if (not query or query.get('tokenPair')==pool['tokenPair']) else []
        if table=='liquidityPositions':
            return [position] if position else []
        return []
    return rpc

class SnapshotTests(unittest.TestCase):
    def test_snapshot_records_exact_share(self):
        s=snapshot_fee_state(fake_rpc(position=posr('25')),'alice','AAA:BBB',at_ms=123)
        self.assertEqual(s['status'],'ok')
        self.assertEqual(s['pool'],'AAA:BBB')
        self.assertEqual(s['account'],'alice')
        self.assertEqual(s['share'],{'num':'1','den':'4'})
        self.assertEqual(s['at_ms'],123)
        self.assertEqual(s['total_shares'],'100')
        self.assertEqual(s['reserves'],{'AAA':'1000','BBB':'2000'})
    def test_missing_position_share_null(self):
        s=snapshot_fee_state(fake_rpc(position=None),'alice','AAA:BBB',at_ms=1)
        self.assertEqual(s['status'],'ok')
        self.assertIsNone(s['share'])
        self.assertIn('reason',s)
    def test_missing_pool(self):
        s=snapshot_fee_state(fake_rpc(),'alice','ZZZ:QQQ',at_ms=1)
        self.assertNotEqual(s.get('status'),'ok')
        self.assertIn('reason',s)

class DiffTests(unittest.TestCase):
    def snap(self,shares='100',pos='25',base='1000',quote='2000',at=1,prec=8):
        pool=dict(BASE,baseQuantity=base,quoteQuantity=quote,totalShares=shares,precision=prec)
        return snapshot_fee_state(fake_rpc(pool=pool,position=posr(pos)),'alice','AAA:BBB',at_ms=at)
    def test_constant_share_fee_growth_exact(self):
        # reserves grow 1000->1100 base, 2000->2100 quote; share 25/100=1/4
        # 2026-10-22 re-pin: reserve deltas are reported as NET RESERVE
        # MOVEMENT (whole-pool delta + our share), NEVER as fees — the
        # attributable_fees field is None and fee truth is volume-based.
        before=self.snap(at=1); after=self.snap(base='1100',quote='2100',at=2)
        d=diff_fee_snapshots(before,after)
        self.assertEqual(d['status'],'measured')
        self.assertIsNone(d['attributable_fees'])
        self.assertIn('NOT fees', d['fee_basis'])
        self.assertEqual(d['reserve_movement']['AAA']['net_reserve_delta'],'100')
        self.assertEqual(d['reserve_movement']['AAA']['our_share_of_delta'],'25')
        self.assertEqual(d['reserve_movement']['BBB']['net_reserve_delta'],'100')
        self.assertEqual(d['reserve_movement']['BBB']['our_share_of_delta'],'25')
    def test_fractional_attribution_exact(self):
        # share 25/100 = 1/4; base grows by 5 -> our 5/4; precision 8
        before=self.snap(at=1); after=self.snap(base='1005',quote='2004',at=2)
        d=diff_fee_snapshots(before,after)
        self.assertEqual(d['status'],'measured')
        self.assertIsNone(d['attributable_fees'])
        self.assertEqual(d['reserve_movement']['AAA']['our_share_of_delta'],_fmt_amount(Fraction(5,4),8))
        self.assertEqual(d['reserve_movement']['BBB']['our_share_of_delta'],_fmt_amount(Fraction(1,1),8))
    def test_totalshares_changed_unmeasured(self):
        before=self.snap(at=1)
        after=self.snap(shares='110',base='1100',quote='2200',at=2)
        d=diff_fee_snapshots(before,after)
        self.assertEqual(d['status'],'unmeasured')
        self.assertEqual(d['reason'],'share base changed; fee growth not isolatable')
        self.assertIsNone(d['attributable_fees'])
    def test_account_shares_changed_unmeasured(self):
        before=self.snap(at=1)
        after=self.snap(pos='30',base='1100',quote='2200',at=2)
        d=diff_fee_snapshots(before,after)
        self.assertEqual(d['status'],'unmeasured')
        self.assertEqual(d['reason'],'share base changed; fee growth not isolatable')
    def test_reserves_decline_measured_when_share_constant(self):
        # constant share base, reserves tick down (rounding effects) -> measured per-token diff
        before=self.snap(at=1)
        after=self.snap(base='999',quote='2000',at=2)
        d=diff_fee_snapshots(before,after)
        self.assertEqual(d['status'],'measured')
        self.assertIsNone(d['attributable_fees'])
        self.assertEqual(d['reserve_movement']['AAA']['net_reserve_delta'],'-1')
        self.assertEqual(d['reserve_movement']['AAA']['our_share_of_delta'],'-0.25')
    def test_zero_precision_token_exactness(self):
        before=self.snap(at=1,prec=0)
        after=self.snap(base='1005',quote='2004',at=2,prec=0)
        d=diff_fee_snapshots(before,after)
        self.assertEqual(d['status'],'measured')
        self.assertIsNone(d['attributable_fees'])
        # 5/4 at precision 0 rounds DOWN to '1'; 1 -> '1'
        self.assertEqual(d['reserve_movement']['AAA']['our_share_of_delta'],'1')
        self.assertEqual(d['reserve_movement']['BBB']['our_share_of_delta'],'1')
    def test_snapshot_only_status(self):
        s=self.snap(at=1)
        self.assertEqual(s.get('measurement_status'),'snapshot_recorded')

class CollectTests(unittest.TestCase):
    def test_rpc_failure_unavailable_no_fabrication(self):
        out=collect_fee_evidence(fake_rpc(fail=True),'alice',['AAA:BBB'],at_ms=1)
        e=out['AAA:BBB']
        self.assertEqual(e['status'],'unavailable')
        self.assertIn('reason',e)
        self.assertIsNone(e['share'])
    def test_collect_happy_path(self):
        out=collect_fee_evidence(fake_rpc(position=posr('10')),'alice',['AAA:BBB'],at_ms=9)
        e=out['AAA:BBB']
        self.assertEqual(e['account'],'alice')
        self.assertEqual(e['pool'],'AAA:BBB')
        self.assertEqual(e['measurement_status'],'snapshot_recorded')
        self.assertIsNotNone(e['snapshot'])
    def test_contract_hash_unverified(self):
        out=collect_fee_evidence(fake_rpc(position=posr('10')),'alice',['AAA:BBB'],
                                 at_ms=9,contract_hash='deadbeef'*8)
        self.assertEqual(out['AAA:BBB']['status'],'contract_unverified')
    def test_contract_hash_verified(self):
        good=next(iter(KNOWN_CONTRACT_HASHES))
        out=collect_fee_evidence(fake_rpc(position=posr('10')),'alice',['AAA:BBB'],
                                 at_ms=9,contract_hash=good)
        self.assertEqual(out['AAA:BBB']['status'],'ok')

class NoFloatLeakageTests(unittest.TestCase):
    def test_no_fractions_or_floats_in_strings(self):
        s=snapshot_fee_state(fake_rpc(position=posr('7')),'alice','AAA:BBB',at_ms=1)
        after=snapshot_fee_state(fake_rpc(pool=dict(BASE,baseQuantity='1003',quoteQuantity='2007'),
                                          position=posr('7')),'alice','AAA:BBB',at_ms=2)
        d=diff_fee_snapshots(s,after)
        def walk(v):
            if isinstance(v,float): self.fail(f'float leaked: {v!r}')
            if isinstance(v,dict):
                for x in v.values(): walk(x)
            if isinstance(v,list):
                for x in v: walk(x)
        walk(s); walk(d)
        # share components are digit strings
        self.assertTrue(
            d['reserve_movement']['AAA']['our_share_of_delta']
            .lstrip('-').replace('.','').isdigit())
        self.assertIsNone(d['attributable_fees'])

class FmtTests(unittest.TestCase):
    def test_fmt_amount_precision(self):
        self.assertEqual(_fmt_amount(Fraction(5,4),8),'1.25')
        self.assertEqual(_fmt_amount(Fraction(5,4),0),'1')
        self.assertEqual(_fmt_amount(Fraction(-5,4),8),'-1.25')
        self.assertEqual(_fmt_amount(Fraction(3,1),8),'3')

if __name__=='__main__': unittest.main()
