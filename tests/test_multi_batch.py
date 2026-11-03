"""Regression tests for multi-batch / multi-stream reward handling (2026-10-08).

Chain-verified ground truth (queried directly, 2026-10-08):
  - 36 pools run >1 SIMULTANEOUS distribution batch (DVS:DHEDGE: batch 179
    with 124 ticks left AND batch 329 with 626 ticks left — one pays
    SWAP.HIVE/ALIVE/POB etc., the other pays THREE).
  - 2 pools pay via BOTH distribution AND the mining lottery
    (SWAP.HIVE:PAL, MEME:LOLZ).
  - Exhausted batches (numTicksLeft=0) with nonzero tokenBalances are common
    (PKM:SPS carries 10 batches, 9 exhausted with residual balances) — they
    are dead budget, not income.

Three bugs these tests pin (all fixed 2026-10-08):
  #1a reward_discovery[pair] = [...] kept only the LAST batch (assignment) —
      a pool's second income stream vanished from scoring.
  #1b exhausted batches were reported as live reward streams.
  #2  pools in the mining contract were skipped if ANY distribution batch
      existed — dual-stream pools lost their lottery income.
  #3  reward_value_per_tick OVERWROTE per-token entries — two batches paying
      the same token kept only the last one's value.

Run:
    /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest \
        tests.test_multi_batch -v
"""
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, "/home/tydynrain/.hermes/scripts")
import cron_lp_agent  # noqa: E402 — path shim above
import lp_decision as L  # noqa: E402
from lp_sizing import reward_value_per_tick  # noqa: E402


def _batch(bid, ticks_left, tokens, num_ticks=100, curve=None):
    """Distribution-contract batch row, shaped like the chain returns."""
    return {
        "_id": bid,
        "tokenPair": "TEST:A",
        "active": True,
        "strategy": "pool",
        "numTicks": num_ticks,
        "numTicksLeft": ticks_left,
        "tokenBalances": [{"symbol": s, "quantity": q} for s, q in tokens],
        "bonusCurve": curve,
    }


class _FakeHE:
    """he() stub replaying canned contract responses.

    findOne returns a DICT (the row itself); find returns a LIST.
    Passing this stub as discover_rewards' `pe` argument does NOTHING —
    the function uses the module-level he; use _run_discover.
    """
    def __init__(self, dist_rows, mining_rows, params=None):
        self.dist = dist_rows
        self.mining = mining_rows
        self.params = params or {"distTickHours": 24}
        self.calls = []

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        if method == "findOne":
            return self.params
        if payload.get("contract") == "distribution":
            if payload.get("table") == "batches":
                return self.dist
        if payload.get("contract") == "mining":
            return self.mining
        return []


def _run_discover(dist_rows, mining_rows=None, params=None):
    """Monkeypatch cron_lp_agent.he, run discover_rewards, restore.

    discover_rewards(pe, portfolio): pe is UNUSED (rpc goes through the
    module-level he); passing a stub as pe silently hits the LIVE chain.
    """
    stub = _FakeHE(dist_rows, mining_rows or [], params)
    real_he = cron_lp_agent.he
    cron_lp_agent.he = stub
    try:
        return cron_lp_agent.discover_rewards(None, _FakePortfolio())
    finally:
        cron_lp_agent.he = real_he


class _FakePortfolio:
    positions = {}  # no held positions for these tests


class TestDiscoverRewardsMultiBatch(unittest.TestCase):
    """discover_rewards must ACCUMULATE across live batches."""

    def test_two_live_batches_both_kept(self):
        rd = _run_discover([
            _batch("b1", 100, [("SWAP.HIVE", 1000)]),
            _batch("b2", 200, [("THREE", 5000)]),
        ], [])
        self.assertIn("TEST:A", rd)
        entries = rd["TEST:A"]
        syms = sorted(e["mined_token"] for e in entries)
        self.assertEqual(syms, ["SWAP.HIVE", "THREE"],
                         "BOTH batches' tokens must survive — the old "
                         "assignment kept only the last batch")
        # each entry carries its own batch identity
        by_token = {e["mined_token"]: e for e in entries}
        self.assertEqual(by_token["SWAP.HIVE"]["batch_id"], "b1")
        self.assertEqual(by_token["THREE"]["batch_id"], "b2")
        self.assertEqual(by_token["SWAP.HIVE"]["batch_ticks_left"], 100)
        self.assertEqual(by_token["THREE"]["batch_ticks_left"], 200)

    def test_exhausted_batch_filtered(self):
        """numTicksLeft=0 with nonzero balances is dead budget, not income."""
        rd = _run_discover([
            _batch("dead", 0, [("WORTHLESS", 999999)]),
            _batch("live", 50, [("SWAP.HIVE", 100)]),
        ], [])
        entries = rd["TEST:A"]
        syms = {e["mined_token"] for e in entries}
        self.assertEqual(syms, {"SWAP.HIVE"},
                         "exhausted batch's residual balance must not "
                         "appear as a live reward stream")

    def test_all_batches_exhausted_means_no_entries(self):
        rd = _run_discover([_batch("dead", 0, [("WORTHLESS", 42)])], [])
        self.assertNotIn("TEST:A", rd)

    def test_mining_survives_alongside_distribution(self):
        """Dual-stream pool (SWAP.HIVE:PAL pattern) keeps BOTH incomes."""
        rd = _run_discover([_batch("b1", 100, [("SWAP.HIVE", 100)])],
            [{"active": True, "externalMiners": "TEST:A", "minedToken": "PAL",
              "lotteryWinners": 20, "lotteryAmount": 20,
              "lotteryIntervalHours": 1}])
        entries = rd["TEST:A"]
        types = sorted(e["type"] for e in entries)
        self.assertIn("distribution", types)
        self.assertIn("mining", types,
                      "the mining entry must survive even though a "
                      "distribution batch exists for the same pool")

    def test_runway_days_per_batch(self):
        rd = _run_discover([
            _batch("b1", 30, [("SWAP.HIVE", 100)]),   # 30 days at 24h ticks
            _batch("b2", 100, [("THREE", 5000)]),
        ], [])
        days = {e["batch_id"]: e["days_remaining"] for e in rd["TEST:A"]}
        self.assertEqual(days["b1"], 30.0)
        self.assertEqual(days["b2"], 100.0)


class TestRewardValueSumsAcrossBatches(unittest.TestCase):
    """reward_value_per_tick must SUM a token paid by two live batches."""

    def test_same_token_two_batches_sums(self):
        entries = [
            {"mined_token": "LGN", "daily_reward": 10},
            {"mined_token": "LGN", "daily_reward": 15},
        ]
        v = reward_value_per_tick(entries, {"LGN": 0.02})
        self.assertEqual(str(v["per_token"]["LGN"]["quantity"]), "25")
        self.assertAlmostEqual(v["usd_per_tick_whole_pool"],
                               float(Decimal("25") * Decimal("0.02")))
        self.assertTrue(v["comparable"])

    def test_distinct_tokens_unchanged(self):
        entries = [
            {"mined_token": "A", "daily_reward": 10},
            {"mined_token": "B", "daily_reward": 5},
        ]
        v = reward_value_per_tick(entries, {"A": 0.1, "B": 0.2})
        self.assertAlmostEqual(v["usd_per_tick_whole_pool"], 2.0)
        self.assertEqual(sorted(v["per_token"]), ["A", "B"])

    def test_unpriced_then_priced_accumulates(self):
        """Token seen unpriced (batch A) then priced (batch B): both counted."""
        entries = [
            {"mined_token": "X", "daily_reward": 10},
            {"mined_token": "X", "daily_reward": 5},
        ]
        v = reward_value_per_tick(entries, {"X": 0.5})
        # both priced: 15 * 0.5
        self.assertAlmostEqual(v["usd_per_tick_whole_pool"], 7.5)
        self.assertTrue(v["comparable"])
        self.assertEqual(str(v["per_token"]["X"]["quantity"]), "15")


class TestDecisionRunwayMultiBatch(unittest.TestCase):
    """The runway gate must use the MAX runway across a pool's live batches."""

    HELD = ["ALIVE:HEARTBEAT", "CENT:POB"]
    FEE = {"ALIVE:HEARTBEAT": {"fee_apr_pct": 5.0},
           "CENT:POB": {"fee_apr_pct": 5.0}}
    ECON = {"ALIVE:HEARTBEAT": {"our_position_value_usd": 0.001},
            "CENT:POB": {"our_position_value_usd": 0.001}}
    THRESH = {"ALIVE:HEARTBEAT": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6},
              "CENT:POB": {"computable": True, "multiple_to_1e6_fee_threshold": 0.6}}

    def _decide(self, rewards):
        return L.decide(self.HELD, rewards, fee_evidence=self.FEE,
                        position_economics=self.ECON,
                        threshold_analysis=self.THRESH,
                        fee_evidence_override={
                            p: {"fee_apr_pct_authoritative": self.FEE[p]["fee_apr_pct"]}
                            for p in self.HELD})

    def test_short_batch_plus_long_batch_not_blocked(self):
        """A pool with batches at 5d AND 200d still pays for 200d — no block."""
        rewards = {
            "ALIVE:HEARTBEAT": [
                {"mined_token": "A", "daily_reward": 1, "days_remaining": 5.0},
                {"mined_token": "B", "daily_reward": 1, "days_remaining": 200.0},
            ],
            "CENT:POB": [
                {"mined_token": "C", "daily_reward": 1, "days_remaining": 30.0},
            ],
        }
        r = self._decide(rewards)
        for v in r.verdicts.values():
            self.assertIsNone(v.expand_blocked_reason,
                              "a pool paying through a 200-day batch must "
                              "not be blocked by its 5-day sibling")
        # verdict carries the max runway
        self.assertEqual(r.verdicts["ALIVE:HEARTBEAT"].reward_days_remaining, 200.0)

    def test_all_batches_short_still_blocks(self):
        rewards = {
            "ALIVE:HEARTBEAT": [
                {"mined_token": "A", "daily_reward": 1, "days_remaining": 5.0},
                {"mined_token": "B", "daily_reward": 1, "days_remaining": 8.0},
            ],
            "CENT:POB": [
                {"mined_token": "C", "daily_reward": 1, "days_remaining": 30.0},
            ],
        }
        r = self._decide(rewards)
        self.assertEqual(r.expand_candidate, "CENT:POB")
        self.assertIsNotNone(r.verdicts["ALIVE:HEARTBEAT"].expand_blocked_reason)


if __name__ == "__main__":
    unittest.main()
