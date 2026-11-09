# Historical daily universe validation

**Current completed version: v003.** v002 ran out of disk while copying full temporary indicator caches, before trading replay began. Its inputs, log, plans and generated-file inventory are retained; only its generated cache was removed. v003 uses the same native engine and schema extension, computes native trimming lazily, and releases each source frame after the native provider and exact row blocks retain it. It requires non-signals export, a free-disk guard and another full fixed80 parity run before the four economic accounts. Results are under `v003/REPORT.md` and `v003/comparison.json`.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi80_historical_universe_20261109.v003.run
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi80_historical_universe_20261109.v003.final_analysis
```

**Preserved schema revision: v002.** The first dynamic invocation failed native schema validation before account replay because its plugin name was not registered. `build_v002.py` adds only that name to the isolated native `AVAILABLE_PAIRLISTS`; the v001 engine, failed attempt and contracts remain intact. The v002 actual native schema and resolver preflight passed at three historical boundaries. The new manifest records the additional constants registry change; financial backtesting modules and lossless storage match the exact fixed80 parity control. Current results are written under `v002/REPORT.md` and `v002/comparison.json`.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B research/crypto/nfi80_historical_universe_20261109/v002/preflight.py
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B research/crypto/nfi80_historical_universe_20261109/v002/run.py
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi80_historical_universe_20261109.v002.analyze
```

This study answers whether replacing the stale fixed80 basket with a daily historical top80 basket improves the selected NFI account. Read `PROTOCOL.md` for the frozen hypothesis, clocks, costs and 30% drawdown gate, and `REPORT.md` / `comparison.json` for completed audited results when available.

## Owner and extension boundary

The financial engine is pinned upstream Freqtrade `c051e9065c5b677f03817dd1f83cb980af1ed684`, with NFI X8 `1af55971d3a64703ec449078e09fdec257d0d3c6`. This is an execution/universe extension to the existing native strategy workflow, not a new factor or a separate portfolio simulator. The upstream `VolumePairList` is not backtestable. `HistoricalVolumePairList.py` is explicitly local and runs through the existing native PairListManager and Backtesting lifecycle. `native.patch` exposes the replay clock, sets it before selection and prevents fresh entry signals for held symbols outside the active universe. The unchanged native held-first loop retains their position management.

`engine-manifest.json` pins all copied upstream files, names the two modified modules and the one added plugin, and retains the original GPL source/license. The isolated engine lives under the ignored research output, leaving installed paper engine bytes unchanged. `native_runner.py` verifies the manifest, all inputs and dependencies before and after replay, blocks network access during financial replay, calls actual native Backtesting, exports its native archive and independently reconciles fill cash with Decimal. Existing lossless DataFrame storage and 5-minute mark observation are reused. `row_storage.py` retains the exact native generated lists in verified lossless blocks, without modifying signals, numeric precision, matching or wallets; an exact old fixed80 equity/trade parity control must pass first.

## Inputs

`prepare.py` downloads official public daily klines, retaining actual quote-asset turnover and raw close timestamps, and freezes a schedule before new strategy returns. Each decision sees only the previous completed UTC day, not the current ticker or a full-year ranking. The author set, blacklist, current market rules and 60-observation age condition are explicit. All 313 schedules, daily source hashes, rankings and union membership live in the ignored output; `schedule-summary.json` pins that evidence. The initial ordered list exactly matches the old fixed80 baseline, so both comparisons start from the same basket. This conditional test still has retrospective author/metadata/survivorship limitations.

Old80 candles are immutable hardlinks with all hashes checked against the frozen baseline. Additional symbols use the existing checksummed archive/public API preparation, native funding and complete marks, plus the available native 800-day daily lookback. No missing data are synthesized or zero-funded. Public input I/O uses six threads; financial workers remain one. Partial input attempts and a restarted technical launcher are retained, never recast as successful return accounts.

## Execution and audit

The following commands operate on this already frozen version. Do not overwrite completed evidence or change strategy parameters to rescue a failure; use a separately frozen revision for a new study.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi80_historical_universe_20261109.prepare schedule
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi80_historical_universe_20261109.prepare execution
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi80_historical_universe_20261109.native_extension
.venv/bin/python -m pytest research/crypto/nfi80_historical_universe_20261109/test_historical.py -q
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi80_historical_universe_20261109.study register
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi80_historical_universe_20261109.study run
PYTHONDONTWRITEBYTECODE=1 .venv-paper/bin/python -B -m research.crypto.nfi80_historical_universe_20261109.analyze
```

The already started technical parity control uses the immutable old80 input root while remaining public inputs finish; its frozen technical plan is retained and reused by the serial orchestrator. Actual process exit codes, native archives, complete marked curves, accepted initial order timestamps, day-by-day actual pairlist traces and serialized block recovery receipts are required. Each source/config/input is hashed. New initial orders must have been accepted inside that day's pool; prior accepted orders can fill later under native timeout semantics. Removed held positions can continue DCA and exit; these fills are counted separately. No completed terminal forced exit is relabeled a natural trade. Fees/slippage remain the same 15/8bps model proxies, not orderbook execution evidence.

The mistakenly added forward daily paper experiment has been stopped and disabled, with zero trades/orders and preserved backups. Its shutdown receipt is in `deploy/nfi80_daily/stopped-receipt.json`. Older fixed80 and earlier paper accounts were not changed. There is no order authorization or deployment in this research study.
