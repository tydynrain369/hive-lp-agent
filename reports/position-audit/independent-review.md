# Independent top-up correctness review

## Verdict

Reward arithmetic, all-participant budget depletion, and existing-position timeFactor weighting match the supplied deployed contracts under the stated nominal-tick / successful-settlement assumptions. Independent Fraction re-derivation of **ALIVE:HEARTBEAT, move branch** matches saved payouts, reserves used for liquidation, and terminal value. Two defects found below; neither changes this saved branch's terminal value.

Read-only review of implementation and saved artifacts; no implementation patches or transactions. Only this report was created. Contract hashes in the saved inputs match the supplied references: marketpools v13 `fd6d750418df6b23de96fa0ba8cb006828dcbc272d566c6847204b552a41004c`; distribution v6 `6bd095db17d7c2b76ce534b8235b9fcf05c4f9c033bc2d1d87ede518c5dd4178`.

## Contract comparison

- `lp_reward_math.py:97-99` follows the deployed operation order: `share = round20(effective / total)`, then `floor_precision(round20(balance * share / ticksLeft))`. The mathematical shorthand `floor(balance/ticksLeft * effective/total)` must not be interpreted as an instruction to reorder finite-precision division. Deployed code divides the share first, as implemented.
- `lp_position_scenario.py:29-49` calculates every recipient from the same pre-tick batch balance and subtracts the sum of all token-precision-rounded payouts, not just this account's payout. Zero payouts subtract zero. Each processed tick decrements ticksLeft even if all payouts are zero or no eligible recipients exist. This agrees with deployed `runDistribution`, assuming immediate transfers succeed; queued positive payments are reserved immediately on chain too.
- Eligibility at age exactly one tick, exclusions, and the string-enabled capped bonus curve agree with `getPoolRecipients` / `getEffectiveShares` for valid deployed data.
- `lp_mechanics.py:96-104` agrees with deployed `addLiquidity`: new shares are the minimum of the two proportional mint amounts; `timeFactor = min(round_integer(oldTF + (now-oldTF)*round20(added/(old+added))), now)`. It ages the entire combined position. The simplification of contract abs/max expressions is valid for positive additions and oldTF <= now.

## Independent live-input derivation

Used `fractions.Fraction` and independent integer HALF_UP / floor helpers; did not call audited helpers for the derivation. Saved inputs supply pool, 28 participants, budgets, wallet, token precisions and parameters. The saved result supplies the selected deposit amounts because the input artifact has no explicit deposit-request field. This verifies the selected scenario, not deposit sizing policy.

ALIVE:HEARTBEAT move branch, start `1789669726782`, end `1790274526782`:

| Quantity | Independently calculated / matched |
|---|---:|
| Deposit | 0.0071 ALIVE + 0.0525214 HEARTBEAT |
| Minted shares | 0.01921362145284731327 |
| Weighted timeFactor | 1789117012435 |
| Batch 294 ticks | 7 |
| Own HEARTBEAT rewards | 0.00006688 |
| Own other positive rewards | HASHSCORE 0.000030; TOOFUCKEH 0.00001670; POKEMON 0.00001214 |
| Full withdrawal | 0.1244 ALIVE + 0.92089704 HEARTBEAT |
| Terminal ALIVE swap output | 0.91792615 HEARTBEAT |
| Terminal pair-token value | **3.99452159 HEARTBEAT** |

Assertions passed for all seven effective-share totals, all own raw and floored entitlements, all participants' summed reservations for every budget token at every tick, cumulative own rewards, minted shares/timeFactor, withdrawal amounts and terminal swap/value. First-tick reservations include 5.4941 ALIVE, 0.012 ALIVEM and 5.49450548 HEARTBEAT; budgets were decayed using these independently calculated sums. A separate execution of the current comparison core reproduced the entire saved comparison exactly. Third-token rewards remain unliquidated, correctly suppressing conditional_advantage.

## Concrete defects

1. **Medium — valid active unfunded batches abort comparison.** `/home/tydynrain/.hermes/scripts/lp_position_scenario.py:18,29-31` includes active positive-tick batches without the deployed scheduler's `tokenBalances.0 exists` guard. `lp_reward_math.py:85` then rejects a missing tokenBalances field. Deployed `create` legitimately leaves that field absent, and activation does not require funding; `checkPendingDistributions` skips such a batch. Executed failing input: populated AAA:BBB pool with reserves/shares 100/100/100, mature alice/bob shares 10/90, alice wallet/deposit 1/1, active pool batch with 10 ticks, lastTickTime 0, exclusions [], bonusCurve {}, and no tokenBalances; snapshot 172800000, horizon 259200000, tick 24h. Actual: `ValueError: incomplete or invalid data: 'tokenBalances'`. Omitting this non-schedulable batch yields both terminal values 20.78013815, as expected under no funding. No affected active unfunded batch appears among the four saved participant pools.

2. **Low — returned swap pool_after retains stale volume counters.** `/home/tydynrain/.hermes/scripts/lp_mechanics.py:50-58,71` updates reserves/prices/shares but not baseVolume or quoteVolume. Deployed `updatePoolStats(..., swap=true)` increments both counters. Executed failing-input comparison: the saved ALIVE:HEARTBEAT move terminal swap of 0.1244 ALIVE. Returned counters remain 40869.30285776 / 252118.09893961; deployed updates give 40869.42725776 / 252119.01686576. Any nonzero swap with existing counters exposes this discrepancy. Financial outputs do not use these counters, so this is pool_after metadata parity, not a terminal-value error.

## Limitations

Only one live branch was independently re-derived; other pools/branches were inspected but not independently recomputed. Reference source was read, not executed in a full Hive Engine VM. BigNumber 20-decimal HALF_UP defaults are an explicit supplied assumption, not independently established from VM configuration. Nominal scheduling does not model chain block timing, global maxDistributionsLimit contention, pending-payment latency, failures, or horizon-time wallet availability; eventual settlement is not proof of rewards being liquid at the horizon. External reserves/participants and parameters remain fixed as declared. Sequential RPC input is not an atomic chain snapshot. No realized returns, future funding, future fees, price guarantees or executable top-up recommendation are established.
