"""Exercise actual cron main; public RPC fixture, forbidden side effects raise."""
import sys,unittest
from unittest.mock import patch
sys.path.insert(0,'/home/tydynrain/.hermes/scripts')
import cron_lp_agent as c
import liquidity_agent_v2 as m
class CronTests(unittest.TestCase):
    def test_main_never_initializes_executor_or_tracker(self):
        def rpc(method,p):
            if method=='getContract':return {'codeHash':'fixture'}
            return {'tokens':[{'symbol':'A','precision':8},{'symbol':'SWAP.HBD','precision':8}],
                'pools':[{'tokenPair':'A:SWAP.HBD','baseQuantity':'100','quoteQuantity':'200','totalShares':'100','precision':8}] if p['contract']=='marketpools' else [],
                'balances':[{'symbol':'A','balance':'0.001','stake':'100'},{'symbol':'SWAP.HBD','balance':'0.002','stake':'0'}],
                'liquidityPositions':[{'account':'hermes-sandbox','tokenPair':'A:SWAP.HBD','shares':'0.001','timeFactor':1000}],
                'params':[{'distTickHours':'24','tradeFeeMul':'0.9975'}], 'batches':[]}[p['table']]
        with patch.object(m,'he',side_effect=rpc),patch.object(c,'he',side_effect=rpc),\
             patch.object(m,'ExecutionEngine',side_effect=AssertionError('executor forbidden')),\
             patch.object(c,'LPTestTracker',side_effect=AssertionError('tracker forbidden')),\
             patch('builtins.open',side_effect=AssertionError('keys/files forbidden')):
            c.main()
if __name__=='__main__':unittest.main(verbosity=2)
