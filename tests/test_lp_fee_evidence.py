import sys,unittest
from fractions import Fraction
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
from lp_fee_evidence import (snapshot_fee_state,diff_fee_snapshots,
                             collect_fee_evidence,KNOWN_CONTRACT_HASHES,
                             _fmt_amount,rotate_fee_snapshots,
                             record_fee_snapshots)

BASE={'tokenPair':'AAA:BBB','baseSymbol':'AAA','quoteSymbol':'BBB',
      'baseQuantity':'1000','quoteQuantity':'2000','totalShares':'100',
      'baseVolume':'50','quoteVolume':'80','precision':8}

# 2026-11-15 (Fable round 6): a None contract hash is FAIL-CLOSED
# ('unverified-no-codehash') — mechanics tests present the known-good hash.
GOOD_HASH = next(iter(KNOWN_CONTRACT_HASHES))

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
    # 2026-11-15 (Fable round 6): a None contract hash is FAIL-CLOSED now
    # ('unverified-no-codehash') — mechanics tests present the known-good
    # hash, as the live cron does on a successful contract read.
    GOOD_HASH = next(iter(KNOWN_CONTRACT_HASHES))

    def test_rpc_failure_unavailable_no_fabrication(self):
        out=collect_fee_evidence(fake_rpc(fail=True),'alice',['AAA:BBB'],at_ms=1,
                                 contract_hash=self.GOOD_HASH)
        e=out['AAA:BBB']
        self.assertEqual(e['status'],'unavailable')
        self.assertIn('reason',e)
        self.assertIsNone(e['share'])
    def test_collect_happy_path(self):
        out=collect_fee_evidence(fake_rpc(position=posr('10')),'alice',['AAA:BBB'],at_ms=9,
                                 contract_hash=self.GOOD_HASH)
        e=out['AAA:BBB']
        self.assertEqual(e['account'],'alice')
        self.assertEqual(e['pool'],'AAA:BBB')
        self.assertEqual(e['measurement_status'],'snapshot_recorded')
        self.assertIsNotNone(e['snapshot'])
    def test_missing_contract_hash_fails_closed(self):
        """Round 6: the old None -> 'ok' was fail-open (a findOne null read
        as a verified contract). Missing hash = unverified = snapshots skip."""
        out=collect_fee_evidence(fake_rpc(position=posr('10')),'alice',['AAA:BBB'],
                                 at_ms=9,contract_hash=None)
        self.assertEqual(out['AAA:BBB']['status'],'unverified-no-codehash')
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

class RotationTests(unittest.TestCase):
    """2026-11-05: the snapshot store is append-only and had reached 15.4 MB /
    24,709 rows, because `record_fee_snapshots` re-read the WHOLE file once PER
    PAIR. Rotation must therefore (a) preserve diff semantics exactly and
    (b) never lose the newest prior row.

    The reader consults only the MOST RECENT prior row for a (pool, account)
    pair, so keeping the last N per pair is safe. A flat time-based cut is NOT:
    it can drop the newest prior row and silently downgrade attribution from
    'measured' to 'snapshot_recorded' (unmeasured), with no error anywhere.
    """
    def _write(self, rows):
        import tempfile, os, json
        d=tempfile.mkdtemp(); p=os.path.join(d,'fee_snapshots.jsonl')
        with open(p,'w') as f:
            for r in rows: f.write(json.dumps(r)+'\n')
        self.addCleanup(lambda: os.remove(p) if os.path.exists(p) else None)
        return p
    def _row(self, pool, acct, at, base='1000'):
        pool_rec=dict(BASE, baseQuantity=base, totalShares='100')
        snap=snapshot_fee_state(fake_rpc(pool=pool_rec,position=posr('25')),acct,pool,at_ms=at)
        return {'pool':pool,'account':acct,'at_ms':at,'snapshot':snap}
    def _load(self, p):
        import json
        return [json.loads(l) for l in open(p) if l.strip()]

    def test_keeps_last_n_per_pair_and_is_atomic(self):
        import os
        p=self._write([self._row('AAA:BBB','alice',i) for i in range(1,11)])
        rep=rotate_fee_snapshots(store_path=p,keep_per_pair=3)
        self.assertEqual(rep['rows_before'],10)
        self.assertEqual(rep['rows_after'],3)
        self.assertEqual(rep['rows_dropped'],7)
        self.assertEqual(rep['pairs'],1)
        self.assertEqual([r['at_ms'] for r in self._load(p)],[8,9,10],
                         'exactly the LAST three rows survive, in order')
        self.assertFalse(os.path.exists(p+'.rotating'),
                         'atomic replace leaves no temp file behind')

    def test_per_pair_not_by_timestamp(self):
        """A pair with sparse history keeps its own newest rows even when a
        busy pair spans a much longer window."""
        rows=[self._row('BUSY:X','alice',i) for i in range(1,101)]
        rows+=[self._row('QUIET:Y','alice',1),self._row('QUIET:Y','alice',2)]
        p=self._write(rows)
        rotate_fee_snapshots(store_path=p,keep_per_pair=5)
        kept=self._load(p)
        quiet=[r for r in kept if r['pool']=='QUIET:Y']
        busy=[r for r in kept if r['pool']=='BUSY:X']
        self.assertEqual(len(quiet),2,'a sparse pair keeps ALL its rows')
        self.assertEqual(len(busy),5,'a busy pair is trimmed to the last N')

    def test_noop_when_under_keep(self):
        p=self._write([self._row('AAA:BBB','alice',1)])
        rep=rotate_fee_snapshots(store_path=p,keep_per_pair=720)
        self.assertEqual(rep['rows_dropped'],0)
        self.assertEqual(rep['rows_after'],1)

    def test_dry_run_never_writes(self):
        p=self._write([self._row('AAA:BBB','alice',i) for i in range(1,6)])
        rep=rotate_fee_snapshots(store_path=p,keep_per_pair=1,dry_run=True)
        self.assertTrue(rep['dry_run'])
        self.assertEqual(rep['rows_dropped'],4)
        self.assertEqual(len(self._load(p)),5,'dry run must not touch the store')

    def test_corrupt_lines_skipped_never_fabricated(self):
        import json
        p=self._write([self._row('AAA:BBB','alice',i) for i in range(1,4)])
        with open(p,'a') as f: f.write('{not json at all\n\n')
        rep=rotate_fee_snapshots(store_path=p,keep_per_pair=2)
        self.assertEqual(rep['unparsable_skipped'],1)
        self.assertEqual(rep['rows_before'],3,'a corrupt line is not a row')
        kept=self._load(p)
        self.assertEqual([r['at_ms'] for r in kept],[2,3])

    def test_record_after_rotation_still_measures(self):
        """The whole point: rotation must not break the diff. After trimming to
        the last row, the NEXT record still diffs against it -> 'measured'."""
        p=self._write([self._row('AAA:BBB','alice',i) for i in range(1,11)])
        rotate_fee_snapshots(store_path=p,keep_per_pair=1)
        out=record_fee_snapshots(fake_rpc(pool=dict(BASE,baseQuantity='1100',
                                                    quoteQuantity='2100'),
                                          position=posr('25')),
                                 'alice',['AAA:BBB'],store_path=p,at_ms=99,
                                 contract_hash=GOOD_HASH)
        e=out['results']['AAA:BBB']
        self.assertEqual(e['status'],'measured',
                         'the retained prior row must still be found')
        self.assertEqual(e['diff']['status'],'measured')

    def test_record_reads_the_store_once_per_call(self):
        """One read per call, not one read per PAIR (the O(pairs x rows) bug)."""
        import lp_fee_evidence as F
        reads=[]
        p=self._write([self._row('AAA:BBB','alice',1)])
        real=F._read_snapshot_rows
        def counting(store):
            reads.append(str(store)); return real(store)
        F._read_snapshot_rows=counting
        try:
            record_fee_snapshots(fake_rpc(position=posr('25')),'alice',
                                 ['AAA:BBB','CCC:DDD','EEE:FFF'],
                                 store_path=p,at_ms=2,
                                 contract_hash=GOOD_HASH)
        finally:
            F._read_snapshot_rows=real
        self.assertEqual(len(reads),1,
                         f'expected ONE store read per call, got {len(reads)}')

    def test_unparsable_count_is_reported_on_record(self):
        p=self._write([self._row('AAA:BBB','alice',1)])
        with open(p,'a') as f: f.write('garbage\n')
        out=record_fee_snapshots(fake_rpc(position=posr('25')),'alice',
                                 ['AAA:BBB'],store_path=p,at_ms=2,
                                 contract_hash=GOOD_HASH)
        self.assertEqual(out.get('unparsable_lines_skipped'),1)

if __name__=='__main__': unittest.main()
