# Local SFC reported short-inventory input

`equity_short_positions.py` parses the regulator's exact five-column weekly
CSV schema. `equity_short_positions_provider.py` subclasses the existing local
`EquityDataProvider`, which subclasses the pinned upstream `DataProvider`.
Neither module admits an equity financial account or defines a Registry factor.

Shares are nonnegative integers; the optional HKD reported value is Decimal.
An explicit source zero remains zero **reported inventory**, not proof that all
market short positions are zero. An unlisted counter stays unknown. `n.a.` value
does not erase observed shares or become a zero valuation. Duplicate counters,
mixed reporting dates, nonfinite/negative quantities, HTML and byte-hash
mismatches reject. Original five-digit counters are explicit identities;
parallel counters and issuer share classes are not automatically aliased.

Reporting date, source publication and observation are separate fields. Missing
publication/observation keeps data unavailable, even when numbers exist. No
report+7-calendar-day or +10-trading-day rule supplies a historical fact. Current
retrieval does not backdate first observation. The provider requires the native
callback clock and an explicit report date; it does not select an older report
when the requested one is missing. Availability checks only validate supplied
clocks: source authenticity, complete revisions, historical dissemination and
an economic staleness policy need separate evidence before trading research.

Source: [SFC original report index](https://www.sfc.hk/en/Regulatory-functions/Market/Short-position-reporting/Aggregated-reportable-short-positions-of-specified-shares),
[original publication notice](https://apps.sfc.hk/edistributionWeb/api/news/list-content?lang=EN&refNo=12PR94).
This is reportable weekly outstanding inventory, not daily short-sale turnover,
total market shorts, shares outstanding, borrow cost or a short-interest ratio.

The source review at
`research/equity/short_position_admission_native_20261116_v1` pins the separate
GitHub `wonglaitung/fortune` descriptive intensity script. Its normalization,
assumed lag, label and breadth requirements are not silently replaced by these
input modules. Original CSV downloads returned403; browser-independent web text
shows selected schema/rows only. No complete historical SFC input, new stock
account, accepted alpha, factor_analysis or bench is claimed. The existing
ordinary equity financial entry remains closed. Synthetic tests validate input
boundaries, not returns or historical market completeness.
