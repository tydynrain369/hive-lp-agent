# Joint portfolio simulation verification

## Delivered
- Pure engine: `/home/tydynrain/.hermes/scripts/lp_portfolio.py`.
- Ordered explicit deposits debit actual accepted amounts from one wallet and commit shares, age factors and pool reserves.
- All scoped positions accrue batch rewards, then withdraw before any terminal sale. Included idle wallet, principal and same-symbol rewards aggregate once.
- `compare_portfolios` evaluates supplied plans against hold using identical inputs. It is not an allocation search or economic-evidence producer.
- Read-only live/replay driver: `/home/tydynrain/hive-agent-test/rehearse_portfolio.py`.

## Verification performed
- Initial new test failed with the expected missing-module import before implementation.
- 10 portfolio tests pass: independent Fraction synthetic shared-route check, single-pool equivalence (one reward fixture, both branches), wallet overspend, repeated deposits, new positions, malformed participant state, reward aggregation, unknown valuations, explicit sizing, invalid-plan isolation and bad horizons/batches.
- Full suite: **127 tests, OK**, final direct execution exit 0 (1.360s).
- Fresh public capture: **14 requests**, 4 held positions, 2 simulated top-ups (ALIVE:HEARTBEAT and BBH:SWAP.HIVE), zero transactions.
- Live artifact: `/home/tydynrain/hive-agent-test/reports/portfolio-rehearsal-20260917T211950Z.json`.
- Input: `/home/tydynrain/hive-agent-test/reports/position-inputs-20260917T211934Z.json`.
- Verified SHA256: `0752863c13792a20aa120fc72bf23b1011821a4e884d72bec19d403410b38e79`.
- Independent Fraction verifier checked **62 successful sale legs**, including output and committed reserves; exit 0. Scope: sale arithmetic, not global route optimality or all upstream reward/withdrawal arithmetic.
- Initial verifier mismatch was an eight-decimal test-helper assumption; live HSBIDAO:LBI uses six-decimal pool reserves. Dedicated verifier uses recorded pool precision and live fee parameter.

## Actual result and limitations
- Hold terminal, top-up terminal, and overall conditional advantage remain **unknown**.
- Fresh snapshot has 12 unliquidated symbols: 10 have no structural path within 3 hops; LIMITLESS and THREE have paths but fail nonzero-output/liquidity checks at actual amounts. Diagnosis: `portfolio-routing-diagnosis.json`.
- Follow-up trace: both post-withdrawal pool graphs are identical in this snapshot. LIMITLESS and THREE route failures are confirmed rounded-zero outputs (not exhausted liquidity). THREE fails at HSBIDAO:THREE, where HSBIDAO output rounds to 0.000.
- POKEMON reconciles over 21 projected payouts per branch: idle 0.00001092 + hold rewards 0.00001545 = 0.00002637; idle + top-up rewards 0.00001616 = 0.00002708. No POKEMON pool exists in this captured graph. These are scenario projections, not receipts or independent reward verification. Unknown assets cannot simply cancel.
- Known subtotals are diagnostic only, not whole-portfolio value or earnings. No token is assigned zero to manufacture coverage.
- Fixed reserves, fixed outside participants, nominal reward scheduling and eventual settlement are assumptions. Future fees, price risk, RC and snapshot atomicity remain unverified.
- All supplied liquid wallet tokens and scoped holdings are included; stakes are not. Caller must supply all intended participant tables; route-only pools do not reveal omitted positions.
- Example plan generation is snapshot-order paired sizing, not optimal allocation. No funding swaps, exit/reallocation search, automated size search, cron integration or broadcasting added.

## Reproduce
```sh
PYTHONPATH=/home/tydynrain/.hermes/scripts:/home/tydynrain/hive-agent-test/tests /home/tydynrain/hive-agent-test/.venv/bin/python -m unittest discover -s /home/tydynrain/hive-agent-test/tests
/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/verify_portfolio_sales.py /home/tydynrain/hive-agent-test/reports/portfolio-rehearsal-20260917T211950Z.json
/home/tydynrain/hive-agent-test/.venv/bin/python /home/tydynrain/hive-agent-test/rehearse_portfolio.py --replay /home/tydynrain/hive-agent-test/reports/position-inputs-20260917T211934Z.json
```

Next: bounded search over candidate sizes and action sequences, with explicit incomplete-valuation handling; fee measurement and final cron wiring still outstanding. Execution remains disabled.
