# LP valuation and assessment repair

## Verified changes
- Production `PortfolioManager.load_positions()` uses public paginated RPC (no keys), reserve share entitlements, and token reporting-unit marks. Exchange ratios are no longer labeled USD.
- Liquid values exclude stake; stake is separately reported. Small known balances are no longer filtered from idle totals. Missing/invalid prices remain None, and incomplete totals are not presented as complete. Refresh replaces stale positions/balances.
- PriceEngine discovers all token precisions, floors rather than rounds spending quantities, refreshes each load, removes frozen BTC/ETH/LTC/DOGE anchors and invented HIVE fallback. Synchronous shortest-hop graph derivation is order-independent; the deepest already-known side wins within a hop. This is indicative pricing, NOT a validated executable oracle. SWAP.HBD=1 is explicitly a reporting assumption with peg/bridge risk.
- Reward discovery preserves separate batch IDs and raw data, removes budget-inflating bonus multipliers, reads live tick duration, paginates, keeps mining separate, and raises on failed discovery instead of treating failure as no rewards.
- Pure lp_reward_math.py computes conditional one-tick pool distribution entitlement with ALL eligible participants, exclusions, weighted timeFactor, age bonus, tick budget, and token flooring. Unsupported/missing data remains unknown. Public upstream source was inspected directly; exact deployed arithmetic parity is not yet proven.
- Active manage_positions() now calls all-pool assessment instead of legacy dollar gates, PEPE quantity score boosts, and assumed conversions. Legacy planner retained as `legacy_manage_positions()` for regression reference only. It is not called by main.
- New assessment makes independent alternatives explicit, never sums overlapping wallet budgets, and flags PEPE relation as metadata. It is NOT yet a net-advantage optimizer. No move recommendations until route/contract/earnings/cost validation exists.

## Execution evidence
Command:
`/home/tydynrain/hive-agent-test/.venv/bin/python -m unittest discover -s /home/tydynrain/hive-agent-test/tests -p 'test_*.py' -v`

Result: **42 tests passed**. Five of these document the disconnected legacy planner, not policy correctness. Delegated reward work timed out with invalid code and contradictory fixture math; parent replaced it with a bounded pool-only implementation and independently derived tests, then ran all tests successfully.

Live public-RPC diagnostic:
`/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/planner_diagnostic.py`

Latest verified run: 20260917T104204Z, exit 0.
- 338 pools assessed; 194 indicative token marks.
- 245 active-flag distribution batches discovered; 76 funded reward pools after filtering.
- Four held positions loaded through production loader.
- Fifteen pools have nonzero paired liquid amounts after reserve-ratio sizing and precision flooring. These are independent alternatives, NOT fifteen simultaneous funded allocations or validated deposit quotes.
- Three held-pool distribution batches produced conditional one-tick estimates; several tokens round to zero, others remain nonzero. BBH:SWAP.HIVE had no funded distribution/mining discovered; fee income remains unknown, so no automatic exit.
- Liquid known mark: 0.002074966789390151 reporting dollars; LP known mark: 0.0005232938635230651. Ten liquid token symbols remain unpriced. These are incomplete indicative marks, not cash-out values. Earlier ~$1.39 portfolio estimate was invalid.
- Zero planned transactions; no keys, executor, tracker writes or broadcasts in the live diagnostic.

Raw evidence:
- /home/tydynrain/hive-agent-test/reports/planner-diagnostic-20260917T104204Z.txt
- /home/tydynrain/hive-agent-test/reports/planner-snapshot-20260917T104204Z.json

## Explicit remaining work
1. Contract-accurate withdrawal/deposit and orientation-aware swap quote simulation using Decimal string quantities and live params; quote freshness, route price impact and conservative switching costs.
2. Compare staying, top-ups, reallocation, consolidation and liquid holding over reward-schedule-bounded horizons, with new/top-up age eligibility; model portfolio budgets jointly. No arbitrary permanent pool count cap.
3. Validate deployed distribution implementation and numeric settings; verify against real payment events. Current live estimates condition on next nominal tick and sequential snapshots, not an atomic chain state or observed payout.
4. Feed candidate-specific entitlement into optimization; current diagnostic calculates held-position batches only. All-pool assessment does not rank by portfolio-relative net returns yet.
5. Measure fee changes over time; do not use lifetime volume or placeholder zeros as earnings.
6. Retire disconnected legacy strategy execution methods/hardcoded lists, and modernize progressive test tracker before enabling live execution. They remain outside the exercised path and are NOT certified by this suite. Some legacy consumers expect numeric totals rather than None; do not run those execution methods without updating them.
7. Cron main still has existing agent initialization and progressive tracking path; this session exercised the explicitly keyless diagnostic, NOT full cron execution. No cron settings or approvals changed.

Both directories lack git repositories; git status could not produce a diff/commit. No commit claimed.
