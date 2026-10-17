# Conditional liquidation verification

## Scope
Pure simulation only: no network, keys, signing, executor, or broadcasts in the new module. Existing-position/cron integration of this module remains outstanding. These results are not realized earnings or future-profit bounds.

## Changes
- `/home/tydynrain/.hermes/scripts/lp_mechanics.py`: simulated swaps now update baseVolume and quoteVolume from absolute reserve deltas, rounded down at pool precision, matching inspected saved deployed marketpools code. Add/remove leave counters unchanged. Missing counters start at zero.
- `/home/tydynrain/.hermes/scripts/lp_liquidation.py`: reuses actual-amount route search from lp_mechanics, sequentially updates copied shared reserves, canonicalizes asset sale order, and reports incomplete coverage as total=None. Hop bound and fixed-state assumptions are explicit. No global route/order optimality claim.
- `/home/tydynrain/hive-agent-test/tests/test_lp_liquidation.py`: six tests including saved-live single-hop Fraction arithmetic, amount-specific route choice, shared reserves, unknown valuation, negative input, and Decimal-context isolation.

## Concrete routing regression
Synthetic 100 A sale: A:B reserves 1/2; A:C and C:B each 1000/1000; fee multiplier 0.9975; token precision 8.
Independent Fraction calculation applies 20-decimal HALF_UP division and token receipt flooring.
- Direct: 1.98014888 B
- First A:C hop: 90.70243237 C
- Two-hop terminal receipt: 82.96899991 B
- Initial draft actual: 1.98014888 B (incorrect route selection from a one-token proxy).
- Corrected actual: 82.96899991 B (matches independent expected).
Negative input produces QuoteError('invalid amount'), a ValueError subclass.

## Executed verification
Targeted suite: 34 tests passed, exit 0.
Two explicit regressions rerun: 2 tests passed, exit 0.
Full unittest discovery: 94 tests passed in 0.759 seconds, exit 0.
Log: `/home/tydynrain/hive-agent-test/reports/liquidation-suite.log`.
Command: `PYTHONPATH=/home/tydynrain/.hermes/scripts:/home/tydynrain/hive-agent-test/tests /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest discover -s /home/tydynrain/hive-agent-test/tests`
The suite's cron output uses fixtures; it is not a fresh live cron execution. Saved-live arithmetic is replay, not a fresh market quote.

## Remaining work
Wire liquidation into both full terminal-position branches after withdrawal, with branch-specific route state; preserve unknown coverage and the observe-only selector boundary. Then exercise live-read/replay integration. Cross-pool resource conflicts, allocation sizing, route/order optimization, fee measurement, settlement timing, and execution authorization remain separate work. Execution remains disabled.
