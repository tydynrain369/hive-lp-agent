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
# DEADLINES (2026-11-06) -- why both steps run under the AgentOS supervisor:
#   This job had no deadline of its own. `lp-sync.sh` ends in a pytest run that
#   can block on live RPC, and a cron job that never returns never alerts --
#   the alert fires when the job ENDS, so a hang is indistinguishable from a
#   job that is still working. Both steps now run through run_supervised.py
#   (agentos-process-supervisor), which adds a wall deadline, an idle deadline,
#   a process-GROUP kill (the real tree is bash -> bash -> python -m pytest,
#   so killing the direct child alone would orphan the suite) and a structured
#   error in the log instead of silence. Tune with the LP_GUARD_* vars below;
#   a timeout exits 124. Do not remove this wrapper as "redundant indirection".
#
# EXIT  0 coherent | 1 drift detected | 2 suite failed | 124 a step hit its deadline
set -uo pipefail
LIVE="$HOME/.hermes/scripts"
LOG="$HOME/.hermes/lp_tests/drift-guard.log"
mkdir -p "$(dirname "$LOG")"

SUP="$HOME/.hermes/skills/system-protocols/agentos-process-supervisor/scripts/run_supervised.py"
PY="${LP_GUARD_PY:-/usr/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"

{
  echo "=== lp-drift-guard $(date -Is) ==="
  "$PY" "$SUP" \
    --timeout "${LP_GUARD_RESTORE_WALL:-300}" --idle "${LP_GUARD_RESTORE_IDLE:-120}" \
    --label restore-tests -- bash "$LIVE/tests/restore_tests.sh"
  rc_restore=$?
  [ "$rc_restore" -ne 0 ] && echo "[lp-drift-guard] restore-tests exited $rc_restore"
  "$PY" "$SUP" \
    --timeout "${LP_GUARD_WALL:-2400}" --idle "${LP_GUARD_IDLE:-900}" \
    --label lp-sync -- bash "$LIVE/lp-sync.sh" --local
  rc_sync=$?
} >"$LOG" 2>&1

# REPORT THE FIRST NON-ZERO STEP. In a brace group the status is only the LAST
# command's, so a dead restore step used to hide behind a healthy lp-sync run and
# the guard reported "coherent" over a broken tree. A watchdog that can report
# success while a step failed is worse than no watchdog.

# --- CHAIN AUDIT (P1-9, Opus round 3) ------------------------------------
# Chain vs ledgers, read-only (no keys, no signing). Orphans or a total node
# failure exit non-zero and fail the guard, so the cron's failure_deliver
# reaches the human. Multi-node fallback lives inside lp_chain_audit.py.
rc_audit=0
python3 /home/tydynrain/.hermes/scripts/lp_chain_audit.py || rc_audit=$?
if [ "$rc_audit" -ne 0 ]; then
  echo "LP chain audit: ORPHANS or failure (rc=$rc_audit)"
fi

rc=0
if [ "${rc_restore:-0}" -ne 0 ]; then
  rc="${rc_restore}"
elif [ "${rc_sync:-0}" -ne 0 ]; then
  rc="${rc_sync}"
elif [ "$rc_audit" -ne 0 ]; then
  rc="$rc_audit"
fi

if [ "$rc" -ne 0 ]; then
  echo "LP tree coherence guard FAILED (rc=$rc)"
  echo "live ~/.hermes/scripts  vs  repo ~/hive-agent-test"
  echo "--- last 25 lines of $LOG ---"
  tail -25 "$LOG"
fi
exit "$rc"
