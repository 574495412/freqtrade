"""Local option fill boundary on native matcher,orders and callbacks.

Generic perpetual funding/liquidation arithmetic is inapplicable to options.
The full option Backtesting entry remains closed;this is an offline component.
"""

from decimal import Decimal
from math import inf, nextafter

from freqtrade.data.inverse_option_contracts import OptionAdmissionError, decimal
from freqtrade.exchange import amount_to_contract_precision
from freqtrade.option_wallets import OptionPremiumWallets
from freqtrade.strategy.strategy_wrapper import strategy_safe_wrapper


def native_option_notional(quantity, price, market, precision_mode):
    """Represent intended quantity through the native floating stake interface.

    This changes the request representation only. Native resultant quantity must
    equal the target exactly;actual premium cash and margin are checked on fills.
    """
    decimal(quantity, positive=True)
    decimal(price, positive=True)
    stake = float(quantity) * float(price)
    for _ in range(5):
        rounded = amount_to_contract_precision(
            stake / float(price),
            market["precision"]["amount"],
            precision_mode,
            market["contractSize"],
        )
        actual = Decimal(str(rounded))
        if actual == quantity:
            return stake
        if actual > quantity:
            break
        stake = nextafter(stake, inf)
    raise OptionAdmissionError("Native premium notional cannot preserve exact requested quantity")


class OptionFeeFillMixin:
    def get_valid_entry_price_and_stake(self, pair, *args, **kwargs):
        rate, stake, leverage, _ = super().get_valid_entry_price_and_stake(pair, *args, **kwargs)
        if rate <= 0:
            return rate, stake, leverage, 0.0
        spec = self.wallets.contract.spec(pair)
        # The generic futures stoploss reserve is not an option minimum size.
        # Native stake carries premium notional;actual cash/IM/fees gate the fill.
        minimum_premium_notional = spec.min_amount_base * Decimal(str(rate))
        return rate, stake, leverage, float(minimum_premium_notional)

    def _try_close_open_order(self, order, trade, current_date, row):
        if not isinstance(self.wallets, OptionPremiumWallets) or self.wallets.asof != current_date:
            raise OptionAdmissionError("Bound native option wallet/current cash clock required")
        if order and self._get_order_filled(order.ft_price, row):
            self.wallets.require_fill_capacity(trade, order)
            order.close_bt_order(current_date, trade)
            self.wallets.update()
            strategy_safe_wrapper(self.strategy.order_filled, supress_error=True)(
                pair=trade.pair,
                trade=trade,
                order=order,
                current_time=current_date,
            )
            if not (order.ft_order_side == trade.exit_side and order.safe_amount == trade.amount):
                self._call_adjust_stop(current_date, trade, order.ft_price)
            return True
        self.wallets.update()
        return False

    def _run_funding_fees(self, trade, current_time, force=False):
        self.wallets.contract.spec(trade.pair)
        if trade.funding_fees or trade.funding_fee_running:
            raise OptionAdmissionError("Options cannot inherit perpetual funding")
