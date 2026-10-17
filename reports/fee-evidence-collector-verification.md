# Fee evidence collector — verification

## Result
- Full suite: `Ran 155 tests in 1.584s / OK`, exit 0 (139 → 155; +16 new tests, zero regressions).
- Copies identical: `/home/tydynrain/hive-agent-test/lp_fee_evidence.py` ≡ `/home/tydynrain/.hermes/scripts/lp_fee_evidence.py` (cmp verified, 8,550 bytes each).
- Test file: `tests/test_lp_fee_evidence.py` (7,167 bytes, repo copy only, per spec).

## Built
`lp_fee_evidence.py` (built by delegated subagent; timed out before writing this report — code + tests landed complete):
- `snapshot_fee_state(rpc, account, pair, at_ms)` — read-only snapshot: pool record + account liquidity position from marketpools; share as exact Fraction {num, den}; reserves, volumes, precision; RPC failure/malformed → `unavailable` with reason.
- `diff_fee_snapshots(before, after)` — longitudinal attribution: share × per-token reserve growth. FAILS CLOSED: any change in pool totalShares or account position shares → `unmeasured` ('share base changed; fee growth not isolatable'). Chosen spec deviation: no per-share ratio isolation attempted — conservative is correct per hive-lp-decision-preferences.
- `collect_fee_evidence(rpc, account, pool_pairs, *, at_ms, contract_hash=None)` — per-pool evidence; hash vs known-good set (fd6d750418df prefix, verified live 2026-09-17); unknown hash → `contract_unverified`.

## Guarantees (test-enforced)
- No Decimal, no floats; all amounts exact integer-string/Fraction; precision 0–8 via divmod flooring.
- Fees reported per-token only — never summed across symbols.
- Unknowns are null + explicit status, never zero.

## Evidence boundaries
- Attribution is longitudinal only: first cron run will record `snapshot_recorded` baselines; a window diff is needed before any `measured` status. One snapshot alone yields no fee numbers.
- Contract attributions valid only while deployed hash stays in known-good set (re-verify on marketpools redeploy).
- Volumes are pool-wide counters — kept in snapshots for diagnostics, NOT used as account income.

## Not yet done
- Wiring into cron_lp_agent.py (persist snapshots per run, feed diff results into lp_test_tracker when measured) — next task.
- Live two-snapshot validation on a real pool over a real window.
- Scheduling/cron integration remains unverified. Execution still disabled; no transactions.
