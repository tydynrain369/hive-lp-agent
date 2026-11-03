#!/bin/bash
# ZERO-RISK shadow wrapper for Gemma cron qualification.
# Same binary, same code path, --dry-run instead of --auto: exercises the FULL
# agent loop (tool call -> script run -> read output -> report) without
# broadcasting anything. Swap to --auto only after the shadow proves clean.
/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/.venv/bin/waat-hsbidao-collect --dry-run 2>&1