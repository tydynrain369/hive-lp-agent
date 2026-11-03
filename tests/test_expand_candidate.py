"""Regression tests for the LP decision engine.

RESTORED 2026-10-02 (second deletion). This file has been removed from tests/
more than once while sibling test files survived. tests/restore_tests.sh heals
it from the git repo at ~/hive-agent-test — the versioned mirror, which sits
outside the execute_code kernel's working directory. (The old
scripts/tests_canonical/ copy was retired 2026-11-03: it duplicated tests/
byte-for-byte and broke a bare `pytest` run with an import mismatch.) If this
vanishes again, run restore_tests.sh and treat the deletion as a live bug to
chase, not housekeeping.

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest discover \
      -s tests -p 'test_*.py' -v
"NO TESTS RAN" means the tests are GONE, not passing.
"""
import sys
import unittest

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import lp_decision as L


HELD = ["ALIVE:HEARTBEAT", "ALIVE:SWAP.HIVE", "CENT:POB", "HSBIDAO:PIXYDUST"]

FEE = {
    "ALIVE:HEARTBEAT":  {"fee_apr_pct": 1.204},
    "ALIVE:SWAP.HIVE":  {"fee_apr_pct": 4.88},
    "CENT:POB":         {"fee_apr_pct": 5.133},
    "HSBIDAO:PIXYDUST": {"fee_apr_pct": 0.036},
}
REWARDS = {"ALIVE:HEARTBEAT": {"count": 11}, "ALIVE:SWAP.HIVE": {"count": 20},
           "CENT:POB": {"count": 1}, "HSBIDAO:PIXYDUST": {"count": 13}}
ECON = {
    "ALIVE:HEARTBEAT":  {"our_position_value_usd": 0.00012},
    "ALIVE:SWAP.HIVE":  {"our_position_value_usd": 0.00008},
    "CENT:POB":         {"our_position_value_usd": 0.00102},
    "HSBIDAO:PIXYDUST": {"our_position_value_usd": 0.00022},
}
THRESH = {
    "ALIVE:HEARTBEAT":  {"computable": True, "multiple_to_1e6_fee_threshold": 18.9},
    "ALIVE:SWAP.HIVE":  {"computable": True, "multiple_to_1e6_fee_threshold": 6.5},
    "CENT:POB":         {"computable": True, "multiple_to_1e6_fee_threshold": 0.6},
    "HSBIDAO:PIXYDUST": {"computable": True, "multiple_to_1e6_fee_threshold": 312.6},
}
OVERRIDE = {
    "ALIVE:HEARTBEAT":  {"fee_apr_pct_authoritative": 1.204},
    "ALIVE:SWAP.HIVE":  {"fee_apr_pct_authoritative": 4.88},
    "CENT:POB":         {"fee_apr_pct_authoritative": 5.133},
    "HSBIDAO:PIXYDUST": {"fee_apr_pct_authoritative": 0.036},
}


def run(override=None):
    return L.decide(HELD, REWARDS, fee_evidence=FEE,
                    position_economics=ECON, threshold_analysis=THRESH,
                    fee_evidence_override=override)


class TestExpandCandidateFromDustVerdict(unittest.TestCase):
    def setUp(self):
        self.r = run(override=OVERRIDE)

    def test_dust_expand_pools_are_registered(self):
        expands = [p for p, v in self.r.verdicts.items() if v.action == "expand"]
        self.assertTrue(expands)
        self.assertIsNotNone(self.r.expand_candidate,
                             f"{len(expands)} expand pool(s) but no destination")

    def test_best_apr_expand_wins(self):
        expands = {p: v.fee_apr_pct for p, v in self.r.verdicts.items()
                   if v.action == "expand"}
        if expands:
            self.assertEqual(self.r.expand_candidate, max(expands, key=expands.get))

    def test_reallocation_names_a_destination(self):
        consolidates = [p for p, v in self.r.verdicts.items()
                        if v.action in ("consolidate", "exit")]
        if consolidates and self.r.expand_candidate:
            self.assertIsNotNone(self.r.recommended_reallocation)
            self.assertEqual(self.r.recommended_reallocation["to_pool"],
                             self.r.expand_candidate)

    def test_no_false_optimal_claim(self):
        consolidates = [p for p, v in self.r.verdicts.items()
                        if v.action in ("consolidate", "exit")]
        if consolidates:
            self.assertNotIn("near-optimal", " ".join(self.r.notes).lower())

    def test_source_is_never_the_destination(self):
        r = self.r.recommended_reallocation
        if r:
            self.assertNotEqual(r["from_pool"], r["to_pool"])


class TestDivergentFallbackIsNeverTrusted(unittest.TestCase):
    def test_missing_authoritative_apr_is_flagged(self):
        r = run(override=None)
        self.assertIn("UNKNOWN", " ".join(r.notes))

    def test_fallback_alone_does_not_produce_a_zombie_exit(self):
        r = run(override=None)
        for pool, v in r.verdicts.items():
            self.assertNotEqual(v.action, "exit",
                                f"{pool} exited using the known-divergent fallback APR")

    def test_authoritative_apr_is_preferred_when_present(self):
        r = run(override=OVERRIDE)
        self.assertNotIn("UNKNOWN", " ".join(r.notes))


class TestDustVerdictInvariants(unittest.TestCase):
    def test_dust_verdict_never_invents_multiple(self):
        self.assertEqual(L.dust_verdict({"computable": False}, 0.001, 1.0), (None, None))
        self.assertEqual(L.dust_verdict({"computable": True}, 0.001, 1.0), (None, None))

    def test_scoring_weights_are_named_and_positive(self):
        self.assertGreater(L.FEE_APR_SCORE_WEIGHT, 0)
        self.assertGreater(L.REWARD_TOKEN_SCORE_WEIGHT, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)