# Local fixed month-boundary calendar signal

`month_boundary_state` is a local UTC calendar input component, not upstream
Freqtrade functionality, a cross-sectional predictor or an account engine.
The owning study freezes two published GitHub defaults, last3/first2 and
last2/first3 calendar days. Leap days and month lengths are real calendar
properties; UTC is explicit. Future prices are not an input.

The local `MonthBoundary` IStrategy computes the calendar at an hourly candle's
completion. Ordinary native conversion lags flags one row, so execution uses
that next opening. The account loads one preceding hour to start flat at the
exact declared boundary; this is not a pre-window holding or return. Native
unlimited stakes, quantities, fees, matching and wallet accounting still own
the financial path. Actual quantities remain fixed inside each holding period;
there is no daily free weight reset or DCA.

This shared BTC/ETH adaptation is not an author account reproduction. Both
selected source repositories use MIT and retain their original LICENSE files
in the study. Only reviewed calendar functions are executed for semantic parity;
neither source financial engine or DEMO-data fallback supplies account returns.
One source hypothesis cites a German BTCE ETP last5-trading-day study, a different
instrument and rule. Preserve that source-method gap rather than relabelling
the local last2/first3-calendar-day hypothesis as the original article's strategy.

The study `research/crypto/month_boundary_source_native_20261116_v1` completes
12 native wallets and10 fixed actual500+500 portfolios. All calendar financial
gates and all portfolio gates fail. Keep those failures,source versions and
cost/window rules without sign/date/weight rescue. Statistical independence,
causal institutional flows, private exchange balances, paper/live execution
and production eligibility are not established by these historical models.
