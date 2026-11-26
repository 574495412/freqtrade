# Local macro quote inputs

`macro_transmission.py` owns an immutable snapshot of raw daily Yahoo `^TNX`
and `DX-Y.NYB` index quotes. `macro_transmission_provider.py` extends upstream
Freqtrade `DataProvider` for read-only BACKTEST research. Ordinary candle
storage/slicing stays in the parent. Cache reset also invalidates the local
observation clock. There is no strategy, wallet, order or financial admission.

Exact ticker, INDEX product, daily frequency and provider timezones are checked.
Provider epoch timestamps identify a bar, not its publication or close time.
Raw null closes stay null. A returned prior nonnull quote retains its own date,
age and count of later null rows; no newer observed close is fabricated. A
120-hour limit bounds this explicit carry scenario. It does not certify why
a source row is absent or a market holiday. TNX units are percentage points;
negative yields are allowed. DXY must be positive. No FRED, real-yield or
futures substitution is implicit.

The default snapshot-observed mode requires the actual receipt by the current
feature clock. A past dated endpoint can be read today from today's historical
snapshot; this does not establish its historical first-seen version. The
explicit `next_day_06utc_model` is a retrospective availability assumption,
not an ICE/Cboe/Yahoo dissemination certificate. Both endpoints preserve that
distinction. The fixed source characteristic compares ten **calendar** days:
positive yield change AND positive log-dollar change gives target fraction
0.25; otherwise1. Unknown endpoints produce no fraction, not cash or full risk.
This fraction is a model input, not an actual funded portfolio weight.

Source method: [pinned teaching notebook](https://github.com/mikemarshall01/crypto-quant-strategies/blob/6f039e64d6d13227cdc5a79bbd9d78b093468cd0/notebooks/10_cross_asset_rates_dxy.py).
Original MIT text and source bytes are retained in the study. Its illustrative
return calculation compounds log returns as simple returns. This module does
not adopt or execute that financial calculation, and registration is not a
backtest. See `research/crypto/macro_transmission_source_native_20261116_v1`:
actual969raw rows,93nulls and34unique technical checks;no new financial account,
accepted alpha, factor_analysis or bench. Original first-seen, complete native
execution rules and independent economic evidence remain separate requirements.
