# Split notices in the local equity DataProvider

This local research extension is not upstream Freqtrade equity support. It
changes neither native orders/holdings nor the closed `EquityBacktesting`
financial entry. Callbacks read a `SplitNoticeBook` through the inherited native
`_set_dataframe_max_date` clock and must request an exact pair, security and event.

The motivating source is QuantConnect/Lean.DataSource.EODHD commit
`cfc33067942ae481f3015f2953fc978e10db8392`, selected Python sample and C# adapter.
Their headers state Apache-2.0; unchanged selected originals and the license text
are retained under `research/equity/split_notice_admission_native_20261116_v1`.
The source algorithm, funding/account engine and dataset are not ported. The
sample targets AAPL long for old/new factor <1 within three days, short for >1;
it exits on absence of target data rather than an explicit split-plus-one timer.
The current documentation contains a different two-day equal-weight model;
these are separate source versions, not interchangeable strategies.

`old_shares/new_shares` is the exact theoretical **price** factor. Its reciprocal
is the theoretical share **quantity** factor. Fractions retain nonterminating
ratios without rounding. Neither is an executable fill or booked entitlement.
Record, distribution and planned first post-split trading clocks are distinct;
none is inferred from another. Proposed/approved states retain their meaning.
Passing the planned date does not confirm execution. A cancellation clears terms
only when that publication is visible. Sparse absence remains unknown.

Both a supplied timezone-aware publication clock and an observation clock must
be reached. Current acquisition cannot backdate historical observations. Among
visible revisions the latest publication wins, so a late arrival of an older
document cannot undo a newer amendment. Byte SHA/status/URL checks bind supplied
interpretations to a document, but do not authenticate first-seen, issuer/class
mapping, revision completeness or a trading calendar. No historical coverage
certificate or no-event indicator is produced. No notice implicitly carries to
a different event/security. The book intentionally rejects security remapping.

The original fixed 503 US/11 HK quote queue has no split notice fields. ORLY's
issuer announcement is conditional and separates the June 9, 2025 after-close
distribution from June 10 post-split trading. Its captured original quote jumps
from about 1348 to 90 across that boundary. This supports a unit-boundary review,
not a new return or a certified full corporate-action feed. The current page
does not supply certified historical first-seen. No historical Hong Kong split
event is admitted in this study; Hong Kong coverage is synthetic interface QA.

Before factor selection: admit complete dated source inputs and original method,
then Registry and actual factor_analysis. Before performance comparison: actual
supported signals and full native financial accounts with correct corporate
actions, outstanding orders, lots/ticks, cash settlement, fees/tax, dividends and
terminal holdings. Split/reverse-split share mutations, fractional cash, due bills,
pending-order transformations and borrow remain outside this input interface.
