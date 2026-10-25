"""Apply the fixed stop through the native after-fill callback, which updates the real trade."""

from freqtrade.exchange.exchange import TICK_SIZE
from freqtrade.strategy import stoploss_from_absolute
from research.crypto.strategies.first_hit_dual import FirstHitDualBase


class FirstHitDualFixedBase(FirstHitDualBase):
    use_custom_stoploss = True

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        if not after_fill:
            return None
        locked = trade.get_custom_data("locked_stop")
        if locked is None or trade.precision_mode_price != TICK_SIZE or not trade.price_precision:
            return None
        # Native rounds a long stop upward and a short stop downward. Aim strictly inside
        # that rounding cell so floating reconstruction cannot tighten by an extra tick.
        sign = -1 if trade.is_short else 1
        inside_cell = locked - sign * float(trade.price_precision) / 4
        return stoploss_from_absolute(inside_cell, current_rate, trade.is_short, trade.leverage)


class FirstHitDualLinearV2(FirstHitDualFixedBase):
    policy = "LinearStatic"


class FirstHitDualStaticV2(FirstHitDualFixedBase):
    policy = "BoostStatic"


class FirstHitDualMonthlyV2(FirstHitDualFixedBase):
    policy = "BoostMonthly"
