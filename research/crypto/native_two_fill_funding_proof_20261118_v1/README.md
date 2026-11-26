# Detached native two-fill funding proof

The **object-level mechanical proof passed**: 16 directional cases and 68 hourly snapshots matched the frozen arithmetic expectations. The command actually exited0, with a peak RSS of 277,905,408 bytes on macOS. This is not a complete native backtest, matching trajectory, account audit, exchange settlement test, or strategy-return result.

The fixture verified all **595 files** of the pinned official Freqtrade vendor tree before import (`c051e9065c5b677f03817dd1f83cb980af1ed684`). It constructed **16 detached real `LocalTrade` and 32 real `Order` objects**. It called native `Order.close_bt_order`, `LocalTrade.recalc_trade_from_orders`, `LocalTrade.close`, and the real `Backtesting._run_funding_fees` bound to a minimal `SimpleNamespace`; that method invokes native `set_funding_fees`. Actual funding frames and rate calculations used `Exchange.combine_funding_and_mark` and `Exchange.calculate_funding_fees`.

There were **zero Backtesting, Wallet or Exchange instances and zero Backtesting.start calls**. Constructor/start/network guards would raise on accidental use. Native account-global collections and totals remained unchanged. No market data was loaded, no financial account was run, and no execution release was created. The 32 filled order objects are synthetic object state, not account or exchange fills.

## Frozen cases and result

All cases used quantity2, leverage1, and deliberately synthetic funding units at minutes0/60/120/240 of `1/-2/3/4`. Long funding is negative the quantity-weighted sum; short funding reverses the sign.

| Scenario | Entry minute | Exit minute | Expected long terminal funding |
|---|---:|---:|---:|
| Entry on funding slot | 0 | 125 | -4 |
| Exit on funding slot | 30 | 120 | -2 |
| Entry equals exit on slot | 60 | 60 | +4 |
| No funding slot | 5 | 55 | 0 |
| Exit five minutes after hour | 60 | 125 | -2 |
| Signed rates at both boundaries | 0 | 240 | -12 |
| Force-close final base row after full detail loop | 60 | 240 | -10 |
| Entry equals exit without slot | 30 | 30 | 0 |

Each scenario ran long and short. Final funding matched `sign*q*sum(E<=F<=C)`. Every recorded hourly observation ran before that clock's fills and refresh and matched `sign*q*sum(E<=F<T, F<=C)`.

The preserved sequence is material: entry fill → forced funding → final entry recalc → same-clock ordinary funding refresh → exit fill transfers running funding → forced refresh → closing recalc. For the same-slot long case, the trace shows total funding **4 → 0 → 4 → 8 → final4**. The transient double count after the exit's forced refresh is removed by native closing recalc; it must not be substituted for final funding.

The force-close fixture first processed the complete five-minute sequence through minute295, then closed using the final base-row timestamp240. That proves this fixed whole-hour tail with no later funding event; it does not establish an arbitrary tail policy.

Observed calls: `_run_funding_fees`370, `close_bt_order`32, explicit entry `recalc_trade_from_orders`16, `close`16. Native internal entry/closing recalculations are additional to the explicit count.

## Evidence and limits

The scheme and code were frozen before execution. The actual command was:

```text
.venv-paper/bin/python -B research/crypto/native_two_fill_funding_proof_20261118_v1/prove_objects.py
```

The original run completed in session58281 with exit0. No failed fixture occurred. `validation.json` contains every case and snapshot; `native_object_trace.json` preserves intermediate stored/running/total funding. `validation.log` is the actual process output. A separate read-only review checked the caller sequence before freezing; it did not execute an account or replace this probe.

Freeze SHA: `5ac34bef400821e07b7349689873c05d5a6251c09fec15a254136c0399524fd7`.

Wallet updates, order matching, liquidation calculations, strategy callbacks and trade-global registration were intentionally outside this object fixture. The fixed H&S policy has no funding-mutating callback, but omitted paths are not certified by this result. NFI DCA, missing AXS/TRB rates, historical precision uncertainty, actual future H&S account results, and risk-price certification retain their previous status. No existing sealed research file was modified.
