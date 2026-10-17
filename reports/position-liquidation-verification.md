# Full-position liquidation integration verification

## Scope
Implemented conditional common-unit valuation for keep versus specified top-up. Both branches withdraw their full position, aggregate principal with idle pair-token funds and projected rewards, then liquidate against separate post-withdrawal reserve graphs. Same-symbol amounts are combined. Sales share projected reserves in sorted-symbol order; routing uses actual sale amounts with an explicit hop bound. Sale traces and affected pools are retained.

Public-input replay now supplies the full pool graph and defaults to SWAP.HIVE reporting. Independent top-ups do NOT jointly allocate the wallet. Missing routes leave full terminal value and advantage unknown. Conditional reports retain coverage_complete=false and execution_allowed=false; they are not verified selector evidence.

## Execution evidence
- Final unittest discovery: 99 tests, 1.187s, OK, exit 0. Includes actual cron main with synthetic public inputs and guarded side effects; not a live scheduled cron test.
- Public diagnostic: 14 read-only RPC requests; input snapshot saved at position-inputs-20260917T201637Z.json; results at position-results-20260917T201637Z.json. Zero transactions submitted.
- Snapshot SHA256: 8e2df573331a5cfb2245f465d0e341f214eedda15256e91844b1b1f070288d79.
- Independent verifier: /home/tydynrain/hive-agent-test/verify_position_liquidation.py, exit 0. Uses Fraction arithmetic without importing the production math. Validates specified BBH deposit/share issuance, full withdrawals, wallet aggregation, direct sale and resulting reserves in both branches.
- BBH:SWAP.HIVE terminals: keep 0.00881354 SWAP.HIVE; top-up 0.00881354 SWAP.HIVE; conditional advantage 0.00000000. This saved fixture has no projected rewards and does not independently verify reward scheduling or route optimality.

## Replay
```sh
PYTHONPATH=/home/tydynrain/.hermes/scripts:/home/tydynrain/hive-agent-test/tests /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest discover -s /home/tydynrain/hive-agent-test/tests
/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/verify_position_liquidation.py
/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/position_scenario_diagnostic.py --replay /home/tydynrain/hive-agent-test/reports/position-inputs-20260917T201637Z.json
```

## Remaining boundaries
Sequential RPC snapshot is not atomic; future external trades, fees, funding, participants and settlement timing remain assumptions. Bounded simple paths and deterministic sale order are not a globally optimal liquidation strategy. Deposit sizing is specified, not optimized. Cross-pool allocation, fee measurement and final operational integration remain outstanding. No trading was enabled.
