# Original 40-pair head-and-shoulders source admission

The source-recognition diagnostic passed for all original 40 ordered pairs. This directory contains **no completed native account and no market return calculation**. It is a source-admission artifact for a subsequent Freqtrade integration, with overlap, allocation and execution policy still unregistered.

## Frozen source and rules

The author repository is [neurotrader888/TechnicalAnalysisAutomation](https://github.com/neurotrader888/TechnicalAnalysisAutomation/tree/da99c20bf3d977b639451258cd6cfca9baa1dcc3), commit `da99c20bf3d977b639451258cd6cfca9baa1dcc3`, MIT. `source_receipts.json` records exact downloaded bytes, URLs and SHA256 values for `head_shoulders.py`, `rolling_window.py`, `LICENSE` and `README.md`.

The recursive GitHub tree request returned HTTP 403 (rate limit). Its URL and complete error body are retained in `tree_api_receipt.json`; the full tree is **not verified**. The four raw files individually matched the independently fixed hashes.

`SourcePatterns.py` loads eight original class/function AST nodes without rewriting them: `rw_top`, `rw_bottom`, `HSPattern`, `compute_pattern_r2`, `check_hs_pattern`, `check_ihs_pattern`, `find_hs_patterns`, and `get_pattern_return`. Source hashes fail closed. Plotting, imports and the author's executable example are not run. The local module supplies the original dependencies and disables inherited compiler future flags.

Recognition uses natural-log close, `order=6`, and `early_find=False`, exactly as the author's example. All returned normal and inverse patterns are retained. R-squared is recorded without filtering. The pre-diagnostic protocol explicitly selects the author's `stop_return` rule; the example also contains a different `hold_return` calculation, which is not selected. No thresholds, filters or variants were chosen from results.

The source stop is the right shoulder; target is the confirmation neckline plus/minus head height. Crossing is strict (`>` / `<`), so equality does not exit. Its observation loop includes `break_i` and ends at `break_i + head_width - 1`. These are source event rules, not exchange stop orders or intrabar high/low execution. The forward-looking return helper was called only on synthetic boundary fixtures and never on market data.

## Inputs, chronology and results

`protocol.json` and `implementation_freeze.json` were written before the market-pattern diagnostic. They freeze the original ordered pool from `strategy_comparison_wide_20261105/plan.json`, all 40 input hashes, implementation hashes, parameters and checks. All candles are the original `nfi40_data_v002/candles/futures/*-1h-futures.feather` inputs.

| Check | Observed result |
|---|---:|
| Original ordered pairs | 40 / 40 |
| Continuous UTC hourly rows per pair | 27,048 |
| Calendar | 2023-08-01 00:00 through 2026-08-31 23:00 UTC |
| Total input rows | 1,081,920 |
| All source events retained | 1,891 |
| Normal / inverse events | 984 / 907 |
| Independent geometric checks | 1,891 passed |
| Pre-registered prefix and future-shock cases | 120 passed, each with both comparisons |
| Synthetic strict crossing / expiry / tail fixtures | 16 passed |
| Self-terminal at confirmation | 30 retained |
| Expiry horizon outside input snapshot | 0 |
| Nonfinite R-squared / same-pair same-bar events | 0 / 0 |
| Peak process RSS | 123,830,272 bytes |
| Completed native accounts | 0 |

Each source event is dated by `break_i`, with earliest signal availability at that candle's **close**, not the earlier extremum or pattern-start timestamp. Independent assertions reconstruct confirmed extrema, right shoulder, head/shoulder balance, time symmetry, first neckline crossing, neckline/target/stop, confirmation price, expiry and self-terminal flags.

For every pair, complete pattern records before each of three frozen cutoffs are exactly identical when processing a truncated prefix and when multiplying all future OHLC prices by four. Cutoffs are 2025-01-01 00:00, 2026-01-01 00:00, and 2026-08-22 00:00 UTC (before the last 240 candles). This is coverage of those three cutoffs per pair, **not an exhaustive test of every possible prefix**. The 30 events that already cross a source stop or target at confirmation remain explicit events; no filled account trade is inferred from their zero source duration.

The output retains observation-coverage and unevaluated-outcome fields. No event in this snapshot has an expiry horizon beyond the observed data. This does not assert a realized exit or profit: all market outcomes remain unevaluated. The synthetic incomplete-tail fixtures return NaN as the author function specifies.

`source_events.parquet` is 348,282 bytes, SHA256 `1603ca0759de09c70a9fcbca363a3f69230573017f39fef2519fa52bc07ce157`. Its columns are documented in `pattern_metadata.json`. `source_coverage_validation.json` contains per-pair input hashes and checks; `source_validation.log` records all 40 completed pairs. The validation process was observed to exit 0.

## Limits and next admission boundary

The local environment is Python 3.12.14, NumPy 2.5.4, pandas 3.0.6 and pyarrow 26.0.0. The author's README suggests NumPy 1.23.1 for a separate pyclustering dependency. This admission uses only the selected head/shoulders definitions and does not claim the author's complete environment has been reproduced.

The author supplies pattern recognition and isolated forward return calculations, not a shared-wallet portfolio. Overlapping patterns, same-pair position conflicts, native order timing, expiry when entry is delayed, and allocation across the 40 pairs require a separately frozen native policy before any account run. No return has been computed here and nothing is ranked against the old NFI X8 40-pair results. Orders and deployment remain disabled; the broader research goal remains active.

Reproduce the diagnostic from the frozen implementation with `.venv-paper/bin/python -B -m research.crypto.head_shoulders_source_native_20261117_v1.validate_source` from the Freqtrade repository, using a new output directory/version: the original runner refuses to overwrite its event and validation artifacts. Retain the original source, protocol, receipts and log.
