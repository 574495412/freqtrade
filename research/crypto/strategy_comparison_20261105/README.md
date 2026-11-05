# Frozen native strategy comparison, 2026-11-05

The purpose is to compare five existing strategy versions under one external
account contract. It is not a parameter search, global strategy ranking, new
factor study or permission to trade. The complete registration is `plan.json`.

## Fixed comparison

- Official Freqtrade commit `c051e9065c5b677f03817dd1f83cb980af1ed684`, copied in
  CZ-Agent; every official source hash is checked before and after each account.
- One independent 1000 USDT Binance isolated-futures wallet per strategy and cost.
- BTC, ETH, BNB, SOL, XRP and DOGE. All candidates read the same 42 native input
  files, with unchanged warmup, real historical funding events and hourly marks.
- X8 v18.0.146 with fixed 1x parameters; X7 with fixed 1x parameters; the already
  frozen X4 `NFI2023OneXCompact`; `Donchian20HalfRisk`; `TrendControl`.
- Source-native slots, stake sizing, DCA, direction and exit rules are retained.
  Equal leverage is not equal realized portfolio risk. X4's pre-existing compact
  indicator storage and Donchian's pre-existing half-risk setting are disclosed.
- Total fee proxies of 5/8/15 bps per side. This means a 5 bps commission plus
  0/3/10 bps execution-cost proxy; it does not simulate orderbook fill probability.
- Native 5m matching, with official 5m detail for hourly strategies. The change
  from old hourly-only execution is explicit. No matching algorithm is patched.
- Continuous native account from 2025-01-01 through 2026-08-31. All rankings stop
  at the common **2026-08-31 23:00 UTC pre-bar marked account**. Native forced
  liquidation has different endpoints for different main timeframes and is
  retained separately for cash auditing, never relabeled as the common endpoint.
- Annual slices retain positions and capital; they are overlapping descriptions
  of the same account, not independent trials or reset wallets.

Every ranking uses the same hourly mark OPEN and includes open inventory,
partial exits, modeled funding, fees and a remaining exit-fee reserve. Hourly
drawdown is not the worst intrahour drawdown or certified executable equity.
The pre-existing NFI cash observer independently reconciles open order cost
against native valuation with the known average-basis tick rounding adjustment.
The original official trade and wallet behavior is not modified.

## Selection

Compare net return and full-position hourly mark drawdown across all three costs
and all three calendar slices. A strategy dominates another only if it has no
lower return and no higher drawdown in every scenario, with at least one strict
advantage. If more than one strategy remains on the Pareto frontier, report
**no unique best**. Do not introduce a weighted score after seeing the results.

The earlier study's 30-campaign / PF 1.1 / 15% drawdown / positive-all-cost
screens are retained as separate diagnostics. They are not a user-approved live
risk limit. Campaign PF belongs to campaigns closing within a slice; account
returns use marked capital at slice boundaries. Unrealized PnL is never omitted
from the primary return or drawdown comparison.

Daily Sharpe in the final analysis uses only complete UTC midnight-to-midnight
returns. It excludes the final 23-hour fragment from daily observations while
including that fragment in account return. Sharpe does not select the winner.

## Reproduce

From the Freqtrade project with the prepared CZ environment:

```sh
/path/to/CZ-Agent/.venv-freqtrade/bin/python research/crypto/run_matched_matrix.py \
  --plan research/crypto/strategy_comparison_20261105/plan.json \
  --output user_data/crypto_quant/NEW_UNIQUE_OUTPUT
/path/to/CZ-Agent/.venv-freqtrade/bin/python research/crypto/analyze_matched_comparison.py \
  --root user_data/crypto_quant/NEW_UNIQUE_OUTPUT \
  --output user_data/crypto_quant/NEW_UNIQUE_ANALYSIS
```

The supervisor runs one financial worker at a time. Each worker is limited to
5.5 GiB RSS and 1800 seconds. Wide indicator mappings spill losslessly to disk;
no new numerical downcasting is introduced. During financial replay, socket
connections are rejected: all public market rules, leverage tiers and candles
come from the frozen snapshot; no credentials are supplied or accounts accessed.

`freqtrade.research.data` and `.strategies` are frozen research dependencies of
the old TrendControl source. They are loaded through an explicit research-only
namespace, not copied over or substituted for official core engine files.

The first technical attempt stopped before loading candles because the native
configuration API requires an explicit datadir argument. Its failure is kept in
`matched_strategy_comparison_20261105_v001`. Revision 2 supplies that documented
argument; source strategies, prices, candidate selection and economic settings
are unchanged. Completed accounts and original logs are retained in the v002
output. Failed scenarios are not silently dropped or retuned.

All dates have previously been used by this project; neither this matrix nor its
calendar slices are independent out-of-sample evidence. The six-coin universe
does not establish behavior for the author's full-market portfolio. Final
selection, prospective paper evidence and a user-specified account/risk contract
remain distinct stages.
