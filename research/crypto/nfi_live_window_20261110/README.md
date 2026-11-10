# Live-window native replay — 2026-11-10

## Result

For **2026-11-10 13:00–15:00 Asia/Shanghai** (05:00–07:00 UTC,
end exclusive), both actual native Freqtrade backtests completed successfully.
Neither produced an entry signal. Zero orders in the matching live ledgers
therefore agrees with this retrospective replay.

| Market | Frozen live pairs | Initial USDT / stake budget | Slots / leverage | Raw entry signals | Entry attempts | Native trades | Live orders / trades |
|---|---:|---:|---|---:|---:|---:|---:|
| Spot | 36 | 100 / 99 | 6 / 1x | 0 | 0 | 0 | 0 / 0 |
| Futures | 80 | 100 / 99 | 12 / isolated 3x | 0 | 0 | 0 | 0 / 0 |

Each pair has exactly 24 native execution rows, from 05:00 through 06:55 UTC.
There are 864 spot and 1,920 futures pair-candle evaluations. The inspected
close, RSI_14, EMA_200, RSI_14_1h, EMA_200_1d and BTC_RSI_14_4h fields are
nonmissing on every signal row. Native wallet archives independently contain
the same 24 execution clocks and finish at 100 USDT on each side.

This establishes the absence of replay entry signals in this window. It does
not establish a successful first live order or fill, or complete live/backtest
equivalence under intrabar prices, latency and order-book conditions.

## Native implementation and evidence

- Owning engine: Freqtrade `Backtesting.start()`, upstream source baseline
  `c051e9065c5b677f03817dd1f83cb980af1ed684`; the deployed capital cap is a local
  extension. This is not a Vibe-Trading SignalEngine/AlphaBench result.
- `WindowReplay.py` is explicitly a local backtest-only startup adapter. It
  inherits the deployed strategy's financial callbacks, including entry/exit,
  stake, DCA, leverage and pool admission. It replaces only live startup
  admission, with a BACKTEST and dry-run guard.
- `protocol.json` freezes the window, account contracts, exact pair lists,
  source identities and modeled costs before results. Futures uses the actual
  daily 80-pair pool snapshot available before this window.
- `prepare.py` supports native input preparation: existing historical warmup
  plus actual public 5m candles, mark candles and funding records. Public
  requests use the shared Binance request guard and sequential pacing.
- `run.py` invokes the native lifecycle with result caching disabled and
  outbound sockets blocked during replay. Its counters observe native
  financial execution without replacing the accounting engine.
- `comparison.json` records native archive/result hashes, coverage checks and
  read-only live ledger counts. `report.py` verifies registered source hashes,
  live config hashes, native rows and wallet archives before producing it.
- `execution-status.json` retains the process exit codes. The initial metadata
  parser and configuration validation failures are preserved as technical
  failures, not economic results. Both final native runs exited zero.

Large raw inputs and native exports remain local, under the ignored directory
`user_data/crypto_quant/nfi_live_window_20261110_v001/`. The authoritative
archives are in `spot/native-run-verified/native-results/` and
`futures/native-run-verified/native-results/`; their exact paths and SHA-256
digests are in `comparison.json`. Earlier successful native runs are retained.

## Boundaries and limitations

The native engine trims and shifts signal rows. A 04:55 UTC support bar is
included to obtain exactly the requested 05:00–06:55 execution rows. Source
end-boundary warnings were checked against actual execution coverage. Some
young assets have less than 800 daily candles; inspected indicators are
nevertheless populated in this window. Empty funding intervals are retained
as such; synthetic zero events are not inserted.

Costs retain frozen all-in models of 13 bps per side for spot and 8 bps for
futures, rather than certifying charged live commissions. Current public
symbol rules use existing frozen leverage brackets. With no entry signals or
trades, neither costs nor fill assumptions create this zero-entry result.

No live workers were restarted, no orders were forced, no paper workers were
started, and live account configurations were unchanged. All crypto paper
trading remains stopped.

## Reproduce the evidence report

With the retained native artifacts present, from the repository root:

```sh
.venv-paper/bin/python -B -m research.crypto.nfi_live_window_20261110.report
```

The intake and native run directories are immutable attempts: do not overwrite
them to rerun research. Freeze a new attempt directory and protocol instead.
