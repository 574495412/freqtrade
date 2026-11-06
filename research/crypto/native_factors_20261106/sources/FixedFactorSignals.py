"""Native IStrategy signal adapters; financial activation awaits shared RD1.

These are research-engineering classes, not runnable qualified strategies.
bot_start rejects execution explicitly rather than silently producing cash-only
backtests that could be misreported as factor results.
"""
import pandas as pd
from freqtrade.exceptions import OperationalException
from freqtrade.strategy import IStrategy

from AverageStrategy import AverageStrategy
from factor_inputs import cross_section, effective_daily_scale, scale_risk_approved_stake
from native_reductions import scale_target


class PendingRD1:
    # Frozen common RD1 stop, shared by F1, F2 and its same-risk control.
    stoploss = -0.05
    position_adjustment_enable = True
    max_entry_position_adjustment = 0

    def bot_start(self, **kwargs):
        from native_research_startup import require_scoped_startup
        if require_scoped_startup(self):
            return
        raise OperationalException("F1/F2 native signals only: shared RD1 lifecycle adapter is not yet integrated; financial run refused")

    def confirm_trade_entry(self, **kwargs):
        return False

    def custom_stake_amount(self, **kwargs):
        return 0.0

    def transform_reduction_target(self, target, current_time):
        return target

    def adjust_trade_position(self, trade, current_time, **kwargs):
        controller = getattr(self, "rd1_reduction_controller", None)
        provider = getattr(self, "rd1_reduction_target", None)
        if controller is None or provider is None:
            return None
        target = provider(trade, current_time)
        if target is None:
            return None
        target = self.transform_reduction_target(target, current_time)
        return None if target is None else controller.issue(trade, target, current_time)


class F1Momentum168Native(PendingRD1, IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    startup_candle_count = 169 * 12
    can_short = False
    minimal_roi = {}
    stoploss = -0.05

    def informative_pairs(self):
        return [(p, "1h") for p in self.config["exchange"]["pair_whitelist"]]

    def populate_indicators(self, dataframe, metadata):
        universe = tuple(self.config["exchange"]["pair_whitelist"])
        if metadata["pair"] not in universe:
            raise ValueError("Pair outside frozen universe")
        dates = pd.DatetimeIndex(dataframe.date).tz_convert("UTC")
        decisions = pd.DatetimeIndex(dates.normalize().unique()).sort_values()
        frames = {p: self.dp.get_pair_dataframe(pair=p, timeframe="1h") for p in universe}
        values, selected, valid = cross_section(frames, universe, decisions)
        result = dataframe.copy()
        days = dates.normalize()
        result["factor_momentum168"] = values[metadata["pair"]].reindex(days).to_numpy()
        result["factor_selected"] = selected[metadata["pair"]].reindex(days).to_numpy()
        result["factor_universe_valid"] = valid.reindex(days).to_numpy()
        result["factor_decision_bar"] = dates == days
        return result

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"] = (dataframe.factor_decision_bar & dataframe.factor_universe_valid & dataframe.factor_selected).astype(int)
        dataframe["enter_tag"] = "F1_MOM168"
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = (dataframe.factor_decision_bar & dataframe.factor_universe_valid & ~dataframe.factor_selected).astype(int)
        dataframe["exit_tag"] = "F1_daily_selection"
        return dataframe


class AverageRD1ControlNative(PendingRD1, AverageStrategy):
    """Unchanged EMA8/21 signal/ROI; common RD1 stop, lifecycle still incomplete."""
    pass


class F2Volatility24Native(AverageRD1ControlNative):
    """Inherits every control indicator/entry/exit method; changes size only."""

    def informative_pairs(self):
        return [(p, "1h") for p in self.config["exchange"]["pair_whitelist"]]

    def factor_scale(self, pair, current_time):
        hours = self.dp.get_pair_dataframe(pair=pair, timeframe="1h")
        return effective_daily_scale(hours, current_time)

    def scale_approved_stake(self, pair, current_time, base_stake, min_stake, max_stake):
        return scale_risk_approved_stake(base_stake, self.factor_scale(pair, current_time), min_stake, max_stake)

    def transform_reduction_target(self, target, current_time):
        # Target is the shared unscaled RD1 cap, never the already-scaled holding.
        if float(target.max_remaining_quantity) == 0:
            return target
        return scale_target(target, self.factor_scale(target.pair, current_time))
