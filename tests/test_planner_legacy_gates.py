"""Synthetic gate regression tests. No RPC, keys, imports of the trading agent, or broadcasts."""
import ast
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path('/home/tydynrain/.hermes/scripts/cron_lp_agent.py')

def load_planner():
    tree = ast.parse(SOURCE.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'legacy_manage_positions')
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope['legacy_manage_positions']

class LegacyGateTests(unittest.TestCase):
    def run_case(self, position_value=0.25, balance_value=0.001, diagnostics=True):
        position = NS(pool_id='HELD:QUOTE', shares=1, value_usd=position_value)
        pool = NS(base='NEW', quote='QUOTE', liquidity_usd=10, volume_24h=0, apr=0)
        agent = NS(
            portfolio=NS(positions={'HELD:QUOTE': position}, balances={
                'NEW': NS(balance=0.1, value_usd=balance_value),
                'QUOTE': NS(balance=0.1, value_usd=balance_value)}),
            pe=NS(pools={'NEW:QUOTE': pool}, token_prices={'REWARD': 1}))
        rewards = {'NEW:QUOTE': [{'mined_token':'REWARD', 'daily_reward':1}]}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            actions = load_planner()(agent, rewards, diagnostics=diagnostics)
        return actions, output.getvalue()

    def test_small_position_and_entry_rejected(self):
        actions, text = self.run_case()
        self.assertEqual(actions, [])
        self.assertIn('HELD:QUOTE: ordinary exit assessment skipped', text)
        self.assertIn('NEW:QUOTE: entry rejected', text)
        self.assertIn('[DRY-RUN]', text)

    def test_exact_one_dollar_exit_excluded(self):
        actions, text = self.run_case(position_value=1)
        self.assertFalse(any(a['type']=='exit' for a in actions))
        self.assertIn('ordinary exit assessment skipped', text)

    def test_above_one_dollar_exit_included(self):
        actions, _ = self.run_case(position_value=1.0001)
        self.assertEqual(actions[0]['reason'], 'no_active_rewards')

    def test_entry_at_one_dollar_included(self):
        actions, _ = self.run_case(balance_value=0.5)
        self.assertEqual([a['type'] for a in actions], ['enter'])

    def test_diagnostics_do_not_change_decisions(self):
        actions_on, _ = self.run_case()
        actions_off, text = self.run_case(diagnostics=False)
        self.assertEqual(actions_on, actions_off)
        self.assertNotIn('[GATE]', text)
        self.assertNotIn('[INPUT', text)

if __name__ == '__main__':
    unittest.main(verbosity=2)
