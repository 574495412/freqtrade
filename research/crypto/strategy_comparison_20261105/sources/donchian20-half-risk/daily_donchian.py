"""Daily Donchian trend rules with a fixed entry-ATR stop and volatility-based sizing."""

import numpy as np
import pandas as pd
import talib.abstract as ta

from freqtrade.strategy import IStrategy, informative, stoploss_from_absolute


def signal_atr(frame, when):
    """Select the exact completed signal hour, even if a provider contains future rows."""
    clock = pd.Timestamp(when).floor("h") - pd.Timedelta(hours=1)
    rows = frame.loc[frame.date.eq(clock), "atr_1d"]
    if len(rows) != 1:
        return None
    value = float(rows.iloc[0])
    return value if np.isfinite(value) and value > 0 else None


class DailyDonchian(IStrategy):
    """Core channel rules, with no winner-skip filter, pyramiding or parameter optimization."""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    startup_candle_count = 800
    process_only_new_candles = True
    use_custom_stoploss = True
    position_adjustment_enable = False
    trailing_stop = False
    minimal_roi = {"0": 1000.0}
    stoploss = -0.99
    entry_days = 20
    exit_days = 10
    risk_fraction_per_trade = 0.005

    @informative("1d")
    def populate_indicators_daily(self, dataframe, metadata):
        dataframe["channel_high"] = dataframe.high.rolling(self.entry_days).max()
        dataframe["channel_low"] = dataframe.low.rolling(self.entry_days).min()
        dataframe["exit_high"] = dataframe.high.rolling(self.exit_days).max()
        dataframe["exit_low"] = dataframe.low.rolling(self.exit_days).min()
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=20)
        return dataframe

    def populate_indicators(self, dataframe, metadata):
        # Native merge makes a completed daily candle visible on the23:00 signal bar.
        # Shift one hourly row so today's channel and risk levels stay fixed from00:00.
        for name in ("channel_high", "channel_low", "exit_high", "exit_low", "atr"):
            dataframe[name + "_1d"] = dataframe[name + "_1d"].shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        valid = dataframe.atr_1d.gt(0) & dataframe.volume.gt(0)
        dataframe["enter_long"] = (
            valid
            & dataframe.close.gt(dataframe.channel_high_1d)
            & dataframe.close.shift(1).le(dataframe.channel_high_1d.shift(1))
        ).astype(int)
        dataframe["enter_short"] = (
            valid
            & dataframe.close.lt(dataframe.channel_low_1d)
            & dataframe.close.shift(1).ge(dataframe.channel_low_1d.shift(1))
        ).astype(int)
        dataframe["enter_tag"] = f"daily_channel_{self.entry_days}_{self.exit_days}"
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = dataframe.close.lt(dataframe.exit_low_1d).astype(int)
        dataframe["exit_short"] = dataframe.close.gt(dataframe.exit_high_1d).astype(int)
        return dataframe

    def custom_stake_amount(
        self,
        pair,
        current_time,
        current_rate,
        proposed_stake,
        min_stake,
        max_stake,
        leverage,
        entry_tag,
        side,
        **kwargs,
    ):
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        atr = signal_atr(frame, current_time)
        if atr is None or current_rate <= 0 or not 0 < 2 * atr / current_rate < 0.9:
            return 0.0
        # Native wallet stake capital includes realized profits, not marked unrealized PnL.
        capital = self.wallets.get_total_stake_amount()
        target = capital * self.risk_fraction_per_trade / (2 * atr / current_rate)
        stake = min(target, proposed_stake, max_stake)
        return stake if min_stake is None or stake >= min_stake else 0.0

    def fixed_atr(self, pair, trade):
        value = trade.get_custom_data(key="entry_atr")
        if value is None:
            frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            value = signal_atr(frame, trade.open_date_utc)
            if value is not None:
                trade.set_custom_data(key="entry_atr", value=value)
        return value

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side == trade.entry_side:
            self.fixed_atr(pair, trade)

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        atr = self.fixed_atr(pair, trade)
        if atr is None:
            return None
        stop = trade.open_rate + (2 * atr if trade.is_short else -2 * atr)
        return stoploss_from_absolute(stop, current_rate, trade.is_short, trade.leverage)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if self.fixed_atr(pair, trade) is None:
            return "missing_fixed_entry_atr"
        return None


class Donchian20(DailyDonchian):
    entry_days = 20
    exit_days = 10


class Donchian55(DailyDonchian):
    entry_days = 55
    exit_days = 20
