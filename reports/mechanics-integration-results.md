# LP mechanics and dry-run integration verification

## Implemented
- Added pure marketpools mechanics (conditional exact-input swaps, bounded path search, populated-pool deposits, percentage withdrawals, weighted position age) against saved deployed contract code. These are simulations, not guaranteed executable quotes.
- Added conservative shared-budget subset selection for supplied independent, fixed-cost scenarios. Shared routes/pools conflict; this is not a global continuous allocation optimizer.
- Wired live deposit/withdrawal screening and candidate rejection reporting into cron management.
- Removed wallet/executor construction and simulated funded-test tracking from the dry-run cron main. No execution settings enabled.

## Executed verification
- Full unittest discovery: 63 tests pass, exit 0. The newly added screen-to-selector fixture initially omitted required pool precision; corrected the fixture without weakening production validation and reran successfully.
- Public-data planner diagnostic: exit 0. Snapshot: planner-snapshot-20260917T171214Z.json.
- Actual cron entry point: exit 0. Output: cron-entry-verification.log.
- Latest captured diagnostic: 337 pools assessed, 15 preliminary paired-balance possibilities, 10 passing conditional deposit mechanics, four existing-position withdrawal reviews, zero selected allocations.
- A complete synthetic positive-net-advantage scenario selects; removing switching-cost evidence blocks it. Synthetic values were never attached to live candidates.

## Remaining implementation gap
Live candidates deliberately have unknown reward-horizon earnings, fee/risk coverage, switching-cost bounds, and quote freshness. The screen does not yet construct complete economic scenarios, and cron returns no actions. Consequently it is NOT a finished growth optimizer. Conditional routing exists as a helper but conversion-funded allocations are not integrated.

Next acceptance target: construct one fully traceable move-vs-stay scenario from captured public state, including post-deposit timeFactor, all eligible participants, per-batch schedule, swaps/rounding/costs and explicit uncertainty; feed identical-horizon scenarios into shared-budget selection. Do not mark current reserve-price observations as executable quote evidence. Actual payment reconciliation and longitudinal fee measurement remain outstanding.

No transactions were submitted. No keys were needed by the diagnostic or refactored cron main.
