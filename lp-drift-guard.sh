#!/usr/bin/env bash
# lp-drift-guard.sh — daily coherence guard for the LP agent tree (cron entry).
#
# WHY THIS EXISTS
# ---------------
# The live runtime tree (~/.hermes/scripts) and the git mirror
# (~/hive-agent-test) drifted apart undetected for weeks: 20 live-only tests and
# 10 live-only modules never reached the repo, 16 repo-only tests never reached
# live, and on 2026-11-03 an unreleased live runtime fix was overwritten by the
# mirror. Nothing enforced sameness. This guard is that enforcement, on a timer.
#
# WHAT IT DOES
#   1. Heals any test the execute_code kernel reaped out of the live tree
#      (fills holes only — never overwrites an existing live file).
#   2. Promotes live -> repo, verifies the two trees are byte-identical, runs
#      the suite, and commits locally.
#
# WHY NOT --commit
#   Publishing to the remote stays a reviewed act. When you want origin updated,
#   run:  ~/.hermes/scripts/lp-sync.sh --commit
#
# WATCHDOG SEMANTICS
#   Silent on success (empty stdout -> nothing delivered). On drift or suite
#   failure it prints a short tail of the log, so the cron alert carries the
#   reason. The full transcript always lands in the log beside AUTONOMY_ON.
#
# EXIT  0 coherent | 1 drift detected | 2 suite failed
set -uo pipefail
LIVE="$HOME/.hermes/scripts"
LOG="$HOME/.hermes/lp_tests/drift-guard.log"
mkdir -p "$(dirname "$LOG")"

{
  echo "=== lp-drift-guard $(date -Is) ==="
  bash "$LIVE/tests/restore_tests.sh"
  bash "$LIVE/lp-sync.sh" --local
} >"$LOG" 2>&1
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "LP tree coherence guard FAILED (rc=$rc)"
  echo "live ~/.hermes/scripts  vs  repo ~/hive-agent-test"
  echo "--- last 25 lines of $LOG ---"
  tail -25 "$LOG"
fi
exit "$rc"
