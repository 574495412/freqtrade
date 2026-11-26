# NFI40 execution-jump audit

The largest saved hourly NAV jumps are explained by exported fills at successive 5-minute OPEN prices during the recorded October 10 rebound. This audit found no same-campaign or same-pair same-bar entry/exit cycle and no unexplained cash-ledger jump. This is evidence about the fixed historical native model, not certification of real fills, intrahour risk or corrected historical execution rules.

Both accounts retain the original ordered 40-coin NFI X8 source, 1x isolated futures, six slots, 15 bp per side and 2025-01-01 through 2026-08-31. The original 1,000 USDT and newly completed 500 USDT accounts are separate actual native archives, not a scaled account. No new `Backtesting` instance, financial run, strategy change or active 8 bp process interaction occurred.

## Largest hour: 2025-10-10 21:00–22:00 UTC

Snapshots precede processing at their own clock. The 22:00 row therefore includes fills before 22:00 and has zero open positions in both accounts.

| Reconciliation, USDT | Original 1,000 | New 500 |
|---|---:|---:|
| Marked NAV at 21:00 | 1,552.90845495 | 768.95982890 |
| Marked NAV at 22:00 | 1,794.92175085 | 885.74310809 |
| Net change | **242.01329590** | **116.78327919** |
| Hourly return | **15.58451788%** | **15.18717556%** |
| Marked price-PnL change | 248.68480529 | 120.08486000 |
| New fees paid | −6.96234516 | −3.39068663 |
| Release of prior estimated exit-fee reserve | +0.29083576 | +0.08910581 |
| Native-model funding accrual change | 0 | 0 |
| Closed realized PnL increase | 255.36322279 | 119.63537421 |
| Prior marked open PnL, already in 21:00 NAV | 13.34992689 | 2.85209502 |
| Open campaigns at 21:00 / 22:00 | 2 / 0 | 1 / 0 |

The alternative identity is closed-realized change minus prior marked open PnL, because the hour ends flat. Sum-of-campaign residuals are −7.67e−9 and −8.19e−9 USDT. The reserve release is part of the saved marked-NAV convention; it is not an extra exchange cash inflow.

| Campaign contribution to this hour, USDT | Original 1,000 | New 500 | Native close UTC |
|---|---:|---:|---|
| AAVE short already open | 5.643708 | — | 21:15 |
| BCH short already open | 16.145432 | 7.965080 | 21:20 |
| LTC long | 8.847758 | 4.365487 | 21:30 |
| AVAX long | 20.005503 | 10.002752 | 21:35 |
| SAND long | 18.845291 | 9.300639 | 21:35 |
| GRT long | 18.493037 | 9.124080 | 21:35 |
| GALA long | 11.670784 | 5.758917 | 21:40 |
| ADA long | 61.248297 | 30.242029 | 21:45 |
| RUNE long | 32.485606 | 16.028518 | 21:45 |
| THETA long | 48.627880 | 23.995778 | 21:45 |

The eight new longs enter at 21:25, 21:30, 21:35 or 21:40 and exit one or two bars later. Examples: ADA 21:40 OPEN 0.4856 → 21:45 OPEN 0.5964; THETA 0.3568 → 0.4216; RUNE 0.718 → 0.806. No inferred intra-bar low purchase or earlier high sale is needed to explain these exported prices.

ADA/THETA/RUNE supply 58.82% and 60.17% of the two hourly jumps. The jumps are 17.84% and 18.01% of each account's full-window net USDT profit. These are concentration descriptors, not returns from a hypothetical account with that event removed.

## Complete exported-fill and calendar checks

| Check | Original 1,000 | New 500 |
|---|---:|---:|
| Closed campaigns | 282 | 282 |
| Exported fills | 690 | 683 |
| Entries equal their corresponding 5m OPEN | 361 / 361 | 359 / 359 |
| All fills, including exits, equal corresponding 5m OPEN | 690 / 690 | 683 / 683 |
| Fills outside corresponding low/high | 0 | 0 |
| Missing fill bars / off-5m fill clocks | 0 / 0 | 0 / 0 |
| Same campaign or same pair entry+exit at one 5m clock | 0 | 0 |
| Complete same-5m cycles / their profit | 0 / 0 USDT | 0 / 0 USDT |
| Account clocks with entries and exits in different pairs | 11 | 11 |
| Maximum per-campaign Decimal cash error | 9.62e−9 USDT | 1.20e−8 USDT |
| Maximum error across 18 selected hourly NAV snapshots | 3.47e−8 USDT | 7.29e−8 USDT |

All 40 instruments have every one of the expected 175,104 five-minute timestamps in the full account window: zero missing or duplicate timestamps, zero off-grid rows and zero invalid OHLCV rows. The detailed 20:00–23:55 window has 48 rows per instrument and no zero-volume rows. These are consistency checks on the frozen data, not proofs of executable order-book liquidity.

An additional independent query through the Binance public USDⓈ-M kline connector returned ADA, THETA and RUNE over this four-hour window. All **144 bars / 720 numeric OHLCV fields** exactly match the local data as Decimal values. This verifies those three public OHLCV series, not all 40 against an external source, and not tick paths, queue position or strategy execution. Raw responses and parameters are preserved in `binance_public_ohlc_raw.json`; the comparison is in `binance_public_ohlc_comparison.json`.

The selected snapshots also cover each account's five largest positive and three largest negative hourly returns, including February 3 at 04:00/06:00 UTC. Those changes reconcile to the exported-fill/mark ledger at the errors above. The audit does not relabel these 18 selected points as a new full-account simulation.

## Native source semantics and remaining uncertainty

Pinned official `backtesting.py` lines 791–793 accept a limit price when it lies inside the bar's low/high range. Lines 1566–1579 process entry fills, then exits, within the same native row. `_check_trade_exit` passes current bar OPEN to source exit evaluation; CUSTOM_EXIT normally returns OPEN, and exit price uses the trade's saved precision. Original X8 does not override `custom_entry_price`, `custom_exit_price`, `adjust_entry_price` or `adjust_order_price`; inherited entry and exit hooks return the proposed rate. See `source_matching_review.json` for source SHA and exact owner pointers.

For a hypothetical lower-limit entry followed by same-bar OPEN exit, OHLC cannot prove the required order of price visits. That general matcher limitation is real, but **the reviewed two archives contain zero such cycles and all fills are at their bar OPEN**. Inferring that it caused this jump would contradict the exported evidence. The result also does not independently certify every upstream indicator's causality.

Real fill feasibility, latency, spreads, queueing and slippage during the violent rebound remain unverified. Minified archives omit order-creation timestamps. Each account has five funding-boundary fills elsewhere in its window; the conditional native funding reconstruction agrees with terminal funding, but ambiguous deferred/DCA boundary paths remain UNKNOWN in this audit. No funding change drives the largest hour.

The hourly-mark maximum drawdowns, 4.5154% and 4.4733%, are **hourly sampling statistics**. They do not represent worst drawdown between snapshots, tick-level equity or live execution. The previously recorded whole-month precision, historical universe eligibility and historical rule gaps remain unchanged. No promotion or higher-return claim follows from this audit.

## Reproduction and retained failure

`audit_jumps.py` and its first freeze/log remain intact: the first diagnostic failed because `Timestamp.value` still returns nanoseconds after `as_unit('ms')`. `audit_jumps_v2.py` changes only that scalar conversion to integer ns/1,000,000 and the freeze filename. It completed with actual process exit 0; the public OHLC comparison also exited 0. The first error produced no result tables. Neither attempt constructs or imports a native backtester.

Key artifacts: `results.json`, `hourly_campaign_contributions.csv`, `all_native_fills_with_bars.csv`, `oct10_native_fills.csv`, `oct10_5m_prices.csv`, `oct10_hourly_marks.csv`, `selected_snapshot_reconciliation.csv`, `calendar_audit.json`, `funding_boundary_fills.csv`, `source_matching_review.json`, `validation.json`, both diagnostic freezes and the retained failure log. Inputs bind both native ZIPs/curves, source, official owners and all 120 price/mark/funding files by SHA256. The final artifact manifest seals all new outputs.
