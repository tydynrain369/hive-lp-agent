# LP planner diagnostic handoff

## Completed
- Added opt-in `diagnostics=True` to `manage_positions`; default allocation rules and dry-run behavior preserved. Function now returns its planned actions for inspection.
- Added five synthetic legacy-gate regression tests; all five pass. These validate diagnostics and current behavior, not the desirability of the policy.
- Executed a public-RPC-only live diagnostic for hermes-sandbox at 20260917T100507Z. No keys, executor, trading-agent initialization, or test-tracker database writes.
- Live result: 338 pools loaded, 4 account LP positions loaded, legacy discovery produced 76 reward-pool entries, 73 unheld candidates rejected, 0 actions. All four positions fail the ordinary exit USD gate. Raw responses retained in planner-snapshot-20260917T100507Z.json; this is a sequential live capture, not an atomic block snapshot.

## Unresolved correctness failures
- `PortfolioManager.load_positions` values tokens using pool exchange ratios, not the token USD price map. Earlier ~$1.39 portfolio claims must not be treated as established USD valuations.
- Two synthetic valuation tests in tests/test_position_valuation.py FAIL intentionally against current production code: USD unit correctness and unknown-price propagation. The AST-expression test harness will need adapting if the expression is refactored into a helper or conditional.
- Legacy token price engine itself still has fixed external-token anchors, order-dependent first-path derivation, and missing-price zero defaults. Diagnostic alternative marks are not validated liquidation values.
- Liquid balance `value_usd` includes stake; it is not a safe spendable balance.
- Existing distribution discovery inflates total budgets with a bonus multiplier and merges same-token batches, losing eligibility/schedule distinctions. Upstream distribution.js shows bonuses apply to eligible effective shares, minimum age depends on distTickHours, and payout is floored to token precision. Upstream master is reference evidence, not yet proof of exact deployed bytecode parity.
- Existing $1 entry/exit and $0.01 pool-reward thresholds remain unchanged. Route quotes, our entitlement, switching costs, and conservative hold/move comparison are not yet implemented.

## Reproduce
`/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/tests/test_planner_legacy_gates.py`

`/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/tests/test_position_valuation.py` (expected failures until repaired)

`/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/planner_diagnostic.py`

## Next bounded implementation
Repair valuation units and explicit unknown handling without introducing None arithmetic failures in legacy callers; separate spendable balances from stake. Then retain per-batch reward data and model eligible rounded entitlements. Only after those inputs are tested should the planner replace dollar gates with precision/contract/quote feasibility and a conservative net-advantage comparison. Keep broadcasts disabled.
