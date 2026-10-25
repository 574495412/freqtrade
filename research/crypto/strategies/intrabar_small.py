"""Research-only small net target at native intrabar ROI; frozen core remains a control."""

from freqtrade.exchange import price_to_precision
from freqtrade.exchange.exchange import ROUND_DOWN, ROUND_UP
from research.crypto.strategies.first_hit_dual_v2 import FirstHitDualFixedBase


class IntrabarSmallBase(FirstHitDualFixedBase):
    use_custom_roi = True

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        super().order_filled(pair, trade, order, current_time, **kwargs)
        if order.ft_order_side != trade.entry_side or trade.enter_tag != "small_first_hit":
            return
        target = trade.get_custom_data("target_roi")
        if target is not None:
            # The frozen checkpoint base targets .5R. This separately registered policy uses .25R.
            trade.set_custom_data("target_roi", target / 2)

    def custom_roi(self, pair, trade, current_time, trade_duration, entry_tag, side, **kwargs):
        if entry_tag != "small_first_hit":
            return None
        target = trade.get_custom_data("target_roi")
        if target is None:
            return None
        price = trade.calc_close_rate_for_roi(target)
        quoted = price_to_precision(
            price,
            trade.price_precision,
            trade.precision_mode_price,
            rounding_mode=ROUND_DOWN if trade.is_short else ROUND_UP,
        )
        return trade.calc_profit_ratio(quoted)


class IntrabarSmallLinear(IntrabarSmallBase):
    policy = "LinearStatic"


class IntrabarSmallStatic(IntrabarSmallBase):
    policy = "BoostStatic"


class IntrabarSmallMonthly(IntrabarSmallBase):
    policy = "BoostMonthly"
