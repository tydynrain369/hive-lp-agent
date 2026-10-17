# CENT:POB direct-deposit counterfactual verification

## Result and boundary
A replayable seven-day comparison now runs on saved public RPC data for hermes-sandbox. It compares depositing idle CENT/POB to holding those same tokens, using POB terminal liquidation in both branches. It is **not a forecast, a conservative economic lower bound, or authorization to trade**. No keys, executor, signing, or broadcast are involved.

| Quantity | Result |
|---|---:|
| Conditional CENT deposit | 7.88107587 |
| Conditional POB deposit | 1.96463403 |
| Added LP shares | 3.41975432563212342302 |
| Nominal ticks in seven-day window | 7 |
| Eligible ticks for new position | 6 |
| Scheduled POB entitlement | 0.01731696 |
| Hold terminal POB | 5.69767901 |
| Deposit/withdraw terminal POB including rewards | 5.71499596 |
| Conditional advantage, POB | 0.01731695 |
| Live execution | Disabled; zero broadcasts |

The first scheduled tick occurs before the minimum holding age, so the new position earns nothing at that tick. Each later tick uses the remaining batch balance divided by remaining ticks and eligible share fraction, rounded down to token precision. All participants' payouts, including queued payments, reduce the next tick's budget.

## Verification
- Full test suite: **67 tests passed**.
- Focused ScenarioTest.test_scenario_mechanics: passed; no demonstrated mismatch required a production repair.
- Independent Fraction-based arithmetic validated every live-input tick payout and batch depletion without calling the reward module: all checks passed.
- Participant share sum matches pool totalShares; deposit stays within saved balances.
- Terminal subtraction and the observe-only evidence boundary passed independent checks.

## Reproduce offline
```sh
PYTHONPATH=/home/tydynrain/.hermes/scripts /home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/replay_lp_scenario.py /home/tydynrain/hive-agent-test/reports/cent-pob-comparison-inputs.json --days 7
```

Artifacts: `cent-pob-comparison-inputs.json`, `cent-pob-scenario-result.json`, `cent-pob-independent-checks.json` in this directory. Result provenance includes the input SHA256 and reference contract hashes. Those hashes are snapshot-provided references, not fresh on-chain verification by the offline replayer.

## Assumptions and unfinished work
- Constant reserves/prices and participants; no future trades, new funding, or batch edits.
- Successful eventual reward settlement; nominal tick times, not guaranteed block scheduling or receipt before horizon.
- Future LP fee income is unknown. Zero future fees here follows the no-trades scenario, not a claim that LPs earn no fees.
- Deployed swap code updates reserves with full input and reduced output; fee value is retained in LP reserves. Reserve changes alone cannot separate fees from trades and liquidity flows.
- Snapshot atomicity, RC sufficiency, adverse price movement, execution latency, and failure costs are not verified.
- Scope is a new position funded with idle paired balances. Existing-position opportunity costs, cross-pool allocation, and conversion-funded deposits are not covered.
- The seven-day horizon is a demonstration input, not an approved allocation policy.
- Actual historical reward-payment reconciliation remains a separate investigation. This report proves a conditional calculation, not realized earnings.

Therefore the result remains **observe; conditional scenario only** and must not populate verified selector bounds automatically.
