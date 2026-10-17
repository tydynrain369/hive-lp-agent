# Deployed distribution reward audit

## Outcome
- Confirmed actual hermes-sandbox receipt: **0.00006709 PEPE**, batch **296**, virtual transaction **109992361-9**, sidechain block **62933498**, Hive reference block **109992361**, **2026-09-17 13:05:24 UTC**. The same logs contain `tokens.transferFromContract` from `distribution` to `hermes-sandbox` and `distribution.payment` with `pending:true`; no transaction errors. Additional receipts: 0.00000057 LIMITLESS and 0.00000022 PIXYDUST.
- Batch 296 currently maps to **HSBIDAO:PIXYDUST**. Its calculation tick is supported by immediate batch-296 payment events in block **62933493**, timestamp **13:05:09 UTC**, virtual transaction **109992356-9**, matching current `lastTickTime=1789650309000`.
- Receipt is verified, **exact historical entitlement reconciliation is not**. No realized receipt demonstrates projected returns, annualized yield, sale value, or future payment guarantees.
- No production files, keys, execution configs, skills or chain state changed. All audit artifacts are under `/home/tydynrain/hive-agent-test/reports/reward-audit/`. Twelve public network requests total (including one arithmetic-library download).

## Public verification path / raw evidence
All filenames below are within `/home/tydynrain/hive-agent-test/reports/reward-audit/`.

1. `GET https://accounts.hive-engine.com/accountHistory?account=hermes-sandbox&limit=100&offset=0` returned indexed distribution transfers, including their block numbers and virtual transaction IDs. Raw: `account_history.raw.json`.
2. `POST https://api.hive-engine.com/rpc/blockchain`, JSON-RPC method `getBlockInfo`, params `{"blockNumber":62933498}` returned authoritative sidechain event logs. Raw: `payment_block.raw.json`; readable account-only extract: `payment_account_events.json`. For prior queued receipts see `payment_prior_block.raw.json` (62933497). Calculation-time block: `candidate_calculation_block.raw.json` (62933493).
3. `getTransactionInfo` with `{"txid":"109992361-9"}` returned null; virtual transactions were accessible in `getBlockInfo.virtualTransactions`. Do not treat this null as nonpayment. Raw: `payment_transaction.raw.json`.
4. `POST https://api.hive-engine.com/rpc/contracts`: `findOne` of `distribution.batches` `_id:296`; `findOne` of `distribution.params`; `find` of `distribution.pendingPayments` with `{"accounts.account":"hermes-sandbox"}`; `getContract` with `{"name":"distribution"}`. Raw: `current_batch_296.raw.json`, `distribution_params.raw.json`, `pending_hermes.raw.json`, `current_distribution_contract.raw.json`.
5. Every request has an adjacent `.request.json` containing URL, payload, retrieval timestamp and HTTP status. `network_manifest.json` inventories calls; `sha256_manifest.json` fingerprints the authority file, audited Python source, payment block and arithmetic dependency.

## Exact snapshot availability
- Public contract table reads obtained **current state**, not historical pre-tick state. No archive/as-of-state API was established in this bounded audit. Block logs provide transactions/events and state hashes, not full participant or pre-deduction batch rows. A hash does not reconstruct its state.
- `pendingPayments` currently has no entry containing this account. Deployed queue records retain dueTime and precomputed account/symbol/quantity, not denominator, shares, bonus curve, precision or pre-payment balances; completed entries are removed. Therefore a pending receipt cannot itself recover the calculation inputs.
- Local planner snapshots **do** contain earlier complete-query participant rows and batch state. `/home/tydynrain/hive-agent-test/reports/planner-snapshot-20260917T104204Z.json` contains 20 HSBIDAO:PIXYDUST participants, batch 296 with 599 ticks, distribution params, and token precision rows. Relevant subset: `earlier_snapshot_296.json`; inventory of seven inspected local snapshots: `local_snapshot_inventory.json`.
- That snapshot precedes the 13:05:09 calculation by about 2h23m and its RPC reads are not atomically pinned to the calculation block. It is **not the exact archived calculation state**. The audited earlier-snapshot run matches PEPE/LIMITLESS/PIXYDUST receipts after rounding but disagrees for THREE: **714.67226091 calculated vs 714.66950768 observed** in block 62933497. This is concrete evidence against declaring exact reconciliation from that snapshot, not evidence of a Python arithmetic defect: Python and authority arithmetic agree on the same earlier inputs.
- Exact reconciliation needs the ordered eligible participant state (including shares and fractional timeFactor), batch balances/ticks/exclusions/bonus curve, pool existence/positive shares, params and token precision immediately before the calculation transaction, plus verified runtime numeric settings. Immediate/queued delivery reconciliation also needs recipient order, queue progress and transfer success. Obtain an appropriate archive checkpoint plus deterministic replay of intervening transactions/virtual ticks, or capture verified pre-calculation state prospectively. Neither was available/performed here. Full chain replay was deliberately outside the network/scope budget.

## Deployed authority and numeric comparison
Authority: `/home/tydynrain/hive-agent-test/references/deployed-distribution.json`, version 6, codeHash `6bd095db17d7c2b76ce534b8235b9fcf05c4f9c033bc2d1d87ede518c5dd4178`. Current RPC code was byte-for-byte identical. Audit source copy: `deployed_distribution_code.js`.

Payment transaction `executedCodeHash` is `de3130bb640245ae00005a031356584df1e67ac1b0d8c4b1a9eec00bef55a440`, unlike the individual contract codeHash. Do not infer an upgrade from comparing different hash fields; their relationship and historical runtime/source version were not independently established here.

Compared with `/home/tydynrain/.hermes/scripts/lp_reward_math.py` (unchanged; audit copy `lp_reward_math_audited.py`):

### Arithmetic agreement in tested valid states
Deployed eligibility/age snippets:
```js
timeFactor:{$lte:minHoldTime}, account:{$nin:dist.excludeAccount}
if(timeDiff.lte(3600*params.distTickHours*1e3))return lp.shares;
"string"==typeof dist.bonusCurve.numPeriods
```
Python lines 71–84 exclude accounts, require age >= tick, leave exactly-one-tick shares unbonused, enable bonus only for string numPeriods, and add the capped elapsed-period multiplier. Fractional timeFactor is preserved.

Deployed payout snippet:
```js
payoutShare=api.BigNumber(tr.effShares).dividedBy(shareTotal)
payoutQty=api.BigNumber(payTokens[i].quantity).multipliedBy(payoutShare)
  .dividedBy(dist.numTicksLeft).toFixed(payToken.precision,api.BigNumber.ROUND_DOWN)
```
Python lines 97–99:
```python
share=divide(own,total)
amount=divide(quantity*share,ticks)
rounded=amount.quantize(Decimal(1).scaleb(-precision),rounding=ROUND_DOWN)
```
The order agrees, including intermediate ratio division; bonus changes effective shares, not batch token budget. `tokenMinPayout` is applied to the deployed fixed strategy, **not pool strategy**: its absence from this pool estimator is not a defect.

Offline execution used the actual extracted deployed `getEffectiveShares`, `getPoolRecipients` and `runDistribution` bodies, mocked read-only DB/transfer interfaces and bignumber.js 9.1.2 at default 20 decimal places/ROUND_HALF_UP. No Hive actions or broadcasts were executed. `compare_math.py` ran with `/home/tydynrain/hive-agent-test/.venv/bin/python -B` and exited 0. **12 comparisons: 11 entitlement matches; one intentional caller-precondition mismatch**. Fixtures cover immature/exact-tick/tick+1 ages, fractional timeFactor, cap, numeric curve disabled, excluded denominator, rounded zero, queue delay and earlier live snapshot. Files: `math_fixtures.json`, `offline_authority_results.json`, `math_comparison.json`, `offline_authority_runner.js`.

### Concrete differences / limits (not all arithmetic bugs)
1. **Pool guard outside helper:** deployed `if(!pool||pool.totalShares<=0)return result;`; Python has no pool argument and documents caller responsibility at lines 4–5. Synthetic zero-pool fixture yields no deployed payout and `no liquidity shares for this tokenPair`, but Python predicts 13.94150768 TEST. This is a verified integration precondition gap, not a same-valid-input formula discrepancy.
2. **Entitlement is not transfer timing/success:** deployed `payTxCount<params.maxTransferLimit ? ...payRecipient(...) : ...storePending.push(...)`, then debits the batch for successful or queued payouts. Python returns the conditional amount without queue/order, transfer failure, balance mutation, `numTicksLeft-=1` or scheduling. Queue fixture agrees on quantity while no immediate account payment occurs. Live maxTransferLimit=50; maxDistributionsLimit=1. Pending work is processed only in the no-due-distribution branch.
3. **Scheduling and flush omitted intentionally:** deployed normal scheduler requires active, positive remaining ticks, a balance entry, and `lastTickTime <= now - tick`. Python checks active/ticks but not due-time scheduling. Deployed flush sets numTicksLeft=1 and bypasses the normal scheduler; helper does not model it. Never interpret status `ok` as a scheduled receipt.
4. **Zero-output/status differences:** deployed skips nonpositive rounded quantities and asserts if eligible share total is zero; Python may return zero entitlement entries/status ok with eligible false. Consumers must not record these as payments.
5. **Domain/runtime caveats:** Python rejects negative/future participant age and several malformed inputs, uses Decimal precision 100 for intermediate arithmetic and assumes BigNumber default division settings. Deployed uses JavaScript Number for timestamp thresholds plus the VM-provided BigNumber. Local execution verifies the stated mock/default configuration, not chain VM configuration or universal numerical equivalence. Python source metadata still says upstream/not-deployed parity; this audit does not modify it.

No valid-state arithmetic mismatch was found in the tested cases. No production fix is justified by the stale-snapshot THREE mismatch. The self-check request to use exact archived state cannot be satisfied from the state retrieved; substituting the earlier snapshot would fabricate certainty. Production math remained unchanged.
