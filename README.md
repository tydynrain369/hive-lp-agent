# hive-lp-agent

The autonomous **Hive Engine liquidity agent** — a planner,
advisor, and executor stack that manages liquidity positions
across Hive Engine `marketpools` with deterministic math, real
fee evidence, and hard safety gates.

**The architectural boundary**: the LLM advises, deterministic
Python decides and executes. Every allocation decision is computed
from on-chain evidence; the advisory layer (lp_advisory.py) may
challenge and explain, but never computes quantities or bypasses
the guardrails. Irreversible actions (transfers, add/remove
liquidity) pass through the executor with caps and dry-run modes.

## Layout

| Path | What it is |
|---|---|
| `cron_lp_agent.py` | the daily production entrypoint (cron) |
| `run_lp_agent.sh` | the runtime wrapper |
| `liquidity_agent_v2.py` | the agent core (v2) |
| `lp_*.py` | the engine modules: evidence, mechanics, decision, recommendation, executor, guardrails, sizing, router, reward math, scenarios, assessments |
| `hive_lp_agent.py`, `hive_lp_dashboard.py`, `lp_status.py` | the WAAT-facing agent + dashboards |
| `waat-collect` | the WAAT HIVE collection workflow |
| `tests/` | the pytest suite (economics, mechanics, decisions, reward math) |
| `references/` | deployed `marketpools` / `distribution` contract code (on-chain reference) |
| `reports/` | run reports, verification logs, planner snapshots (data) |
| `archive/` | the rehearsal ladder (`rung1`-`rung15`) and one-off diagnostics that built the system — kept for provenance, not production |

## Running

```bash
python -m pytest tests/ -q        # the suite
python cron_lp_agent.py           # a dry-run cycle
LP_TEST_LIVE=1 python run_live_once.py   # live mode (capped)
```

**Test state (honest)**: 132/162 pass. The 30 failures cluster in
`test_price_inputs.py` and `test_reward_discovery.py` — the engine's
price anchoring and reward-discovery semantics evolved (named
scoring weights, historical price fallbacks) after those tests were
written, and they fail identically against the runtime lane. They
are known debt, tracked for the next agent work pass; everything
else — mechanics, decisions, portfolio, liquidation, reward math,
position scenarios — is green.

Signing keys never live in this tree — the runtime loads them from
the host keyring. The canonical runtime lane is
`~/.hermes/scripts/` on the host; this repository is the source of
truth, synced from it.

## Provenance

The `archive/` rung ladder rehearsed each contract operation live,
one rung at a time (follow → comment → vote → transfer → stake →
swap → liquidity → delegate → lifecycle), before the integrated
agent ever ran. The `reports/` directory holds the verification
logs from those rehearsals and the subsequent integration suites.
