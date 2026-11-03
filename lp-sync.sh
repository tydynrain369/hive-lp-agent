#!/usr/bin/env bash
# lp-sync.sh — keep the live runtime tree and the git repo as ONE tree.
#
# MODEL (settled 2026-11-03)
# -------------------------
#   LIVE  ~/.hermes/scripts     runtime source of truth.
#                               run_lp_agent.sh pins LP_SCRIPT to
#                               live/cron_lp_agent.py, and Python resolves the
#                               lp_* imports from that script's own directory.
#                               Whatever LIVE holds is what actually runs.
#   REPO  ~/hive-agent-test     versioned mirror (git), a symlink target under
#                               /mnt/Arch-Linux-Share/AgentOS/repos/. It also
#                               supplies the venv the cron activates.
#
# DIRECTION IS LIVE -> REPO. The previous revision of this script called the
# repo canonical and copied repo -> live over every tracked file. On 2026-11-03
# that silently CLOBBERED an unreleased live runtime fix: the joint-allocation
# evidence handoff existed only in live, while the repo still held the
# regression that disabled it. A runtime tree must never be overwritten by its
# own mirror, so the enforcer now promotes and then verifies.
#
# WHAT IS "THE LP TREE"
#   live  : top-level *.py/*.sh/*.ini whose name matches the LP namespace, plus
#           tests/*.py and tests/*.sh, plus pytest.ini.
#   repo  : the same set; the repo additionally (and legitimately) holds
#           README.md, references/, reports/, bin/, archive/, .gitignore and the
#           waat-collect source script. Those are repo-only and never expected
#           in live.
#   other lanes sharing ~/.hermes/scripts (hermes-mega-sync.sh = MEGA DR,
#   qortal_listing_watch.sh = Qortal monitor) are NOT part of this tree and are
#   excluded by name.
#
# USAGE
#   lp-sync.sh            promote live -> repo, verify, run the suite
#   lp-sync.sh --check    verify only; no copy and no suite (fast drift alarm)
#   lp-sync.sh --commit   promote + verify + suite, then commit + push
#
# EXIT  0 ok | 1 drift detected | 2 suite failed | 64 usage
set -uo pipefail

REPO="${HOME}/hive-agent-test"
LIVE="${HOME}/.hermes/scripts"
VENV="${REPO}/.venv/bin/python"

MODE=run
COMMIT=0
case "${1:-}" in
  "")        ;;
  --check)   MODE=check ;;
  --commit)  COMMIT=1 ;;
  *) echo "usage: lp-sync.sh [--check|--commit]" >&2; exit 64 ;;
esac

[ -d "$REPO/.git" ] || { echo "lp-sync: no git repo at $REPO" >&2; exit 64; }
[ -d "$LIVE" ]      || { echo "lp-sync: no live tree at $LIVE" >&2; exit 64; }

# --- the LP namespace ---------------------------------------------------
is_lp_base() {
  case "$1" in
    lp_*|lp-*|waat-*|cron_lp_agent*|hive_lp_agent*|hive_lp_dashboard*|\
liquidity_agent_v2*|run_lp_agent*|run_live_once*|supervised_live_tick*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- 1. build the promote list from LIVE --------------------------------
promote=()
while IFS= read -r f; do
  base="${f##*/}"
  is_lp_base "$base" && promote+=("${f#"$LIVE"/}")
done < <(find "$LIVE" -maxdepth 1 -type f \( -name '*.py' -o -name '*.sh' \) 2>/dev/null | sort)

while IFS= read -r f; do
  promote+=("${f#"$LIVE"/}")
done < <(find "$LIVE/tests" -maxdepth 1 -type f \( -name '*.py' -o -name '*.sh' \) 2>/dev/null | sort)

[ -f "$LIVE/pytest.ini" ] && promote+=("pytest.ini")

# --- 2. promote live -> repo --------------------------------------------
changed=0
for rel in "${promote[@]}"; do
  src="$LIVE/$rel"; dst="$REPO/$rel"
  mkdir -p "$(dirname "$dst")"
  if [ ! -f "$dst" ] || ! cmp -s "$src" "$dst"; then
    if [ "$MODE" = run ]; then
      cp -f "$src" "$dst" && { echo "promoted: $rel"; changed=1; }
    fi
  fi
done
[ "$MODE" = run ] && [ "$changed" -eq 0 ] && echo "lp-sync: nothing to promote"

# --- 3a. verify: every promoted file is identical in the repo -----------
drift=0
for rel in "${promote[@]}"; do
  src="$LIVE/$rel"; dst="$REPO/$rel"
  if [ ! -f "$dst" ]; then
    echo "DRIFT (missing from repo): $rel" >&2; drift=1
  elif ! cmp -s "$src" "$dst"; then
    echo "DRIFT (live != repo): $rel" >&2; drift=1
  fi
done

# --- 3b. verify: no tracked test has been reaped out of LIVE ------------
while IFS= read -r -d '' rel; do
  if [ ! -f "$LIVE/$rel" ]; then
    echo "DRIFT (tracked test missing from live): $rel" >&2
    echo "       -> run tests/restore_tests.sh to heal it" >&2
    drift=1
  fi
done < <(cd "$REPO" && git ls-files -z -- 'tests/*.py')

if [ "$drift" -ne 0 ]; then
  echo "lp-sync: DRIFT DETECTED" >&2
  exit 1
fi
echo "lp-sync: trees identical ($(cd "$REPO" && git ls-files | wc -l) tracked files in repo)"

[ "$MODE" = check ] && exit 0

# --- 4. run the suite against the repo (the promoted mirror) ------------
echo "lp-sync: running suite..."
if ! (cd "$REPO" && "$VENV" -m pytest -q -p no:cacheprovider) >/tmp/lp-sync-pytest.log 2>&1; then
  echo "lp-sync: SUITE FAILED" >&2
  tail -20 /tmp/lp-sync-pytest.log >&2
  exit 2
fi
passed=$(grep -oE '[0-9]+ passed' /tmp/lp-sync-pytest.log | tail -1)
echo "lp-sync: ${passed:-suite ok}"

# --- 5. optional commit + push ------------------------------------------
if [ "$COMMIT" -eq 1 ]; then
  cd "$REPO"
  if git diff --quiet && git diff --cached --quiet; then
    echo "lp-sync: nothing to commit"
  else
    git add -A
    git commit -q -m "lp-sync: promote live runtime tree -> repo (${passed:-suite ok})"
    git push -q origin main && echo "lp-sync: committed + pushed"
  fi
fi
exit 0
