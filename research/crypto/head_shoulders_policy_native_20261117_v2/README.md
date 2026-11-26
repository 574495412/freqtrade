# Head/shoulders native interface admission

The actual Freqtrade constructor, exchange configuration validation, DataProvider, signal conversion and callback checks passed for the original ordered 40-pair pool. The process exited 0. **The 1,014 confirmed entry masks are signals, not fills or completed accounts: no `Backtesting.start()` was called, and no order or fill was created.** This directory is the frozen interface stage; financial accounts belong to a separate later study.

## Ownership and frozen policy

The native owner is `/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade`, commit `c051e9065c5b677f03817dd1f83cb980af1ed684`. All 595 official source files matched its manifest before and after the probe. `HeadShouldersStrategy` is a local supported `IStrategy` extension, not an upstream strategy. No main module or exchange capability was modified.

Source recognition remains the MIT author implementation from [neurotrader888/TechnicalAnalysisAutomation](https://github.com/neurotrader888/TechnicalAnalysisAutomation/tree/da99c20bf3d977b639451258cd6cfca9baa1dcc3), fixed by the sealed sibling `head_shoulders_source_native_20261117_v1`. Its 21 sealed files remain unchanged. The v1 source loader and original source functions are hash checked and used again inside `populate_indicators`: log close, order 6, `early_find=False`, source `stop_return` rules, no R-squared filter or parameter search. The old event Parquet is not a signal cache. It was read only after this independent native recomputation for exact artifact reconciliation.

`protocol.json`, `native_config.json` and all implementation hashes were frozen before the policy probe. This local policy fixes the following behavior:

- Original ordered 40 pairs, 1x leverage, six native position slots, no position stacking, DCA, ROI or trailing stop.
- Each source event is emitted only on its confirmation candle. The native converter shifts its signal and tag once to the next 1h open; `confirm_trade_entry` accepts only that exact first clock. Repeated events and later 5m retries are rejected.
- A same-pair position already open when the event becomes available blocks that event. A position closed at the same clock also blocks it, preventing the native short-enabled reverse-entry second loop from reopening on an overlapping event. The old position's source metadata remains bound to its original event.
- Simultaneous same-pair source events are an error, with no selected direction priority. Known self-terminal source events remain in the event catalog with explicit flat masks.
- A 44-character event ID binds `trade.enter_tag` to the original direction, stop, target and expiry. Event IDs contain no return or later exit outcome.
- The source stop and target use strict completed 1h **close** comparisons; touching a threshold does not trigger. Source expiry is `break_i + head_width - 1`, available only when that hour closes. A newly known condition is available at the next native allowed 5m open, including the same whole-hour boundary; no extra five-minute delay is inserted. These interface checks do not establish actual fill prices.
- Callback reads explicitly retain only `date + 1h <= current_time`. Missing or stale completed history and invalid trade bindings produce a distinct `hs_policy_error_exit` plus an error receipt. Entry exceptions explicitly return False because the native safe wrapper otherwise defaults to True. Any callback error must invalidate a later financial run.
- The native -99% catastrophe stop and liquidation remain separate native risk exit types. They are not presented as author source exits.

The supported indicator callback uses `startup_candle_count=0`. It verifies the frozen original hourly file, prepends only dates before the native frame, and computes the original recognition from 2023-08-01. Every native OHLCV row must match the frozen history exactly. The native frame starts 2024-12-31 23:00 UTC, allowing the first shifted execution boundary at 2025-01-01 00:00 UTC. Future on-disk rows are never prepended.

## Observed interface results

| Evidence | Result |
|---|---:|
| Original pairs in native data / pool order | 40 / 40 |
| Native 1h rows loaded | 583,720 |
| Native 5m rows loaded | 7,004,640 |
| Combined native funding/mark rows | 78,745 |
| Original source events regenerated | 1,891 |
| Native-window source events before masking | 1,031 |
| Native-window self-terminal masks | 17 |
| Native shifted entry masks and no-position confirmations | 1,014 |
| All self-terminal events retained, including prehistory | 30 |
| Non-self-terminal cold-history-only events | 847 |
| Synthetic native callback cases passed | 38 |
| Peak process RSS | 708,313,088 bytes |
| Native orders / fills / completed financial accounts | 0 / 0 / 0 |

The regenerated event catalog matches the sealed v1 raw source fields **exactly**, including all 1,891 events and their pair order. `artifact_validation.json` records that post-probe reconciliation, all 30 masked IDs, and the policy status counts. The full catalog is `native_probe/policy_event_catalog.parquet`, SHA256 `92192539203c9f431ffdc9472ebdbed5c81a833348939f479e598dac6ad12fb4`.

`native_probe/policy_audit.json` preserves the 1,014 actual entry-callback decisions with source IDs and clocks. They were evaluated with no open positions and are not orders. Thus real-position overlap count is zero in this interface probe; overlap rejection is covered by the named synthetic case receipts, not inferred from market positions.

The callback receipt schema is `fixture_kind=SYNTHETIC_NATIVE_CALLBACK_INTERFACE_ONLY`, with `case_count=38` and a `cases` array containing each case name, `passed`, expected reason/error count or native exit type where relevant. It records the actual native provider and strategy classes, 36 detached `LocalTrade` argument objects, zero created orders/fills/wallets, and restoration of all temporary native trade globals. The cases cover:

- Both directions: strict target/stop equality and crossing, expiry at the width-minus-one candle's close, future-row isolation, exact/early/late/repeated entry clocks, and self-terminal masks.
- Empty, stale, interrupted and stale-after-seen close history; delayed callbacks preserving the first eligible crossing; backward clocks unable to read an already cached future exit.
- Existing positions, same-clock closed positions and loop-start overlap blocks; unknown tags, wrong direction/pair, nonmarket entries and missing confirmation rows.
- Safe-wrapper entry exceptions, invalid exit bindings, and native `should_exit` classification of source, independent policy-error, -99% stop-loss and liquidation reasons.

The fixtures use synthetic interface records, not claimed author-recognized market patterns, and do not calculate a market strategy return. The original all-40 source chronology tests remain in v1.

## Remaining financial boundary

The probe used the fixed public Binance market snapshot observed on 2026-11-05. It explicitly **does not certify dated historical eligibility, precision, amount limits or suspension rules**. Native data loading also constructs its ordinary precision history, which is not accepted here as an economic contract. No new return is compared with or claimed to exceed the old 40-pair NFI baseline.

`social_receipts.json` records the parent's primary-author YouTube metadata/description checks: [head-and-shoulders video](https://www.youtube.com/watch?v=6iFqjd5BOHw) and [extrema algorithms video](https://www.youtube.com/watch?v=X31hyMhB-3s). No video viewing or full transcript is claimed. The author's description of an early inverted variant did not change the frozen `early_find=False` policy. No matching primary X post or executable Douyin rule source was found in that search.

The next financial stage must separately freeze costs, capital, funding ownership, historical rule treatment and account audit. It must reject every callback error and separately reconcile source exits, native risk exits and forced closeout. Orders and deployment remain disabled; the overall research goal remains active.
