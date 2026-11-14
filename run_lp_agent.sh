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

# --- RUN LOCK (2026-11-13, Claude review P1-4) ---------------------------
# One run at a time: an overrunning hour or a scheduler retry must never
# overlap another run (double-remove / double-deploy). Non-blocking: a
# second invocation exits 0 — a healthy skip, not a cron failure.
mkdir -p "$HOME/.hermes/lp_tests"
exec 9>"$HOME/.hermes/lp_tests/lp-agent.lock"
if ! flock -n 9; then
    echo "[lp-agent] another run holds the lock — skipping this tick"
    exit 0
fi

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

# ---------------------------------------------------------------------
# BOUNDED OUTPUT CAPTURE (2026-11-03)
# ---------------------------------------------------------------------
# A stray debug print dumped the entire assessment report as ONE 1.38 MB
# line into this job's stdout, hourly for eight hours. Hermes captures a
# no_agent script's stdout verbatim into ~/.hermes/cron/output/<id>/*.md,
# so that became 10.6 MB of single-line blobs. Reading those then blew the
# agent's context past the 65,536-token compression threshold and livelocked
# compression (repeated "made insufficient progress: N -> N; skipping").
#
# We must NOT pipe the live run through `head -c`: SIGPIPE would kill a
# process that may be mid-broadcast. So the run completes untouched into a
# temp file; only the REPORTING is bounded.
LP_OUTPUT_MAX_BYTES="${LP_OUTPUT_MAX_BYTES:-262144}"   # 256 KiB per run

_tmp="$(mktemp "${TMPDIR:-/tmp}/lp-cron-out.XXXXXX")"
trap 'rm -f "$_tmp"' EXIT

set +e
"$PROJECT_VENV/bin/python3" "$LP_SCRIPT" "$@" >"$_tmp" 2>&1
rc=$?
set -e

_size="$(wc -c <"$_tmp")"
if [ "$_size" -gt "$LP_OUTPUT_MAX_BYTES" ]; then
  echo "=== OUTPUT TRUNCATED: run emitted ${_size} bytes; showing first ${LP_OUTPUT_MAX_BYTES} + final 4096 ==="
  head -c "$LP_OUTPUT_MAX_BYTES" "$_tmp"
  printf '\n=== ...output tail... ===\n'
  tail -c 4096 "$_tmp"
  printf '\n=== TRUNCATION NOTE: something printed an oversized blob. Find and bound it at the source (an unbounded print of a dict/report/response). ===\n'
else
  cat "$_tmp"
fi

exit "$rc"
