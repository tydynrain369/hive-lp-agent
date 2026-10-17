"""Saved-public snapshot and synthetic precision regressions; no broadcasts."""
import json
import unittest
from pathlib import Path
from fractions import Fraction as F
from copy import deepcopy
from lp_mechanics import swap_exact_in, best_route, QuoteError
from test_lp_liquidation import pool, rounded

class PrecisionRoutes(unittest.TestCase):
    def test_saved_lolz_direct_and_best_routes(self):
        data = json.loads(Path('/home/tydynrain/hive-agent-test/reports/position-inputs-20260917T211934Z.json').read_text())
        graph = {p['tokenPair']: p for p in data['pools']}
        prec = {t['symbol']: t['precision'] for t in data['tokens']}
        before = deepcopy(graph)
        qty = '0.0000061'
        for pair, sym in [('MEME:LOLZ', 'LOLZ'), ('SWAP.HIVE:MEME', 'MEME')]:
            p = graph[pair]; a, b = pair.split(':')
            ri, ro = (F(p['baseQuantity']), F(p['quoteQuantity']))
            if sym == b: ri, ro = ro, ri
            dest = b if sym == a else a
            raw = rounded(F(qty)*F(data['marketpools_params']['tradeFeeMul'])*ro / (ri+F(qty)*F(data['marketpools_params']['tradeFeeMul'])), 20, True)
            q = swap_exact_in(p, sym, qty, prec, data['marketpools_params'])
            self.assertEqual(F(q['amount_out']), rounded(raw, prec[dest]))
            qty = q['amount_out']
        self.assertEqual(F(qty), F('0.00000003'))
        best = best_route(graph, 'LOLZ', 'SWAP.HIVE', '0.0000061', prec, data['marketpools_params'])
        self.assertGreaterEqual(F(best['amount_out']), F(qty))
        self.assertEqual(prec['MEME'], 4)
        self.assertEqual(prec['STARBITS'], 0)
        self.assertEqual(graph, before)

    def test_output_precision_zero_through_eight(self):
        for places in range(9):
            with self.subTest(places=places):
                p = pool('A:B', '100', '100')
                q = swap_exact_in(p, 'A', '2', {'A':8,'B':places}, {'tradeFeeMul':'0.9975'})
                raw = rounded(F(2)*F('0.9975')*100/(100+2*F('0.9975')),20,True)
                self.assertEqual(F(q['amount_out']), rounded(raw, places))

    def test_integer_intermediate_zero_is_not_missing_pool(self):
        graph = {p: pool(p, '100', '100') for p in ['A:STARBITS','STARBITS:B']}
        prec = {'A':8,'STARBITS':0,'B':8}
        with self.assertRaisesRegex(QuoteError, 'no nonzero executable path'):
            best_route(graph,'A','B','0.1',prec,{'tradeFeeMul':'0.9975'})
        q = best_route(graph,'A','B','2',prec,{'tradeFeeMul':'0.9975'})
        self.assertEqual(q['legs'][0]['amount_out'],'1')
        with self.assertRaisesRegex(QuoteError, 'excess-precision'):
            swap_exact_in(graph['STARBITS:B'],'STARBITS','0.5',prec,{'tradeFeeMul':'0.9975'})

if __name__ == '__main__': unittest.main()
