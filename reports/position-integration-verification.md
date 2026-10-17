# Position assessment integration — verified 2026-09-17

## Delivered
- Shared pure `decide_topup_vs_keep` calculation in `.hermes/scripts/lp_position_scenario.py`; standalone capture/replay uses it.
- New public-read `lp_position_assessment.py` adapter: contract-version checks, paginated table reads, current account positions, all participants, active batches, live parameters and balances. Sequential snapshot explicitly NOT atomic.
- Actual cron `manage_positions` now calls the adapter and reports comparisons. Errors become visible observe results. Conditional scenarios are NOT fed to economic evidence or selector decisions.
- No execution settings changed; zero transactions submitted.

## Execution evidence
- Missing cron call reproduced by targeted failing test (expected once; called zero times); same test passed after integration.
- Full suite after audit fix: **84 tests passed**, exit 0. Log: `position-integration-audited-suite.log`.
- Actual cron entry point executed against public RPC, exit 0: `cron-position-integrated-live.log`. Assessed **337 pools**, 15 nonzero paired amounts; position adapter made **14 public reads**, compared all **four held positions**, reported no adapter error, zero transactions.
- Saved replay after final fix: `position-results-20260917T192249Z.json`, exit 0, zero network requests. Results remain observe: HSBIDAO:PIXYDUST rounds to zero; both ALIVE pools have unliquidated third-token rewards; BBH:SWAP.HIVE conditional advantage zero.
- Tests cover replay equivalence, pagination, unknown contracts, observable RPC failure, no conditional evidence promotion, and existing cron key/executor/tracker prohibitions.

## Independent review and correction
Read and verified report artifact: `position-audit/independent-review.md`. Reviewer independently re-derived one saved ALIVE:HEARTBEAT move branch using Fraction arithmetic and checked supplied contract semantics. Review is not a full VM or realized-profit verification.

Medium defect reproduced: active unfunded batch with missing tokenBalances caused ValueError; empty tokenBalances incorrectly consumed simulated ticks. Confirmed deployed scheduler's tokenBalances.0 existence predicate in `reward-audit/deployed_distribution_code.js`; corrected scenario filtering. Regression includes coexistence with a funded batch, and both missing and empty balances. Full suite and replay passed after fix.

Low defect remains open: swap pool_after retains stale cumulative volume counters. Reviewer reports no terminal-value impact; do not use those simulated metadata fields for fee measurement. See independent report for exact reproduction.

## Next economic completion gates
1. Correct simulated swap volume metadata before using it downstream.
2. Branch-specific third-token liquidation routes and common terminal unit, with shared-reserve conflict accounting.
3. Cross-pool reallocation and candidate sizing under shared-wallet constraints.
4. Interval/event-based fee evidence and conservative execution bounds.

This integrates conditional assessment, not a completed trading optimizer. Future prices, fees, participants, funding, settlement availability, and atomic snapshots remain unproven. Scheduled repetition of the updated entry point was not observed in this verification; the entry point was invoked manually.
