#!/usr/bin/env bash
# restore_tests.sh — heal any LP test missing from ~/.hermes/scripts/tests.
#
# WHY THIS EXISTS
# ---------------
# tests/*.py have been deleted from the live tree repeatedly (6+ recorded
# events; attributed by PID to the Hermes execute_code kernel, which reaped .py
# files under its working directory). The live tree is the runtime source of
# truth, so a reap silently disarms the suite.
#
# DIRECTION MATTERS (2026-11-03). This script only ever fills a HOLE. It must
# never overwrite an existing live test: live can hold a fix that has not yet
# been promoted to the repo, and an earlier version of this script "refreshed"
# from the repo on any difference — which clobbered exactly such a fix.
# Content reconciliation is lp-sync.sh's job, and it runs live -> repo.
#
# The git repo at ~/hive-agent-test is the restore source: it sits outside the
# kernel's working directory and is version-controlled. The old
# ~/.hermes/lp_tests_canonical/ subset is no longer consulted (it tracked only
# 21 of the 40 tests).
#
# Idempotent; prints nothing when unhealthy. Safe to run from cron.
set -uo pipefail

REPO="/home/tydynrain/hive-agent-test"
DST="/home/tydynrain/.hermes/scripts/tests"

if [ ! -d "$REPO/.git" ]; then
  echo "restore_tests: git repo not found at $REPO" >&2
  exit 1
fi
mkdir -p "$DST"

total=0
restored=0
while IFS= read -r -d '' rel; do
  total=$((total + 1))
  name="${rel##*/}"
  src="$REPO/$rel"
  dst="$DST/$name"
  # Fill a hole ONLY. Never overwrite an existing live test (see DIRECTION above).
  if [ ! -f "$dst" ]; then
    cp -f "$src" "$dst" && echo "restored $name" && restored=$((restored + 1))
  fi
done < <(cd "$REPO" && git ls-files -z -- 'tests/*.py')

if [ "$restored" -eq 0 ]; then
  echo "all $total tests present"
else
  echo "restored $restored of $total tests"
fi
exit 0
