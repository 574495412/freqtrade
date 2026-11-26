"""Local fee-aware pre-fill boundary; ordinary native crypto code is unchanged."""

from decimal import Decimal

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_fee_wallets import EquityFeeWallets
from freqtrade.optimize.equity_execution import EquityFillValidationMixin


class EquityFeeFillMixin(EquityFillValidationMixin):
    def _try_close_open_order(self, order, trade, current_date, row):
        if order and self._get_order_filled(order.ft_price, row):
            if not isinstance(self.wallets, EquityFeeWallets):
                raise EquityAdmissionError("Dated fees require the fee-aware native wallet")
            if trade.fee_open != 0 or trade.fee_close != 0:
                raise EquityAdmissionError("Native percentages must be zero for fee overlay")
            quote = self.wallets.fees.quote(
                trade.pair, current_date, order.ft_order_side, order.safe_amount, order.ft_price
            )
            gross = Decimal(str(order.safe_amount)) * Decimal(str(order.ft_price))
            if order.ft_order_side == "buy":
                if gross + quote.total > Decimal(
                    str(self.wallets.get_free(self.wallets.contract.currency))
                ) + Decimal(".00000001"):
                    raise EquityAdmissionError("Native entry exceeds cash including dated fees")
            elif quote.total > gross:
                raise EquityAdmissionError("Negative net sale proceeds unsupported")
        return super()._try_close_open_order(order, trade, current_date, row)
