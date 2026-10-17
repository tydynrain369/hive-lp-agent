"""Synthetic fixtures and saved-live-input arithmetic checks; no earnings evidence."""
import json
import unittest
from pathlib import Path
from copy import deepcopy
from decimal import Decimal as D, getcontext
from fractions import Fraction as F
from lp_liquidation import value_rewards

PARAMS = {'tradeFeeMul': '0.9975'}
PREC = {'A': 8, 'B': 8, 'C': 8, 'X': 8}

def pool(pair, a='100', b='200'):
    return dict(tokenPair=pair, baseQuantity=a, quoteQuantity=b,
                totalShares='100', precision=8)

def rounded(x, places, half=False):
    n, d = x.numerator * 10**places, x.denominator
    q, r = divmod(n, d)
    return F(q + int(half and 2*r >= d), 10**places)

def reference(p, symbol, amount, precision):
    a, b = p['tokenPair'].split(':')
    ri, ro = (F(p['baseQuantity']), F(p['quoteQuantity'])) if symbol == a else (F(p['quoteQuantity']), F(p['baseQuantity']))
    raw = rounded(F(amount)*F('0.9975')*ro/(ri+F(amount)*F('0.9975')),20,True)
    out = rounded(raw,precision)
    return out, rounded(ri+F(amount),8,True), rounded(ro-raw,8,True)

class LiquidationTest(unittest.TestCase):
    def test_saved_live_single_hop_fraction(self):
        data=json.loads(Path('/home/tydynrain/hive-agent-test/reports/position-inputs-20260917T191558Z.json').read_text())
        p=next(p for p in data['pools'] if p['tokenPair']=='ALIVE:HEARTBEAT')
        prec={t['symbol']:t['precision'] for t in data['tokens']}
        before=deepcopy(p)
        result=value_rewards(p,[{'symbol':'ALIVE','amount':'1'}],{},prec,data['marketpools_params'],'HEARTBEAT')
        out,ri,ro=reference(p,'ALIVE','1',prec['HEARTBEAT'])
        self.assertEqual(F(result['total']),out)
        self.assertEqual(F(result['projected_pool']['baseQuantity']),ri)
        self.assertEqual(F(result['projected_pool']['quoteQuantity']),ro)
        self.assertEqual(p,before)

    def test_amount_specific_route_not_one_token_proxy(self):
        # Direct has the best small quote, but the deep two-hop wins for 100 A.
        p=pool('A:B','1','2')
        routes={'A:C':pool('A:C','1000','1000'),'C:B':pool('C:B','1000','1000')}
        r=value_rewards(p,[{'symbol':'A','amount':'100'}],routes,PREC,PARAMS,'B')
        self.assertGreater(D(r['total']),D('80'))

    def test_shared_reserves_and_canonical_order(self):
        p=pool('A:B'); routes={'C:A':pool('C:A','100','100')}
        rewards=[{'symbol':'C','amount':'10'},{'symbol':'A','amount':'10'}]
        before=deepcopy((p,routes,rewards))
        r=value_rewards(p,rewards,routes,PREC,PARAMS,'B')
        reversed_result=value_rewards(p,list(reversed(rewards)),routes,PREC,PARAMS,'B')
        self.assertEqual(r,reversed_result)
        first,ri,ro=reference(p,'A','10',8)
        via,_,_=reference(routes['C:A'],'C','10',8)
        second,_,_=reference(dict(p,baseQuantity=ri,quoteQuantity=ro), 'A',via,8)
        self.assertEqual(F(r['total']), first+second)
        self.assertEqual((p,routes,rewards),before)

    def test_unknown_not_zero_valued(self):
        r=value_rewards(pool('A:B'),[{'symbol':'X','amount':'1'},{'symbol':'B','amount':'2'}],{},PREC,PARAMS,'B')
        self.assertIsNone(r['total'])
        self.assertEqual(r['unroutable'],['X'])
        self.assertEqual(D(r['known_subtotal']),D(2))

    def test_negative_input_rejected(self):
        with self.assertRaises(ValueError):
            value_rewards(pool('A:B'),[{'symbol':'A','amount':'-1'}],{},PREC,PARAMS,'B')

    def test_no_decimal_context_mutation(self):
        import importlib, lp_liquidation
        old=getcontext().prec
        try:
            getcontext().prec=28
            importlib.reload(lp_liquidation)
            self.assertEqual(getcontext().prec,28)
        finally:
            getcontext().prec=old

if __name__=='__main__': unittest.main()
