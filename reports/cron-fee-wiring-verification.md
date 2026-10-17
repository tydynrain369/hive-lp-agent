# Cron fee-wiring — verification

## Result
- Full suite: `Ran 160 tests in 1.235s / OK`, exit 0 (155 → 160; +5 new tests, 10 subtests across both cron copies).
- New: `tests/test_cron_fee_wiring.py`.

## Changes
- `lp_fee_evidence.py` (both copies, synced): added `record_fee_snapshots(rpc, account, positions, *, store_path, at_ms, contract_hash)` — snapshots each held pool, appends to `~/.hermes/lp_tests/fee_snapshots.jsonl` (append-only JSONL), diffs against the most recent prior snapshot for the same (pool, account). Also added the `note` field marking reserve-growth attribution semantics. Corrupt store lines skipped, never parsed-as-truth.
- `cron_lp_agent.py` (both copies): thin `record_fee_snapshots` delegate + `fee_snapshots_section(agent)` wired into `main()`. Section: verifies deployed marketpools codeHash (skip if lookup fails), wraps `he('find', ...)` into the collector's rpc shape, uses `portfolio.account`/`portfolio.positions`, prints per-pool status and any measured per-token fees.

## First live cron run behavior
- Records baseline snapshots for each held position (status `snapshot_recorded`; no fee numbers — there is nothing to diff against yet).
- From the SECOND run onward: per-token attributable fee amounts appear when the share base is unchanged; `unmeasured` + reason otherwise.
- RPC failure → `unavailable`, cron continues. Unrecognized contract hash → all snapshots skipped.

## Boundaries
- Read-only; execution remains disabled; zero transactions.
- Deposit/withdraw-tainted windows return `unmeasured` (conservative), consistent with redemption semantics.
- Feed-through to lp_test_tracker remains untouched: measured per-token amounts are evidence, not yet a unified earnings figure; tracker quarantine stands.
- Cron scheduling itself (actual daemon job) not yet enabled/verified.
