"""SAFETY GUARD — 2026-10-02.

This script sets LP_TEST_LIVE=1 (real broadcast), then calls
    decide(agent.pe, agent.portfolio, reward_discovery)
but decide()'s signature is
    decide(held_pools, reward_info, fee_evidence=None, ...)

So it passes the PriceEntry/portfolio objects where a pool list is expected and
would raise TypeError on the first line that touches them.

It was therefore dead code that LOOKED runnable and dangerous. Worse, it also
calls decide() WITHOUT fee_evidence_override, so if the signature were ever
"fixed" naively it would decide on the fallback APR, which the 2026-10-02 audit
measured as up to 393x too low — enough to classify a 1.18% APR pool as a
zombie and tell the agent to exit it.

Refusal is deliberate. Do not repair this by adjusting the call arguments.
Use the real path: run_lp_agent.sh / cron_lp_agent.py, which supplies the
authoritative APR via fee_evidence_override.
"""
import sys

ABORT = """
REFUSING TO RUN.

run_live_once.py is quarantined. Two independent reasons:

  1. BROKEN CALL. It calls decide(agent.pe, agent.portfolio, reward_discovery)
     but decide() expects (held_pools: list, reward_info: dict, ...). It would
     raise TypeError before doing anything. It is stale, not working code.

  2. WRONG APR PATH. It omits fee_evidence_override, so any decision made here
     would use the decision layer's own fee_apr_pct — measured on 2026-10-02 as
     7.2x-393.3x too low versus the authoritative recommendation-layer figure.
     On ALIVE:HEARTBEAT that is 0.003% vs 1.18%, which classifies a productive
     pool as a zombie and would tell the agent to exit it.

It also sets LP_TEST_LIVE=1, so it is wired for real broadcast.

Correct entry points:
  - advisory (default, no broadcast): /home/tydynrain/.hermes/scripts/run_lp_agent.sh
  - guarded live test: set LP_TEST_LIVE=1 only deliberately, and use the
    cron_lp_agent.py path, which passes the authoritative APR.
"""

if __name__ == "__main__":
    print(ABORT)
    sys.exit(2)