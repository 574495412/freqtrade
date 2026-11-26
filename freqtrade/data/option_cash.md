# Local inverse-option premium cash and standard-margin components

These are local research extensions, not upstream Freqtrade option exchange
support. `OptionPremiumWallets` subclasses native Wallets and reads actual native
Trade/Order fills. `OptionFeeFillMixin` preserves native limit-price matching,
order close and strategy callbacks while replacing incompatible perpetual fee,
funding and liquidation assumptions. FUTURES/linear swap market metadata is a
synthetic carrier for native objects, not the classification or mechanics of a
Deribit inverse option.

With no futures, transfers or cross collateral:

- Cash = initial coin + filled sale premiums − filled purchase premiums − fees.
- Equity = cash + signed option quantity × current option mark.
- S:SM margin balance = cash, excluding all option values.
- Available funds = margin balance − summed initial margin − pending reserve.

An option sold short creates a marked liability; receiving its premium is not
equity profit. A bought option's mark gain does not create cash or S:SM buying
power. Partial position reductions use actual filled quantities without adding
generic Trade realized PnL to premium cash again. Filled-order history must not
change or disappear. Only atomic native limit fills are supported; this is not
an exchange partial-fill model. Native percentage/base fees and funding must be
zero because explicit option fees are accounted for separately.

The current dated Deribit product-document formulas determine each short call's
and put's initial/maintenance margin; requirements sum without portfolio offsets.
This is a current-document model, not certified historical/private account
rules. The explicit local conservative pending policy reserves gross short IM
plus fees without crediting an unfilled sale; long entry reserves premium plus
fees. Cancellation releases that reservation through actual native order state.
No claim is made that this local pending policy replicates the venue risk engine.

Native stake is a floating premium-notional interface. The quantity adapter
checks actual upstream amount/contract rounding equals the exact desired
quantity. Representational nextafter adjustments never change actual premium
cash, fees or risk budgets. The option minimum premium notional replaces an
inapplicable generic futures stoploss-reserve minimum, while coin cash, margin
and fee checks independently gate every fill.

`OptionFixtureLifecycleMixin` is explicitly synthetic-only. Its inherited native
start path loads exact native chart files and runs actual strategy callbacks,
matching, wallet updates and ZIP export. It requires flat terminal holdings and
rejects a maintenance breach; it does not invent a forced exit. Component probes
can retain a long terminal position or margin-breach state without claiming a
completed historical account.

The original `OptionBacktesting` full financial constructor remains closed.
Required future integration includes original historical security/fee/risk
rules and availability, executable quotes/marks/index, changing liquidity and
bandwidths, quantity steps, expiry/delivery legs and fees, futures hedge cash and
PnL, liquidation and complete terminal NAV. Metadata acquisition cannot certify
past first-seen, and these current-rule components do not certify that contract.
No expiry or futures hedge is supported by the cash contract's pre-expiry window.

Raw native ZIP fields have mixed semantics: trade/profit_abs and profit_total_abs
use generic zero-percentage-fee gross PnL; final_balance reads the option wallet's
fee-net coin cash. The sidecar reconstructs actual order cash/fees and marks.
Neither a raw ZIP Sharpe/CAGR nor synthetic sidecar cash change establishes a
strategy return or a comparable historical account.

See `research/crypto/option_cash_native_20261116_v1` for the frozen protocols,
original failed debug attempts, final ten isolated native processes, actual ZIPs
and independent exact-rational audit. No order/deployment authorization follows
from these engineering checks.
