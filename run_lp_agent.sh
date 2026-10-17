#!/usr/bin/env bash
# Cron entry point for the Hive LP agent.
#
# WHY THIS WRAPPER EXISTS
# -----------------------
# Hermes runs `no_agent` cron scripts with its own managed interpreter
# (cron/scheduler_script.py -> resolve_store_python), NOT the shebang in the
# script. The LP agent, however, needs `nectar` (hive-nectar), which lives in
# the project venv:
#
#     /mnt/Arch-Linux-Share/AgentOS/repos/hive-agent-test/.venv   (Python 3.12.14)
#
# Running cron_lp_agent.py under the managed interpreter therefore died with
# `ModuleNotFoundError: No module named 'nectar'` the moment it reached
# liquidity_agent_v2.ExecutionEngine. The project venv has the dependency and
# runs the agent correctly (4 pools scored, decisions written, guard held).
#
# This wrapper activates that venv, so the LP agent's blockchain stack resolves
# regardless of which interpreter Hermes chooses to launch it with.
#
# Deterministic by design: this job stays no_agent (no LLM in the loop), and
# broadcast still requires LP_TEST_LIVE in the environment.
#
# ---------------------------------------------------------------------
# THE AUTONOMY SWITCH (2026-10-17, Tydyn's call)
# ---------------------------------------------------------------------
# Live execution engages when ~/.hermes/lp_tests/AUTONOMY_ON exists:
#
#     touch ~/.hermes/lp_tests/AUTONOMY_ON     # autonomy ON
#     rm    ~/.hermes/lp_tests/AUTONOMY_ON     # autonomy OFF (dry-run)
#
# The caps stay: LP_TEST_MAX_USD (default $0.05 per action) still bounds every
# broadcast; remove-liquidity remains the only exit primitive; the advisory
# layer stays informational. Flipping OFF requires no jobs.json surgery.
set -euo pipefail

PROJECT_VENV="/mnt/Arch-Linux-Share/AgentOS/repos/hive-agent-test/.venv"
LP_SCRIPT="/home/tydynrain/.hermes/scripts/cron_lp_agent.py"
AUTONOMY_FLAG="$HOME/.hermes/lp_tests/AUTONOMY_ON"

if [ ! -x "$PROJECT_VENV/bin/python3" ]; then
  echo "LP agent: project venv missing at $PROJECT_VENV" >&2
  exit 1
fi

if [ -f "$AUTONOMY_FLAG" ]; then
  export LP_TEST_LIVE=1
  echo "=== AUTONOMY: ON (flag present; LP_TEST_LIVE=1; capped at \$${LP_TEST_MAX_USD:-0.05}/action) ==="
else
  echo "=== AUTONOMY: OFF (dry-run; touch $AUTONOMY_FLAG to engage) ==="
fi

exec "$PROJECT_VENV/bin/python3" "$LP_SCRIPT" "$@"
