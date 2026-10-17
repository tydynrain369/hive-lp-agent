# Shared-wallet evidence adapter — partial phase verification

## Delivered
- Added lp_evidence.py: identity/action-bound, explicit economic-evidence adapter into the existing independent subset selector.
- Added diagnostic joint output without altering the production cron or enabling execution.
- Hardened lp_decisions.py conflicts validation: reject strings, dictionaries, empty strings and non-string members.
- 117 tests pass (unittest discover, exit 0); 17 evidence tests plus one new conflict regression above the prior 99-test baseline.

## Important correction
The initial adapter incorrectly promoted complete fixed-state liquidation into verified economic bounds and assumed zero switching costs. Two new regression tests failed as expected, then the gate was corrected. Final code requires explicit economic coverage and separately supplied bounds/costs; raw position_scenario outputs cannot qualify. The adapter checks supplied evidence structure and identity, not its economic truth. No producer of qualifying live bounds has been implemented here.

The earlier position-results-20260917T204355Z.json is a pre-correction development replay and is superseded by the corrected live diagnostic below. Its BBH candidate eligibility is not valid evidence.

## Corrected public-read exercise
- Inputs: position-inputs-20260917T204643Z.json
- Results: position-results-20260917T204643Z.json
- 14 public-read RPC requests; zero transactions.
- Result provenance input SHA256 checked against saved input bytes.
- 4 comparisons; 0 qualifying candidates, 0 selected, execution_allowed=false.
- HSBIDAO:PIXYDUST paired deposit rounds to zero.
- ALIVE:HEARTBEAT and ALIVE:SWAP.HIVE remain incomplete liquidations, advantages unknown.
- BBH:SWAP.HIVE conditional advantage remains zero and economic coverage remains incomplete, so it is rejected.
- Snapshot uses sequential RPC, not atomic block state. Evaluation uses actual wall clock, not an artificial replay freshness exemption.

## Remaining work / limits
This is not a completed cross-pool optimizer. It wires the evidence boundary and exercises shared-wallet/conflict selection with explicitly synthetic complete evidence. Real comparisons still cannot qualify. No joint portfolio resimulation, variable deposit sizing, new-pool allocation, fee/risk bounds, or cron activation was added. Combining candidates with shared reserves is rejected rather than resimulated. Next substantive work is joint portfolio simulation under one wallet, followed by defensible fee/cost/risk measurement. Conditional simulations must remain distinct from conservative economic evidence.

Reproduce tests:
`PYTHONPATH=/home/tydynrain/.hermes/scripts:/home/tydynrain/hive-agent-test/tests /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest discover -s /home/tydynrain/hive-agent-test/tests`
