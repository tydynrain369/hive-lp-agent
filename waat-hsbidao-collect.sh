#!/bin/bash
# WAAT HSBIDAO collection, supervised by the AgentOS process supervisor.
#
# Same contract as waat-collect.sh: wall deadline, idle deadline, process-group
# kill, structured error. The HSBIDAO collector is the one that actually
# broadcasts live transfers, so the idle deadline matters most here -- a
# stalled RPC ("No working nodes available") must not leave the job wedged
# forever with no alert, because an alert fires when the job ENDS.
#
# Measured profile: 1.6s dry-run, 8.9s live. 900s wall / 120s idle gives
# generous headroom over either.
#
# DEADLINES ARE ENV-VARS (like lp-drift-guard's LP_GUARD_*): override
# WAAT_HSBIDAO_WALL / WAAT_HSBIDAO_IDLE to tune without editing this file.
# Defaults below. A timeout exits 124.

WAAT_HSBIDAO_WALL="${WAAT_HSBIDAO_WALL:-900}"
WAAT_HSBIDAO_IDLE="${WAAT_HSBIDAO_IDLE:-120}"

exec python3 \
  /home/tydynrain/.hermes/skills/system-protocols/agentos-process-supervisor/scripts/run_supervised.py \
  --timeout "$WAAT_HSBIDAO_WALL" \
  --idle "$WAAT_HSBIDAO_IDLE" \
  --label waat-hsbidao-collect \
  -- /home/tydynrain/hive-agent-test/.venv/bin/python \
    /home/tydynrain/hive-agent-test/.venv/bin/waat-hsbidao-collect \
    --auto