"""Local fee overlay on actual native Trade/Order cash and settlement state.

Native order/trade percentages must be zero to avoid double charges. These
component balances include fees; native trade PnL/statistics remain fee-exclusive
and must not be presented as net strategy performance. The full equity account
entry point remains closed until its remaining contracts are implemented.
"""

from dataclasses import replace
from decimal import Decimal
from math import isclose

from freqtrade.data.equity_fees import EquityFeeContract
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_wallets import EquitySettlementWallets
from freqtrade.persistence import Trade


class EquityFeeWallets(EquitySettlementWallets):
    def _dividend_nav_balances(self):
        """Unpaid net entitlement and cumulative paid cash; default no dividends."""
        return Decimal(0), Decimal(0)

    def __init__(self, config, exchange, *, fees: EquityFeeContract, asof):
        if not isinstance(fees, EquityFeeContract):
            raise EquityAdmissionError("Explicit fee contract required")
        self.fees = fees
        self.fee_journal = ()
        self.total_execution_fees = Decimal(0)
        super().__init__(config, exchange, contract=fees.cash, asof=asof, entry_fee_rate=0.0)

    def _update_dry(self):
        previous = (
            self._wallets,
            self._positions,
            self.cash_restriction,
            self.fee_journal,
            self.total_execution_fees,
        )
        try:
            super()._update_dry()
            total, unsettled_fees, journal, claims = Decimal(0), Decimal(0), [], []
            for trade in Trade.get_trades_proxy():
                if trade.fee_open != 0 or trade.fee_close != 0:
                    raise EquityAdmissionError("Native percentages must be zero for fee overlay")
                for order in trade.orders:
                    if not order.safe_filled:
                        continue
                    # Base wallet already validates atomic state, clocks and identity.
                    when = order.order_filled_utc
                    quote = self.fees.quote(
                        trade.pair, when, order.ft_order_side, order.safe_filled, order.safe_price
                    )
                    gross = Decimal(str(order.safe_filled)) * Decimal(str(order.safe_price))
                    total += quote.total
                    release = self.contract.release_for_fill(when)
                    journal.append((trade.pair, order.order_id, when, order.ft_order_side, quote))
                    if order.ft_order_side == "sell":
                        if quote.total > gross:
                            raise EquityAdmissionError("Negative net sale proceeds unsupported")
                        if self.asof < release.available_at:
                            unsettled_fees += quote.total
                            claims.append(
                                (
                                    trade.pair,
                                    order.order_id,
                                    release.available_at,
                                    float(gross - quote.total),
                                )
                            )
            balance = self._wallets[self._stake_currency]
            free = balance.free - float(total - unsettled_fees)
            if free < -1e-8:
                raise EquityAdmissionError("Native fills overspent cash including dated fees")
            self._wallets[self._stake_currency] = balance._replace(
                free=max(0.0, free),
                used=balance.used - float(unsettled_fees),
                total=balance.total - float(total),
            )
            restriction = self.cash_restriction
            self.cash_restriction = replace(
                restriction,
                unsettled_sale_proceeds=restriction.unsettled_sale_proceeds - float(unsettled_fees),
                sale_orders=tuple(claims),
                filled_order_cash_flow=restriction.filled_order_cash_flow - float(total),
            )
            adjusted = self._wallets[self._stake_currency]
            expected = (
                self._start_cap[self._stake_currency] + self.cash_restriction.filled_order_cash_flow
                + self._additional_settled_cash()
            )
            if not isclose(adjusted.total, expected, abs_tol=1e-8, rel_tol=0):
                raise EquityAdmissionError("Net fee cash flow does not reconcile")
            self.fee_journal, self.total_execution_fees = tuple(journal), total
        except Exception:
            (
                self._wallets,
                self._positions,
                self.cash_restriction,
                self.fee_journal,
                self.total_execution_fees,
            ) = previous
            raise

    def affordable_quantity(self, pair, when, price, board_lot):
        self._check_isolation(self._config, self.contract)
        if when != self.asof:
            raise EquityAdmissionError("Fee sizing requires the current native cash clock")
        return self.fees.affordable_quantity(
            pair, when, price, board_lot, self.get_available_stake_amount()
        )
