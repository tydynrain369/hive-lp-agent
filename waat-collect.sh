#!/bin/bash
# WAAT collection, supervised by the AgentOS process supervisor.
#
# This is the cron-facing wrapper. It delegates to run_supervised.py, which
# gives the job a wall deadline, an idle deadline, a process-group kill, and a
# structured error instead of a silent hang or an orphaned child.
#
# The bare waat-collect script has a broken shebang (#!/usr/bin/env python3
# resolves to system python, where nectar is not installed) -- it only worked
# before because the old wrapper called the venv python explicitly. This wrapper
# does the same, through the supervisor, so the venv requirement is enforced
# rather than depending on a lucky shebang.
#
# Exit codes mirror timeout(1): 0/child-code on success, 124 on deadline,
# 125 on missing program, 130 on cancel, 1 on other supervisor stop.
#
# DEADLINES ARE ENV-VARS (like lp-drift-guard's LP_GUARD_*): override
# WAAT_WALL / WAAT_IDLE to tune without editing this file.
# Defaults below. A timeout exits 124.

WAAT_WALL="${WAAT_WALL:-1200}"
WAAT_IDLE="${WAAT_IDLE:-300}"

exec python3 \
  /home/tydynrain/.hermes/skills/system-protocols/agentos-process-supervisor/scripts/run_supervised.py \
  --timeout "$WAAT_WALL" \
  --idle "$WAAT_IDLE" \
  --label waat-collect \
  -- /home/tydynrain/hive-agent-test/.venv/bin/python \
    /home/tydynrain/hive-agent-test/.venv/bin/waat-collect \
    --auto