# Unmeasured productivity quarantine

## Result
Full offline suite: `Ran 139 tests in 1.433s`, `OK`, exit 0.
New regression file: `tests/test_lp_unmeasured.py` (4 tests, explicit coverage of both tracker/cron copies).
Initial red run: 2 tests, 4 failing subtests. Missing measurements returned 0.0 and None was classified as failed.

Patched data-flow probe (temporary SQLite only): earned_core_amount=null; measurement_status=unmeasured; record_result.outcome=unmeasured; persisted outcome=testing, fail_streak=0, checked_at=null.

## Changes
In both `/home/tydynrain/hive-agent-test/` and `/home/tydynrain/.hermes/scripts/`:
- lp_test_tracker.py: quarantine legacy fee-counter/wallet-delta aggregation; return null earnings with explicit reason. Unmeasured results cannot complete or penalize a test.
- cron_lp_agent.py: baseline fee fields null rather than default zero; null earnings log safely and leave test pending.

No existing live database or historical balance/payout artifact was rewritten. No schedule changed. No transactions submitted.

## Evidence boundaries
This fixes false classification, not longitudinal fee attribution. The legacy measurement path intentionally cannot return numeric earnings until an attribution adapter exists. Whole-pool counters and unrelated wallet deposits must not be treated as our LP income.

The suite's cron-main execution uses mocked public RPC fixtures; it is NOT a live RPC or scheduled integration run. The source directory is symlink-resolved to /mnt/Arch-Linux-Share/AgentOS/repos/hive-agent-test, but git status reports it is not a git repository; no commit was made.

Next deliverable: a read-only account/pool-attributed fee evidence collector with explicit observation coverage, historical share changes, deployed-contract semantics, per-token amounts, and no invented common-unit valuation. Scheduling integration remains unverified.
