#!/usr/bin/env bash
# lp-sync.sh — enforce that the live working tree and the git repo are ONE tree.
#
# ROOT CAUSE of the 2026-11-03 divergence: ~/.hermes/scripts (live) is NOT a
# git checkout of ~/hive-agent-test. Every commit pushed a chosen subset, so
# the two trees drifted — 20 live-only tests + 10 live-only modules never reached
# the repo, 16 repo-only tests never reached live. Nothing enforced sameness.
#
# This script is the enforcer. Run it after ANY change to the LP agent:
#   lp-sync.sh            # bidirectional sync + verify, no commit
#   lp-sync.sh --commit  # sync, then commit + push
#
# Rules:
#   - repo is canonical (it is the cron venv source, mount-linked at
#     /mnt/Arch-Linux-Share/AgentOS/repos/hive-agent-test).
#   - live (~/.hermes/scripts) is a synced mirror.
#   - sync is bidirectional: repo -> live always; live -> repo for files that
#     pass the gate (see GATE_* below).
#   - exit non-zero on drift so a cron can catch it.
set -euo pipefail

REPO="${HOME}/hive-agent-test"
LIVE="${HOME}/.hermes/scripts"
VENV="${REPO}/.venv/bin/python"
COMMIT=0
if [ "${1:-}" = "--commit" ]; then COMMIT=1; fi

# --- gate: what may travel live -> repo -------------------------------
# Tests must actually pass. Ad-hoc scripts (probes, dryruns that hit live RPC)
# are quarantined, never promoted.
PASSING_TESTS="test_age_bonus test_both_tokens_lifecycle test_expand_candidate \
  test_lp_dividends test_lp_lifecycle test_lp_route_cost test_lp_router \
  test_lp_sizing test_multi_batch test_operator_token_lists \
  test_realisable_dividend test_realloc_executor test_scale_properties"

# --- 1. repo -> live: everything tracked --------------------------------
cd "$REPO"
git ls-files -z -- '*.py' '*.sh' | while IFS= read -r -d '' f; do
  mkdir -p "$LIVE/$(dirname "$f")"
  cp -f "$REPO/$f" "$LIVE/$f"
done
# tests live under tests/ in both
git ls-files -z -- 'tests/*.py' | while IFS= read -r -d '' f; do
  mkdir -p "$LIVE/$(dirname "$f")"
  cp -f "$REPO/$f" "$LIVE/$f"
done

# --- 2. live -> repo: gated promotion ----------------------------------
for t in $PASSING_TESTS; do
  if [ -f "$LIVE/tests/$t.py" ]; then
    cp -f "$LIVE/tests/$t.py" "$REPO/tests/$t.py"
  fi
done

# --- 3. verify: every tracked file identical in both trees --------------
drift=0
while IFS= read -r -d '' f; do
  if [ ! -f "$LIVE/$f" ]; then
    echo "DRIFT (missing from live): $f" >&2
    drift=1
  elif ! cmp -s "$REPO/$f" "$LIVE/$f"; then
    echo "DRIFT (live != repo): $f" >&2
    drift=1
  fi
done < <(git ls-files -z -- '*.py' '*.sh' 'tests/*.py')
# live-only tracked-namespace files: any .py/.sh in live (not under archive/
# agentos/, not tests_canonical/, not __pycache__) that is NOT tracked by git.
# Nothing is allowlisted — the reconcile (2026-11-03) tracked every live-critical
# file, so any straggler is a real drift, not a legitimate exception.
while IFS= read -r f; do
  case "$f" in
    */tests_canonical/*|*/archive/*|*/agentos/*|*/__pycache__/*) continue ;;
  esac
  rel="${f#$LIVE/}"
  if git -C "$REPO" ls-files --error-unmatch "$rel" >/dev/null 2>&1; then
    continue
  fi
  echo "DRIFT (live-only, not in repo): $rel" >&2
  drift=1
done < <(find "$LIVE" -maxdepth 2 \( -name '*.py' -o -name '*.sh' \) -not -path '*/tests/*' \
  -not -path '*/tests_canonical/*' -not -path '*/archive/*' \
  -not -path '*/agentos/*' -not -path '*/__pycache__/*' -print 2>/dev/null)

if [ "$drift" -ne 0 ]; then
  echo "lp-sync: DRIFT DETECTED" >&2
  exit 1
fi
echo "lp-sync: trees identical ($(git ls-files | wc -l) tracked files)"

# --- 4. run the suite in the canonical repo -----------------------------
echo "lp-sync: running suite..."
"$VENV" -m pytest tests/ -q --tb=line -p no:randomly >/tmp/lp-sync-pytest.log 2>&1 || {
  echo "lp-sync: SUITE FAILED" >&2
  tail -20 /tmp/lp-sync-pytest.log >&2
  exit 2
}
passed=$(grep -oE '[0-9]+ passed' /tmp/lp-sync-pytest.log | tail -1)
echo "lp-sync: $passed"

# --- 5. optional commit + push -----------------------------------------
if [ "$COMMIT" -eq 1 ]; then
  cd "$REPO"
  if git diff --quiet && git diff --cached --quiet; then
    echo "lp-sync: nothing to commit"
  else
    git add -A
    git commit -m "lp-sync: reconcile live/repo trees ($passed)"
    git push origin main
    echo "lp-sync: committed + pushed"
  fi
fi
exit 0
