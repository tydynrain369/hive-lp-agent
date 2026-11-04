"""Tree-level collection guard (2026-11-03).

Only tests/ is a suite; everything else is data. archive/ holds retired
modules containing *module-level live code* — archive/test_cycle.py ran a full
strategy cycle on import, and a bare `pytest` from the tree root collected it
and broadcast real market buys and add_liquidity calls (unlogged: the ledger
path is HOME-expanded and test runs sandbox HOME). pytest must never import
anything from those directories.
"""
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
_NO_COLLECT = ("archive", "tests_canonical")


def pytest_ignore_collect(collection_path, config):
    try:
        p = os.path.abspath(str(collection_path))
    except Exception:
        return False
    for d in _NO_COLLECT:
        bd = os.path.join(_ROOT, d)
        if p == bd or p.startswith(bd + os.sep):
            return True
    return False
