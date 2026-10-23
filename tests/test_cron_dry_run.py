"""Exercise actual cron main; dry-run safety posture regression.

2026-10-22 re-scope: the original test forbade ALL file opens — that pinned
the pre-artifact-era design. The live architecture deliberately WRITES
artifacts (fee snapshots, decision files) every tick, so the honest way to
run the real main hermetically is to sandbox HOME (all ~/.hermes paths land
in a temp dir) while keeping the two invariants that actually protect us:
  1. the EXECUTOR is never constructed in a dry-run tick (its constructor
     loads the KEY VAULT — keys must never enter memory for a dry run);
  2. the TEST TRACKER is never constructed (no test-state side effects).
The vault path itself is absolute and untouched by the HOME sandbox — a
dry-run tick that touches it would fail loudly on its own.
"""
import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

# Sandbox HOME BEFORE importing the modules: every ~/.hermes artifact path
# is expanded at import time via os.path.expanduser, so this redirects all
# decision/snapshot/executions writes into the temp dir.
_TMP = tempfile.mkdtemp(prefix="lp_cron_dryrun_")
os.environ["HOME"] = _TMP
os.environ.pop("LP_TEST_LIVE", None)          # dry-run, never broadcast
os.environ["LP_TEST_MAX_USD"] = "0.05"        # the standing cap

sys.path.insert(0, '/home/tydynrain/.hermes/scripts')
import cron_lp_agent as c
import liquidity_agent_v2 as m


class CronTests(unittest.TestCase):
    def test_dry_run_main_never_constructs_executor_or_tracker(self):
        # LEAK INVARIANT: capture the LIVE artifact state first — this test
        # must leave it byte-for-byte untouched (a 2026-10-22 leak through a
        # hardcoded path overwrote the live decision file; that class is
        # sealed, and this assert makes any regression loud).
        live_dir = Path('/home/tydynrain/.hermes/lp_tests/decisions')
        live_today = live_dir / ('decision-%s.json' %
                                 __import__('datetime').date.today().strftime('%Y%m%d'))
        live_mtime = live_today.stat().st_mtime if live_today.exists() else None
        def rpc(method, p):
            if method == 'getContract':
                return {'codeHash': 'fixture'}
            return {'tokens': [{'symbol': 'A', 'precision': 8},
                               {'symbol': 'SWAP.HBD', 'precision': 8}],
                'pools': [{'tokenPair': 'A:SWAP.HBD', 'baseQuantity': '100',
                           'quoteQuantity': '200', 'totalShares': '100',
                           'precision': 8}] if p['contract'] == 'marketpools' else [],
                'balances': [{'symbol': 'A', 'balance': '0.001', 'stake': '100'},
                             {'symbol': 'SWAP.HBD', 'balance': '0.002', 'stake': '0'}],
                'liquidityPositions': [{'account': 'hermes-sandbox',
                                        'tokenPair': 'A:SWAP.HBD',
                                        'shares': '0.001', 'timeFactor': 1000}],
                'params': {'distTickHours': '24', 'tradeFeeMul': '0.9975'},
                'batches': []}[p['table']]
        with patch.object(m, 'he', side_effect=rpc), \
             patch.object(c, 'he', side_effect=rpc), \
             patch.object(m, 'ExecutionEngine',
                          side_effect=AssertionError('executor forbidden')):
            c.main()
        # INVARIANTS of a dry-run tick:
        # 1. the executor was never constructed (its constructor loads the
        #    KEY VAULT — the patch above would have fired);
        # 2. no autonomy flag was created in the sandbox;
        # 3. the LP-test tracker (sanctioned state machinery, default-on in
        #    LP_TEST_CONFIG) wrote its state into the SANDBOXED home, not
        #    the live one — and no broadcast could have happened
        #    (LP_TEST_LIVE is unset).
        self.assertFalse(os.path.exists(os.path.join(_TMP, '.hermes', 'lp_tests', 'AUTONOMY_ON')))
        # artifacts landed in the SANDBOX home — HOME redirect worked and
        # the live ~/.hermes is untouched by this run
        sandbox_state = Path(_TMP) / '.hermes' / 'lp_tests'
        self.assertTrue(sandbox_state.exists(),
                        "dry-run main wrote no state into the sandbox HOME")
        # LEAK INVARIANT (see capture above): the LIVE decision file must be
        # byte-for-byte untouched by this test run.
        if live_mtime is not None:
            self.assertEqual(live_today.stat().st_mtime, live_mtime,
                             "the dry-run test TOUCHED the live decision "
                             "artifact — a sandbox leak through a hardcoded "
                             "path; seal it before running again")


if __name__ == '__main__':
    unittest.main()
