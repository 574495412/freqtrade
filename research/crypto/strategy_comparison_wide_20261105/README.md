# Forty-pair NFI account comparison

This is a research-only comparison of two existing strategies, not a live
authorization or a claim that either is globally optimal. The fixed plan and its
SHA-256 were recorded before running the new accounts. Earlier six-pair results,
the author-month reproduction, and an older forty-pair X4 result were already
known; this is not an unseen or independent test.

`registered_at` is the time of this new wide plan. The inherited
`registration_completed_at`, `technical_revision` and `revision_at` fields in
`plan.json` describe the earlier six-pair plan and its datadir-interface fix;
they are not the registration time or a new revision of this wide study. The
separate `plan-cost15.json` was registered after the X8 8bps outcome was known;
15bps was already a fixed cost scenario in the earlier six-pair protocol.

- Native CZ-vendored Freqtrade `c051e9065c5b677f03817dd1f83cb980af1ed684`.
- Existing frozen NFI X8 v18.0.146 and the existing X4 `NFI2023OneXCompact`.
- Same 40 pairs, Binance isolated futures, one 1,000 USDT independent account
  each, 1x, six positions, native 5m matching, 8bps per side fee proxy and
  native modeled funding. Source-specific orders, DCA, direction and exits remain.
- Continuous 2025-01-01 through 2026-08-31 23:00 UTC pre-bar hourly mark NAV;
  annual slices retain the same account and open positions. Native terminal
  force exits are separately reported and not used as the common endpoint.
- The 40-pair set and data were selected in an earlier study; the historical
  survivorship and trading eligibility are not certified. The original author's
  223 loaded pairs, 3x and 10,000 USDT are a different account contract.
- One cost scenario does not establish sensitivity to other execution costs.
  Hourly marks are not executable prices or worst intrahour drawdowns. Equal
  leverage is not equal risk. The original X4 compact wrapper retains its
  documented float32 indicator storage; X8 is not downcast.

All 280 market-data files were checked against both the earlier inventory and
original dataset receipts. `funding-audit.json` independently checks all 127,576
native funding rows and retains the seven irregular intervals with pointers and
hashes for the prior public API responses. Settlement in an actual customer
account is not verified.

The runner is a copy of the prior matched-account runner with the analyzed-frame
lossless cache capped at six pairs to bound RAM on a 40-pair replay. It does not
alter official engine, candles, indicators, decisions, order matching, or wallet
rules. The copied runner and all configuration, code, market rules and inputs
are hash-bound by `plan.json`; the backtest rejects network access and credentials.
One financial worker runs at a time, with a 5.5 GiB process RSS guard.

After both registered accounts finish, use the existing independent analyzer:

```sh
/Users/chrise/GitHub/CZ-Agent/.venv-freqtrade/bin/python \
  research/crypto/analyze_matched_comparison.py \
  --root user_data/crypto_quant/wide_strategy_comparison_20261105_v001 \
  --output user_data/crypto_quant/wide_strategy_comparison_20261105_analysis_v001
```

The analyzer requires both account result records and an explicit completion
receipt, checks all exported orders with the independent original cash auditor,
and reconstructs return and marked drawdown. No missing or failed account can be
treated as a completed comparison. Review the results and their evidence tier
before proposing any change to a running paper or live account.
