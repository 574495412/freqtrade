# Recent live-window native replay, v002

Window frozen before returns: **2026-11-10 15:50–17:50 Asia/Shanghai**, or
07:50–09:50 UTC, end exclusive. It was the latest two hours of closed 5m bars
when this attempt began. This new attempt preserves the earlier 13:00–15:00
study and appends actual public data to its hash-verified inputs.

| Market | Pairs | Raw signal rows | Native entry attempts / trades | Live orders / filled orders |
|---|---:|---:|---:|---:|
| Spot | 36 | 1 short, 0 long | 0 / 0 | 0 / 0 |
| Futures | 80 | 2 short | 1 / 1 | 1 / 1 |

The spot short column is diagnostic output, not an actionable spot entry:
both native live and backtest market gates disable spot shorting. Futures has
two adjacent DOT short signal rows, belonging to one new trade, not two fills.
Native entry is **17:05:00**, DOT short, tag **562**, 3x; live ledger/logs show
**17:05:59**, same symbol/direction/tag/leverage. The native live ledger reports
8.8 DOT filled at 0.8495 USDT, stake 2.4918667 USDT, still open at report time.
An earlier attempt at 17:05:48 received exchange `-4192` (cooling-off period),
then the 17:05:59 order was created and subsequently reported filled. This is
distinct from an IP HTTP418/429 rate-limit event.

## Match and material difference

The answer to “would this strategy open a trade?” is **yes for futures DOT,
and the actual live ledger agrees**. Spot correctly remains without a trade.
This is signal-level agreement, not complete execution or return parity.

Native backtest quantity is **7.2 DOT**, stake **2.04024 USDT**, entry price
0.8501, whereas live is **8.8 DOT**, stake **2.4918667 USDT**, entry 0.8495.
The native engine uses **-0.05** for the initial minimum-stake reserve in
`Backtesting.get_valid_entry_price_and_stake`, while the live bot passes the
strategy's **-0.99**. With DOT minimum notional 5 USDT and 3x leverage, the
actual native minimum-stake method returns **1.8421053 USDT vs 2.5 USDT**.
The strategy's minimum-stake clamp therefore differs, before quantity rounding.
Price/time differ additionally because a 5m historical open is not a live
order-book limit/fill. This finding is retained; no engine or live parameter
was changed to make the outputs match. See `allocation_probe.py` and
`allocation-diagnostic.json` for the offline native method check and source hashes.

The native window-end forced exit is hypothetical: it produces **-0.0263664
USDT**, or **-0.0263664% of initial 100 USDT**, using the frozen 8bps-per-side
cost model. It is not an actual live exit or a certified live net return.
One short-window trade cannot establish win rate or strategy profitability.

## Engine, data and operational boundaries

- Actual Freqtrade `Backtesting.start()` is the primary financial engine,
  upstream baseline `c051e9065c5b677f03817dd1f83cb980af1ed684`. The local capital
  extension and backtest-only startup adapter are explicitly local features.
- The same deployed financial strategy methods are inherited. Accounts start
  empty, as independently checked at window start, with 100 USDT equity and
  99 USDT trading budget. Spot has 6 slots/1x; futures 12 slots/isolated 3x.
- All 116 pairs have 24 native execution clocks, 07:50 through 09:45 UTC.
  Inspected main/1h/1d/BTC4h indicators are populated on all signal rows.
  Historical warmup is retained; only fully closed public bars enter inputs.
- Anonymous public input requests reuse the installed cross-process Binance
  guard and are paced sequentially. No 418/429 retry loop is introduced.
  Final shared guard strike count remains 1, the same historical strike.
- Each native replay disables result caching and blocks outbound sockets.
  Current public symbol rules use frozen existing leverage brackets; per-side
  fees remain modeled (13bps spot, 8bps futures), not actual fee certification.
- Native live ledger and worker logs are the live evidence here; no additional
  authenticated exchange fill/position reconciliation was performed.
- No live restart, forced/test order, paper worker or configuration change.

Official assumptions:
[Freqtrade backtesting](https://www.freqtrade.io/en/stable/backtesting/#assumptions-made-by-backtesting).

## Evidence and reproduction

`protocol.json` freezes window/cohorts/source hashes before input preparation.
`comparison.json` verifies native archive/source/config/coverage evidence and
contains the read-only matching live ledger snapshot. `execution-status.json`
retains actual process completion. Large data/raw responses/native exports
remain under ignored `user_data/crypto_quant/nfi_live_window_20261110_v002/`.
The native archive paths and SHA-256 digests are recorded in `comparison.json`.

```sh
.venv-paper/bin/python -B -m research.crypto.nfi_live_window_20261110_v002.report
.venv-paper/bin/python -B -m research.crypto.nfi_live_window_20261110_v002.allocation_probe
```

Replay orchestration reuses the existing native workflow with new isolated
paths and timestamps, without overwriting the earlier workflow or results.
Completed attempt directories must not be reused for another financial run.
