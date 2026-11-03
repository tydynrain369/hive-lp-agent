"""Stage A — scale-generic property tests for the LP decision engine.

PURPOSE
  Prove decide() behaves correctly across the FULL scale range — dust
  ($1e-8..$1e-3), threshold crossings, and normal sizes ($0.01..$100) — and
  under BOTH readings of the unresolved "expand" semantics question:

    READING A: every viable candidate is 'expand'; expand_candidate names
               the best one.
    READING B: only the best candidate is 'expand'; the rest are 'hold'.

  Running the same invariants under both shows exactly which properties each
  reading breaks, so the policy choice is made from evidence, not intuition.

NO CAPITAL. NO CHAIN. Pure function tests against lp_decision.decide().

Run:
  /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
      tests.test_scale_properties -v
"""
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import lp_decision as L

# Thresholds in the engine
MIN_FEE = L.MIN_FEE_APR_FOR_PRODUCTIVE
MIN_REW = L.MIN_REWARD_TOKENS_FOR_PRODUCTIVE


def mk(pool, apr, rewards, value_usd, mult=None):
    """Build one pool's evidence. mult=None means 'not computable'."""
    fee = {"fee_apr_pct": apr, "evidence_window_hours": 100.0, "snapshot_count": 10}
    if mult is not None:
        thr = {"computable": True, "multiple_to_1e6_fee_threshold": mult}
    else:
        thr = {"computable": True}          # no multiple key -> not computable
    return {
        "pool": pool,
        "fee": fee,
        "rewards": {"count": rewards},
        "econ": {"our_position_value_usd": value_usd},
        "thr": thr,
    }


def run(cases, reading):
    """Apply one policy reading and return the DecisionReport."""
    held = [c["pool"] for c in cases]
    fee = {c["pool"]: c["fee"] for c in cases}
    rew = {c["pool"]: c["rewards"] for c in cases}
    eco = {c["pool"]: c["econ"] for c in cases}
    thr = {c["pool"]: c["thr"] for c in cases}
    ovr = {c["pool"]: {"fee_apr_pct_authoritative": c["fee"]["fee_apr_pct"]}
           for c in cases}
    r = L.decide(held, rew, fee_evidence=fee, position_economics=eco,
                 threshold_analysis=thr, fee_evidence_override=ovr)
    if reading == "B":
        # Reading B: demote every non-winning 'expand' to 'hold'.
        winner = r.expand_candidate
        for pool, v in r.verdicts.items():
            if v.action == "expand" and pool != winner:
                v.action = "hold"
    return r


class ScaleMatrix:
    """Fixture families spanning dust, threshold crossings, and scale."""

    DUST = [
        mk("P:DUST_SUB", 5.0, 10, 0.00000002, mult=40.0),      # 2e-8, below visibility
        mk("P:DUST_MID", 5.0, 10, 0.00012, mult=6.5),         # 1.2e-4
        mk("P:NEAR_THRESHOLD", 5.0, 10, 0.00102, mult=0.6),  # at threshold
        mk("P:ZOMBIE_DUST", 0.001, 0, 0.00003, mult=900.0),   # unviable
    ]
    SCALE = [
        mk("Q:SIZE_1CENT", 4.0, 5, 0.01, mult=0.1),
        mk("Q:SIZE_1DOLLAR", 4.0, 5, 1.00, mult=0.001),
        mk("Q:SIZE_100", 4.0, 5, 100.00, mult=0.00001),
    ]
    MIXED = DUST + SCALE
    # Reward-only and fee-only productive pools, plus a true tie.
    EDGE = [
        mk("R:REWARD_ONLY", 0.001, 12, 0.0005, mult=20.0),   # no fees, real rewards
        mk("R:FEE_ONLY", 3.0, 0, 0.004, mult=1.0),          # fees, no rewards
        mk("R:ZOMBIE", 0.0, 0, 0.0009, mult=None),          # nothing at all
    ]
    TIE = [
        mk("T:A", 5.0, 10, 0.0005, mult=2.0),
        mk("T:B", 5.0, 10, 0.0005, mult=2.0),               # identical
    ]


class Invariants(unittest.TestCase):
    """Properties that must hold at EVERY scale, under BOTH readings."""

    READINGS = ("A", "B")
    FAMILIES = {
        "dust": ScaleMatrix.DUST,
        "scale": ScaleMatrix.SCALE,
        "mixed": ScaleMatrix.MIXED,
        "edge": ScaleMatrix.EDGE,
        "tie": ScaleMatrix.TIE,
    }

    def _all(self):
        for fam, cases in self.FAMILIES.items():
            for reading in self.READINGS:
                yield fam, reading, cases, run(cases, reading)

    # --- P1: every pool gets a verdict, and it is a known action --------
    def test_p1_every_pool_has_a_known_action(self):
        for fam, reading, cases, r in self._all():
            for c in cases:
                with self.subTest(family=fam, reading=reading, pool=c["pool"]):
                    self.assertIn(c["pool"], r.verdicts)
                    self.assertIn(r.verdicts[c["pool"]].action,
                                  ("exit", "consolidate", "hold", "expand"))

    # --- P2: an already-earning position is never told to consolidate ---
    def test_p2_above_threshold_never_consolidate(self):
        for fam, reading, cases, r in self._all():
            for c in cases:
                th = c["thr"]
                if th.get("already_above_threshold"):
                    with self.subTest(family=fam, reading=reading, pool=c["pool"]):
                        self.assertNotEqual(r.verdicts[c["pool"]].action, "consolidate")

    # --- P3: consolidate REQUIRES a computable multiple ------------------
    def test_p3_consolidate_requires_computable_multiple(self):
        for fam, reading, cases, r in self._all():
            for c in cases:
                if r.verdicts[c["pool"]].action == "consolidate":
                    with self.subTest(family=fam, reading=reading, pool=c["pool"]):
                        self.assertIsNotNone(
                            c["thr"].get("multiple_to_1e6_fee_threshold"),
                            "consolidate issued without a computable multiple")

    # --- P4: the fallback may not flip a PRODUCTIVE pool to exit --------
    # NOTE 2026-10-02: this invariant was originally written as a blanket
    # "never exit without an authoritative APR". That was WRONG and the test
    # correctly failed on a true zero-fee/zero-reward pool.
    #
    # A pool with 0.0 fees AND 0 rewards IS a zombie, and that classification
    # needs only the SIGN of the APR to be right, not its magnitude. The
    # defect hardened against on 2026-10-02 was different in kind: the
    # fallback read 0.003% where truth was 1.18% — an order flip that turned a
    # productive pool into an exit candidate.
    #
    # So the invariant is: a pool with measurable-but-overstated-low fee yield
    # must never be exited without an authoritative APR. A genuinely-zero pool
    # is exempt.
    def test_p4_small_fallback_must_not_flip_a_productive_pool(self):
        for fam, cases in self.FAMILIES.items():
            held = [c["pool"] for c in cases]
            fee = {c["pool"]: c["fee"] for c in cases}
            rew = {c["pool"]: c["rewards"] for c in cases}
            eco = {c["pool"]: c["econ"] for c in cases}
            thr = {c["pool"]: c["thr"] for c in cases}
            r = L.decide(held, rew, fee_evidence=fee, position_economics=eco,
                         threshold_analysis=thr)      # NO override on purpose
            for pool, v in r.verdicts.items():
                c = next((x for x in cases if x["pool"] == pool), None)
                if c is None:
                    continue
                # Exempt: the fallback is genuinely ZERO and there are no
                # rewards. Sign-only classification; correct to exit.
                if c["fee"]["fee_apr_pct"] == 0.0 and c["rewards"]["count"] == 0:
                    continue
                with self.subTest(family=fam, pool=pool):
                    self.assertNotEqual(
                        v.action, "exit",
                        f"{pool} (fallback {c['fee']['fee_apr_pct']}%) exited "
                        f"without an authoritative APR — a merely-small fallback "
                        f"must never flip a pool to exit")

    # --- P5: a reallocation never names the same pool twice --------------
    def test_p5_source_is_never_destination(self):
        for fam, reading, cases, r in self._all():
            rec = r.recommended_reallocation
            if rec:
                with self.subTest(family=fam, reading=reading):
                    self.assertNotEqual(rec["from_pool"], rec["to_pool"])

    # --- P6: no near-optimal claim while a release is pending -------------
    def test_p6_no_false_optimal_claim(self):
        for fam, reading, cases, r in self._all():
            pending = [p for p, v in r.verdicts.items()
                       if v.action in ("consolidate", "exit")]
            if pending:
                with self.subTest(family=fam, reading=reading):
                    self.assertNotIn("near-optimal", " ".join(r.notes).lower())

    # --- P7: the winner is the highest VIABILITY-WEIGHTED score ----------
    # CHANGED 2026-10-02. This previously asserted the winner had the highest
    # RAW combined score. That rule is exactly what the dust fixture proved
    # wrong: a high-rate pool 40x from clearing must lose to a lower-rate pool
    # that can actually start earning. The assertion now applies the same
    # viability factor the engine uses, so it tracks the real rule instead of
    # pinning a superseded one.
    def test_p7_expand_candidate_is_highest_weighted_score(self):
        for fam, reading, cases, r in self._all():
            if not r.expand_candidate:
                continue
            scored = {}
            for c in cases:
                v = r.verdicts.get(c["pool"])
                if v is None:
                    continue
                scored[c["pool"]] = ((v.fee_apr_pct * L.FEE_APR_SCORE_WEIGHT
                                      + v.reward_token_count * L.REWARD_TOKEN_SCORE_WEIGHT)
                                     * L._viability_factor(c["thr"]))
            if scored:
                with self.subTest(family=fam, reading=reading):
                    self.assertEqual(r.expand_candidate,
                                     max(scored, key=scored.get))

    # --- P8: reward-only productive pools are never discarded -------------
    def test_p8_reward_only_pool_is_never_exited(self):
        for fam, reading, cases, r in self._all():
            for c in cases:
                if c["rewards"]["count"] >= MIN_REW and c["fee"]["fee_apr_pct"] < MIN_FEE:
                    with self.subTest(family=fam, reading=reading, pool=c["pool"]):
                        self.assertNotEqual(r.verdicts[c["pool"]].action, "exit")

    # --- P9: dust positions are handled, not ignored ---------------------
    def test_p9_dust_positions_still_get_a_verdict(self):
        for c in ScaleMatrix.DUST:
            r = run([c], "A")
            with self.subTest(pool=c["pool"]):
                self.assertIn(c["pool"], r.verdicts)
                self.assertIn(r.verdicts[c["pool"]].action,
                              ("exit", "consolidate", "hold", "expand"))

    # --- P10: exact decimal strings do not break the math ----------------
    def test_p10_exact_decimal_inputs(self):
        for v in ("0.00000001", "0.0000001", "0.001", "0.1", "1000"):
            c = mk("D:" + v, 3.0, 5, Decimal(v), mult=1.0)
            r = run([c], "A")
            with self.subTest(value=v):
                self.assertIn("D:" + v, r.verdicts)


class DivergentFallbackRegression(unittest.TestCase):
    """The measured 2026-10-02 case, pinned as a permanent test."""

    def test_heartbeat_style_case_is_never_exited_on_fallback(self):
        # fallback says 0.003%, authoritative truth is 1.18%
        pool = "ALIVE:HEARTBEAT"
        fee = {pool: {"fee_apr_pct": 0.003}}
        rew = {pool: {"count": 0}}
        eco = {pool: {"our_position_value_usd": 0.00012}}
        thr = {pool: {"computable": True, "multiple_to_1e6_fee_threshold": 18.9}}
        r = L.decide([pool], rew, fee_evidence=fee, position_economics=eco,
                     threshold_analysis=thr)          # no override
        self.assertNotEqual(r.verdicts[pool].action, "exit")

        # And WITH the authoritative APR it is correctly productive.
        r2 = L.decide([pool], rew, fee_evidence=fee, position_economics=eco,
                      threshold_analysis=thr,
                      fee_evidence_override={pool: {"fee_apr_pct_authoritative": 1.18}})
        self.assertNotEqual(r2.verdicts[pool].action, "exit")


class DistanceToThresholdRanking(unittest.TestCase):
    """A good RATE does not make a hopeless SIZE a good target.

    Found 2026-10-02 by the dust fixture family. P:DUST_SUB has an excellent
    combined score (APR 5.0 + 10 rewards) and wins expand_candidate on score
    alone — yet at $0.00000002 it is 40x from clearing the fee threshold, while
    P:NEAR_THRESH at $0.00102 is only 0.6x away. Ranking on rate alone sends
    liquidity toward a position that earns nothing.

    This contradicts the stated policy in hive-lp-decision-preferences:
    "a pool can have an excellent rate and still be the wrong target because it
    is hundreds of multiples from clearing."
    """

    def _fixture(self):
        return [
            mk("P:HIGH_RATE_HOPELESS_SIZE", 5.0, 10, 0.00000002, mult=40.0),
            mk("P:NEAR_THRESHOLD",         5.0, 10, 0.00102,    mult=0.6),
        ]

    def test_destination_is_the_near_threshold_pool(self):
        """FIXED 2026-10-02: the viable-but-hopeless-size pool no longer wins."""
        r = run(self._fixture(), "A")
        self.assertEqual(
            r.expand_candidate, "P:NEAR_THRESHOLD",
            "destination must be the pool that can actually clear its threshold")

    def test_pure_rate_ranking_is_still_reachable_via_the_tunable(self):
        """The viability weighting is a documented tunable, not a hard rule."""
        original = L.NEAR_THRESHOLD_BONUS, L.FAR_THRESHOLD_PENALTY
        try:
            L.NEAR_THRESHOLD_BONUS = 0.0
            L.FAR_THRESHOLD_PENALTY = 1.0     # neutral for all multipliers
            r = run(self._fixture(), "A")
            self.assertEqual(r.expand_candidate, "P:HIGH_RATE_HOPELESS_SIZE",
                             "with the weighting neutralised, pure rate ranking "
                             "should return")
        finally:
            L.NEAR_THRESHOLD_BONUS, L.FAR_THRESHOLD_PENALTY = original

    def test_the_correct_answer_is_the_near_threshold_pool(self):
        """UPDATED 2026-10-02 for READING B.

        This previously asserted BOTH pools keep the 'expand' action, which was
        true under Reading A. Reading B (adopted) makes exactly one pool
        'expand', so the runner-up is demoted to 'hold'. The assertion that
        actually matters is unchanged and stronger: the TARGET must be the
        near-threshold pool, not the high-rate one.
        """
        r = run(self._fixture(), "A")
        better = "P:NEAR_THRESHOLD"
        worse = "P:HIGH_RATE_HOPELESS_SIZE"
        self.assertEqual(r.expand_candidate, better)
        self.assertEqual(r.verdicts[better].action, "expand")
        # Under Reading B the loser is demoted, and must say why.
        self.assertEqual(r.verdicts[worse].action, "hold")
        self.assertTrue(r.verdicts[worse].reason.strip(),
                        "the demoted pool must still carry a reason")


class ReadingBSemantics(unittest.TestCase):
    """READING B (adopted 2026-10-02): exactly ONE pool is 'expand'.

    Rationale, three independent signals:
      1. hive-lp-decision-preferences says "do not treat pool count as
         diversification once it is clear every position is inert" and
         "recommend concentration toward a clearing target". Reading A marks
         every viable pool 'expand', which is spreading by another name.
      2. The advisory layer raised exactly this as a standing CHALLENGE:
         "labelling ... as expand conflicts with the single stated expand
         candidate and is left unreconciled."
      3. A single destination makes deployment a SEQUENCE: clear the best
         target, let it compound, then promote the runner-up.

    Demoted pools become 'hold' (keep the position, add nothing) and MUST
    state why, so the artifact still carries the ranking for a human.
    """

    LIVE = [
        mk("ALIVE:HEARTBEAT",  1.173, 11, 0.000117, mult=18.8),
        mk("ALIVE:SWAP.HIVE",  4.752, 20, 0.000078, mult=6.4),
        mk("CENT:POB",         4.984,  1, 0.001020, mult=0.6),
        mk("HSBIDAO:PIXYDUST", 0.041, 13, 0.000219, mult=266.6),
    ]

    def _report(self):
        return L.decide([c["pool"] for c in self.LIVE],
                        {c["pool"]: c["rewards"] for c in self.LIVE},
                        fee_evidence={c["pool"]: c["fee"] for c in self.LIVE},
                        position_economics={c["pool"]: c["econ"] for c in self.LIVE},
                        threshold_analysis={c["pool"]: c["thr"] for c in self.LIVE},
                        fee_evidence_override={
                            c["pool"]: {"fee_apr_pct_authoritative":
                                        c["fee"]["fee_apr_pct"]}
                            for c in self.LIVE})

    def test_exactly_one_pool_is_expand(self):
        r = self._report()
        expands = [p for p, v in r.verdicts.items() if v.action == "expand"]
        self.assertEqual(len(expands), 1,
                         f"Reading B requires exactly one expand target, got {expands}")

    def test_the_one_expand_is_the_best_target(self):
        r = self._report()
        self.assertEqual(r.expand_candidate, "CENT:POB",
                         "the nearest-to-threshold pool is the target")

    def test_demoted_pools_explain_the_demotion(self):
        r = self._report()
        for pool in ("ALIVE:SWAP.HIVE", "ALIVE:HEARTBEAT"):
            v = r.verdicts[pool]
            low = v.reason.lower()
            self.assertTrue(
                any(k in low for k in ("not the target", "secondary", "runner",
                                       "rank", "priority", "concentrat", "later")),
                f"{pool} demoted to '{v.action}' without saying why: {v.reason!r}")

    def test_no_pool_is_silently_lost(self):
        r = self._report()
        for c in self.LIVE:
            self.assertIn(c["pool"], r.verdicts)
            self.assertTrue(r.verdicts[c["pool"]].reason.strip())

    def test_reallocation_target_is_the_single_expand(self):
        r = self._report()
        rec = r.recommended_reallocation
        self.assertIsNotNone(rec)
        self.assertEqual(rec["to_pool"], r.expand_candidate)
        self.assertEqual(rec["to_pool"], "CENT:POB")

    def test_ranking_is_still_recoverable_for_a_human(self):
        r = self._report()
        scores = {}
        for c in self.LIVE:
            v = r.verdicts[c["pool"]]
            scores[c["pool"]] = round(
                (v.fee_apr_pct * L.FEE_APR_SCORE_WEIGHT
                 + v.reward_token_count * L.REWARD_TOKEN_SCORE_WEIGHT)
                * L._viability_factor(c["thr"]), 3)
        self.assertEqual(max(scores, key=scores.get), r.expand_candidate)


class ReadingComparison(unittest.TestCase):
    """Report which invariants each reading breaks — the decision aid."""

    def test_show_which_reading_breaks_what(self):
        lines = []
        broken = {"A": [], "B": []}
        for fam, cases in ScaleMatrix.__dict__.items():
            if fam.startswith("_") or not isinstance(cases, list):
                continue
            for reading in ("A", "B"):
                r = run(cases, reading)
                rec = r.recommended_reallocation
                n_expand = sum(1 for v in r.verdicts.values() if v.action == "expand")
                # The reading-specific property: under B, exactly one expand.
                if reading == "B" and n_expand > 1:
                    broken["B"].append(f"{fam}: {n_expand} pools marked expand")
                if reading == "A" and n_expand == 0 and rec:
                    broken["A"].append(f"{fam}: reallocation with no expand pool")
                lines.append(f"    {fam:<8} reading {reading}: "
                             f"expand={n_expand} candidate={r.expand_candidate} "
                             f"realloc={(rec['from_pool']+'->'+rec['to_pool']) if rec else None}")
        print("\n=== READING COMPARISON ===")
        for l in lines:
            print(l)
        print(f"\n    reading A breaks: {broken['A'] or 'nothing'}")
        print(f"    reading B breaks: {broken['B'] or 'nothing'}")
        print("    (P1-P10 above are reading-agnostic and must pass under both.)")


if __name__ == "__main__":
    unittest.main(verbosity=2)