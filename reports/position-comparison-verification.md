# Existing-position comparison verification

## Delivered
- Pure `lp_position_scenario.py`: specified top-up versus keep, both with the same initial pair-token wallet and position. Models rewards independently in both branches, rounded all-participant payout reservation per batch, weighted whole-position timeFactor, full withdrawal, then branch-specific terminal liquidation.
- `screen(..., economic_evidence=...)`: trusted upstream evidence handoff bound to snapshot time, recognized contract hash, exact deposit quote, prior shares/timeFactor, horizon start, quote-token unit, affected-pool conflicts, and absence of reported gaps. Copies only economic fields, never mechanics/spend overrides.
- Selector requires an explicit terminal unit and rejects mixing different token units. Two demonstrated failing regressions passed after repair. This is fixed-candidate subset selection, not a full allocation optimizer.
- Conditional comparisons are NOT automatically promoted to verified economic evidence; live cron supplies none and still selects nothing.

## Verification performed
- Full suite: **78 tests passed**, exit 0 (`position-suite-final.log`). Includes actual mocked dry-run cron entrypoint with side-effect guards.
- Targeted real screen-to-selector boundary: 4 tests passed. Stale position timeFactor produces empty selection and observe.
- Live top-up diagnostic: 14 public read requests, recognized marketpools/distribution code hashes, all four current positions covered, no keys/executor or transactions.
- Independent Fraction implementation verified **28 tick records** across both branches, share minting and weighted timestamps; explicit 20-decimal HALF_UP division and token-floor rounding. No scenario/reward module imported by verifier.
- Offline replay completed with zero requests. Input hash retained in result provenance.
- Separate live planner diagnostic: exit 0; 337 pools, 75 funded reward pools, four positions, 15 nonzero paired candidates, empty selection. Snapshot: `planner-snapshot-20260917T183342Z.json`.

## Live-input seven-day counterfactuals
| Position | Result |
|---|---|
| HSBIDAO:PIXYDUST | Paired top-up rounds to zero; no scenario fabricated |
| ALIVE:HEARTBEAT | Both reward branches computed; HASHSCORE, TOOFUCKEH and POKEMON rewards are unliquidated, so complete advantage is unknown |
| ALIVE:SWAP.HIVE | Both reward branches computed; ten positive third-token reward balances remain unliquidated, so complete advantage is unknown |
| BBH:SWAP.HIVE | No modeled distribution rewards; equal terminal amounts (0.00881354 SWAP.HIVE); conditional advantage zero |

These top-ups are independent alternatives using overlapping wallets, NOT a funded joint allocation. Balances/participants were collected by sequential RPC, not an atomic block snapshot. No future fees, price changes, new participants/funding, timing failures or RC risk bounds are proven. Rewards are hypothetical entitlements under stated eventual-settlement assumptions, not received earnings. The independent audit verifies arithmetic on inputs, not their atomicity or future profitability.

## Artifacts and replay
- `/home/tydynrain/.hermes/scripts/lp_position_scenario.py`
- `/home/tydynrain/hive-agent-test/position_scenario_diagnostic.py`
- `/home/tydynrain/hive-agent-test/verify_position_snapshot.py`
- `/home/tydynrain/hive-agent-test/reports/position-inputs-20260917T182830Z.json`
- `/home/tydynrain/hive-agent-test/reports/position-results-20260917T182830Z.json`

```sh
/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/position_scenario_diagnostic.py --replay /home/tydynrain/hive-agent-test/reports/position-inputs-20260917T182830Z.json
/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/verify_position_snapshot.py
PYTHONPATH=/home/tydynrain/.hermes/scripts:/home/tydynrain/hive-agent-test/tests /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest discover -s /home/tydynrain/hive-agent-test/tests
```

## Remaining completion gates
1. Liquidate third-token rewards and express alternatives in a common terminal unit with branch-specific updated-reserve route quotes. Shared route pools must conflict or require joint resimulation.
2. Model cross-pool reallocation and size alternatives, not only independent paired-balance top-ups.
3. Measure fee accrual from interval/event evidence; cumulative lifetime fields and wallet deltas cannot prove LP-specific fee income by themselves.
4. Integrate conditional scenario reporting into the cron without equating assumptions with verified conservative bounds. Trusted evidence flags are caller assertions, not independent certification.
5. Keep broadcast disabled until execution gates are deliberately validated and explicitly authorized. No execution settings changed during this work.
