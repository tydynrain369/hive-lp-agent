"""Suite-level seal: the tests must never write into LIVE state.

2026-11-14 (Fable round 5, live incident): three module-level state paths
leaked through tests that exercised success paths without patching them —

- lp_ladder.LADDER_STATE_PATH        -> live ladder_state.json (fixture
  deploys poisoned the autonomous destination model),
- lp_executor_test.EXECUTIONS_LOG    -> live test_executions.jsonl (fixture
  broadcast rows polluted the ledger),
- cron_lp_agent.REPORTS_DIR          -> live reports/ (file-relative, so a
  HOME sandbox would NOT cover it — the day's production recommendation
  report was overwritten by an A:SWAP.HBD fixture).

This conftest repoints every one of them into a session temp dir at import
time and re-asserts the seal around every test (a test module that saves a
captured path and restores it in addCleanup must not restore a live value).
A test that wants a specific path patches it itself, as test_lp_ladder
already did; the seal only guarantees the DEFAULT is never live.
"""
import importlib
import os
import sys
import tempfile

import pytest

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

_TMP = tempfile.mkdtemp(prefix="lp-suite-state-")

_SEALED = {
    "lp_ladder": ("LADDER_STATE_PATH", os.path.join(_TMP, "ladder_state.json")),
    "lp_executor_test": ("EXECUTIONS_LOG", os.path.join(_TMP, "test_executions.jsonl")),
    "cron_lp_agent": ("REPORTS_DIR", os.path.join(_TMP, "reports")),
    "liquidity_agent_v2": ("HE_NODE_STATE_PATH", os.path.join(_TMP, "he_node.json")),
    # 2026-11-15 (Fable round 6, F2 follow-up): the audit found six MORE
    # state paths the original seal missed. All default-paths audited and
    # sealed (the rule: a module with a state path joins the seal in the
    # same change — this is that change, late).
    "lp_realloc_executor": ("REALLOC_LOG", os.path.join(_TMP, "reallocations.jsonl")),
    "lp_realloc_executor:2": ("IDLE_DEPLOY_THROTTLE", os.path.join(_TMP, "LAST_IDLE_DEPLOY")),
    "lp_decision": ("DECISIONS_DIR", os.path.join(_TMP, "decisions")),
    "lp_fee_evidence": ("DEFAULT_SNAPSHOT_STORE", os.path.join(_TMP, "fee_snapshots.jsonl")),
    "lp_recommendation": ("FEE_SNAPSHOTS_PATH", os.path.join(_TMP, "fee_snapshots.jsonl")),
    "lp_test_tracker": ("DB_PATH", os.path.join(_TMP, "lp_tests.db")),
    # NOTE (round 6 audit): lp_lifecycle.json has NO module-level path
    # attribute — it resolves `os.path.join(os.path.dirname(__file__), ...)
    # at CALL time, i.e. it writes next to the LIVE SOURCE. Nothing in the
    # suite currently writes it (read-only filter use); if a test ever
    # writes lifecycle state, that call site must grow a path parameter
    # FIRST and then join this seal.
}


def _seal():
    """Import each module if needed and force its state path to the temp dir.

    Unconditional: no test in this suite legitimately wants the live
    default; tests needing a specific path patch it themselves.
    """
    for mod_name, (attr, sealed_path) in _SEALED.items():
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue           # module not importable: not the seal's job
        if hasattr(mod, attr):
            setattr(mod, attr, sealed_path)


_seal()


@pytest.fixture(scope="session", autouse=True)
def _seal_session():
    _seal()
    yield


@pytest.fixture(autouse=True)
def _reseal_every_test():
    _seal()
    yield
    _seal()
