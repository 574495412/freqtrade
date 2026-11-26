# Inverse-option inputs and local native history

These local modules extend the pinned Freqtrade research code; upstream Freqtrade
2026.9 is not declared to support an option exchange/account. The local
`OptionBacktesting` rejects before the inherited constructor. Spot/futures paths
and the TradingMode enum are unchanged.

The motivating selected source is
`mikemarshall01/crypto-quant-strategies@6f039e64d6d13227cdc5a79bbd9d78b093468cd0`,
notebook08 (MIT). Its monthly variance-gap scaling is an educational approximation,
not actual premium cash, collateral, hedge fills or account returns. Its future
realized variance is an ex-post measurement, not an available entry factor. No
source portfolio/account engine or synthetic data fallback is executed here.

The local `inverse_option_contracts.py` admits successful original JSON-RPC
metadata with exact request/currency/strike/type/expiry identity and binds its
byte SHA plus capture time. Supported inverse BTC/ETH contract multipliers are1;
the API amount is underlying-coin exposure, not USD notional. Strike is USD/base,
premium is coin, and settlement is coin. No current capture is certified as a
historical instrument universe, active state, minimum-size or tick schedule.
Expiry comes from the epoch metadata; the name is checked against it rather than
used to infer expiry. `lot_size` is reserved and is not a trading-size constraint.

Price tiers use the original API's strict `above_price` rule: .005 is in the
.0001 tier, while .0055 uses .0005. Known-minimum/tick validation is not venue
acceptance, a quantity increment rule, margin admission or a fill guarantee.

`option_trade_fee` requires explicit underlying-rate and premium-cap fractions:
`amount_base * min(underlying_rate, premium_per_base * premium_cap)`. There are
no implicit historical fees, discounts, fee rounding or delivery charges. Current
standard3bps/12.5% examples are arithmetic parameters, not private-account proof.

`isolated_expiry` returns signed intrinsic **before fees**, not PnL. The coin
amount is `signed_amount_base * max(S-K,0)/S` for calls, or its put analogue.
Current venue documentation states a settlement-method change for expiries from
2026-08-01: ITM options generate an inverse future at strike, then settle coin.
The helper labels that dated route and its USD future notional. Existing future
netting, delivery fees, collateral, premium flows and short-option margin are
not implemented. A delivery input must bind the original index, exact closed
expiry day, single-row/total completeness, positive USD price and capture clock;
it is not a forecast or certification of historical first publication.

`history/inverse_options.py` uses the upstream FeatherDataHandler and
`load_pair_history(fill_up_missing=False,drop_incomplete=False)`. The local
`instrument_name/base_coin` key and SPOT CandleType are storage conventions only,
not a CCXT market declaration or permission to trade it with spot accounting.
Raw unique complete hourly timestamps and OHLCV are validated before native
aggregation. Zero source volumes remain zero. Source chart `cost` is quote
volume, not an account fee. Historical prints, mark prices, bid/ask and charts
remain different data contracts. The parser does not certify executability or
stream individual historical observation clocks into a strategy callback.

See `research/crypto/option_premium_admission_native_20261116_v1/README.md` for
the actual 1949 metadata records, one archived call's25 chart bar starts,21 zero
volumes, actual expiry price, tests, independent unit audit and source receipts.
Full reference-window option accounts and portfolio returns remain unverified.
