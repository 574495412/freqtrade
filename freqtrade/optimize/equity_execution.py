"""Local pre-fill hook for a future complete native equity execution loop.

This mixin is component-tested against actual Backtesting methods. It does not
enable EquityBacktesting, implement auctions, or certify a funded stock run.
"""

from decimal import Decimal

from freqtrade.data.equity_execution import EquityExecutionContract
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.enums import RunMode, TradingMode
from freqtrade.equity_wallets import EquitySettlementWallets
from freqtrade.persistence import Trade


class EquityFillValidationMixin:
    equity_execution_contract: EquityExecutionContract

    def _try_close_open_order(self, order, trade, current_date, row):
        if order and self._get_order_filled(order.ft_price, row):
            contract = getattr(self, "equity_execution_contract", None)
            if (
                not isinstance(contract, EquityExecutionContract)
                or self.config.get("dry_run") is not True
                or self.config.get("runmode") != RunMode.BACKTEST
                or self.trading_mode != TradingMode.SPOT
                or Trade.use_db
                or self.config.get("stake_currency") != contract.cash.currency
                or not isinstance(self.wallets, EquitySettlementWallets)
                or self.wallets.contract != contract.cash
                or self.wallets.asof != current_date
            ):
                raise EquityAdmissionError(
                    "Historical fill guard requires isolated settled-cash backtest"
                )
            if (
                trade.is_short
                or trade.leverage != 1
                or trade.trading_mode != TradingMode.SPOT
                or trade.open_date_utc > current_date
                or trade.fee_open != self.wallets.entry_fee_rate
                or order.ft_pair != trade.pair
                or order.ft_order_side not in {"buy", "sell"}
                or order.side != order.ft_order_side
                or order.order_type not in {"market", "limit"}
                or not order.ft_is_open
                or order.status != "open"
                or order.safe_filled
                or order.safe_remaining != order.safe_amount
                or order.ft_price != order.safe_price
                # Native backtest-created orders set amount, but leave the
                # live-exchange placement mirror ft_amount unset. When present
                # it must agree; safe_amount is validated by the fill contract.
                or (order.ft_amount is not None and order.ft_amount != order.safe_amount)
                or order.safe_fee_base
                or order.funding_fee
                or order.order_date_utc > current_date
            ):
                raise EquityAdmissionError("Unsupported or inconsistent native equity order state")
            if order.ft_order_side == "sell" and order.safe_amount > trade.amount:
                raise EquityAdmissionError("Cash equity order exceeds native long holding")
            contract.require_fill(trade.pair, current_date, order.safe_amount, order.ft_price)
            if order.ft_order_side == "buy":
                required = (
                    Decimal(str(order.safe_amount))
                    * Decimal(str(order.ft_price))
                    * (1 + Decimal(str(self.wallets.entry_fee_rate)))
                )
                free = Decimal(str(self.wallets.get_free(contract.cash.currency)))
                if required > free + Decimal("0.00000001"):
                    raise EquityAdmissionError("Native entry exceeds settled cash including fee")
        return super()._try_close_open_order(order, trade, current_date, row)
