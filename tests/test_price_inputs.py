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
            # 2026-11-15 (round 7, JOB B): the precision loader now batches
            # with a $in query — answer it with the fixture rows. ALIVE keeps
            # precision 2 here (the original 2026-10-22 point: a chain-
            # reported precision must beat the defaults dict), alongside A.
            q = params.get('query') or {}
            if isinstance(q.get('symbol'), dict):
                asked = set(q['symbol'].get('$in', []))
                return [{'symbol': s, 'precision': 2}
                        for s in ('A', 'ALIVE') if s in asked]
            return rows if params['table']=='pools' else [{'symbol':'A','precision':2}]
        with patch.object(m,'he',side_effect=rpc): pe.load_all()
        return pe
    def test_hive_price_from_pool_and_derived_majors(self):
        # 2026-10-22 re-pin: the anchor architecture is deliberate (the
        # 2026-10-17 units fix builds on it). With the HIVE:HBD pool
        # present, HIVE is POOL-derived. 2026-11-13 (Opus P0-10) UPDATE:
        # the wrapped-major SEED anchors are GONE — a stale constant
        # (BTC at $65k) is how a pricing gap becomes a wrong number.
        # Majors now price only from real pool routes: the fixture's
        # SWAP.BTC:SWAP.HIVE pool derives BTC as 200 HIVE = $10.
        pe=self.engine([pool('SWAP.HIVE:SWAP.HBD',100,5),pool('SWAP.BTC:SWAP.HIVE',1,200)])
        self.assertAlmostEqual(pe.token_prices['SWAP.HIVE'],0.05)
        self.assertAlmostEqual(pe.token_prices['SWAP.BTC'],10.0)
    def test_missing_anchor_refuses_to_fabricate(self):
        # 2026-11-13 (Opus P0-10) re-pin: no anchor pool -> HIVE is UNKNOWN.
        # The old $0.05 HIVE fallback and the whole stale major-seed set are
        # GONE: only the HBD peg ($1.00, a real peg, not a guess) remains,
        # and every other token prices from real pool routes or stays
        # unpriced. A fabricated anchor is how a pricing gap becomes a
        # wrong number; unknown is how it becomes a refusal.
        prices=self.engine([]).token_prices
        self.assertNotIn('SWAP.HIVE', prices)
        self.assertNotIn('HIVE', prices)
        self.assertNotIn('SWAP.BTC', prices)
        self.assertEqual(set(prices),{'SWAP.HBD','HBD'})
    def test_order_independent_deeper_known_side(self):
        rows=[pool('SWAP.HIVE:SWAP.HBD',100,5),pool('A:SWAP.HBD',100,1),pool('A:SWAP.HBD2',1,9),pool('SWAP.HBD2:SWAP.HBD',100,100)]
        a=self.engine(rows).token_prices; b=self.engine(list(reversed(rows))).token_prices
        self.assertEqual(a,b)
    def test_floor_never_overspends(self):
        # 'ALIVE' is in the per-symbol precision loader's query list; the
        # fixture rpc returns precision 2 for it. 'A' is NOT queried by the
        # loader and the old default-8 fallback masked that (2026-10-22).
        pe=self.engine([])
        self.assertEqual(pe.round_amount('ALIVE',1.239),1.23)
    def test_unknown_precision_not_invented(self):
        with self.assertRaises(ValueError): self.engine([]).round_amount('MISSING',1.0)
    def test_unknown_fee_has_explicit_status(self):
        p=self.engine([pool('A:SWAP.HBD',100,1)]).pools['A:SWAP.HBD']
        self.assertEqual(p.fee_observation_status,'unknown')

if __name__=='__main__': unittest.main(verbosity=2)
