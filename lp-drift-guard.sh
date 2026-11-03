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
# EXIT  0 coherent | 1 drift detected | 2 suite failed
#   Non-zero surfaces as a cron failure notification. That IS the alarm.
set -uo pipefail
LIVE="$HOME/.hermes/scripts"

bash "$LIVE/tests/restore_tests.sh" || true
exec bash "$LIVE/lp-sync.sh" --local
