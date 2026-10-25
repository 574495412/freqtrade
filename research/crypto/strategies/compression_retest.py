"""A fixed compression breakout control and a subsequent retest treatment."""

import math

import numpy as np
import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute


def features(frame):
    frame = frame.copy()
    previous = frame.close.shift(1)
    frame["atr"] = (
        pd.concat(
            [frame.high - frame.low, (frame.high - previous).abs(), (frame.low - previous).abs()],
            axis=1,
        )
        .max(axis=1)
        .rolling(24)
        .mean()
    )
    frame["upper"] = frame.high.rolling(48).max().shift(1)
    frame["lower"] = frame.low.rolling(48).min().shift(1)
    width = (frame.upper - frame.lower) / previous
    frame["compressed"] = width.le(width.shift(1).rolling(480).quantile(0.25))
    frame["trend"] = frame.close.rolling(240).mean()
    frame["volume_ratio"] = frame.volume / frame.volume.shift(1).rolling(24).mean()
    frame["exit_low"] = frame.low.rolling(24).min().shift(1)
    frame["exit_high"] = frame.high.rolling(24).max().shift(1)
    valid = frame.compressed & frame.volume_ratio.ge(1.5) & frame.atr.gt(0)
    frame["break_long"] = valid & frame.close.gt(frame.upper) & frame.close.gt(frame.trend)
    frame["break_short"] = valid & frame.close.lt(frame.lower) & frame.close.lt(frame.trend)
    return frame


def retests(frame):
    """One decision per breakout; only later completed bars may confirm or invalidate it."""
    long, short = np.zeros(len(frame), dtype=int), np.zeros(len(frame), dtype=int)
    pending = None
    for i, row in enumerate(frame.itertuples(index=False)):
        if pending is not None:
            born, sign, level, atr = pending
            invalid = sign * (row.close - level) < -atr
            if i - born > 6 or invalid:
                pending = None
            else:
                touch = (
                    row.low <= level + 0.25 * atr if sign == 1 else row.high >= level - 0.25 * atr
                )
                reclaim = sign * (row.close - level) > 0 and sign * (row.close - row.open) > 0
                if touch and reclaim:
                    (long if sign == 1 else short)[i] = 1
                    pending = None
        if pending is None and not (long[i] or short[i]):
            if row.break_long:
                pending = (i, 1, row.upper, row.atr)
            elif row.break_short:
                pending = (i, -1, row.lower, row.atr)
    return long, short


class CompressionBreakout(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    startup_candle_count = 600
    can_short = True
    process_only_new_candles = True
    position_adjustment_enable = False
    use_custom_stoploss = True
    stoploss = -0.20
    minimal_roi = {"0": 1000.0}
    use_retest = False

    def populate_indicators(self, dataframe, metadata):
        return features(dataframe)

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = dataframe.copy()
        if self.use_retest:
            dataframe["enter_long"], dataframe["enter_short"] = retests(dataframe)
        else:
            dataframe["enter_long"] = dataframe.break_long.astype(int)
            dataframe["enter_short"] = dataframe.break_short.astype(int)
        dataframe["enter_tag"] = "compression_retest" if self.use_retest else "compression_breakout"
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = dataframe.close.lt(dataframe.exit_low).astype(int)
        dataframe["exit_short"] = dataframe.close.gt(dataframe.exit_high).astype(int)
        return dataframe

    def closed_row(self, pair, when):
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        clock = pd.Timestamp(when).floor("h") - pd.Timedelta(hours=1)
        rows = frame.loc[frame.date.eq(clock)]
        return rows.iloc[0] if len(rows) == 1 else None

    def leverage(
        self,
        pair,
        current_time,
        current_rate,
        proposed_leverage,
        max_leverage,
        entry_tag,
        side,
        **kwargs,
    ):
        return min(2.0, max_leverage)

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        return len(Trade.get_trades_proxy(is_open=True)) < 4

    def distance(self, row, rate):
        if row is None or rate <= 0 or not math.isfinite(row.atr) or row.atr <= 0:
            return None
        distance = max(0.02, 2 * row.atr / rate)
        return distance if distance <= 0.08 else None

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
        distance = self.distance(self.closed_row(pair, current_time), current_rate)
        if distance is None or leverage != 2.0:
            return 0.0
        fee = float(self.config["fee"])
        sign = -1 if side == "short" else 1
        loss = distance + fee + fee * (1 - sign * distance)
        capital = self.wallets.get_total_stake_amount()
        stake = min(capital * 0.20, capital * 0.005 / (loss * leverage), max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side == trade.entry_side:
            distance = self.distance(self.closed_row(pair, trade.open_date_utc), trade.open_rate)
            if distance is not None:
                sign = -1 if trade.is_short else 1
                trade.set_custom_data("initial_stop", trade.open_rate * (1 - sign * distance))

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        stop = trade.get_custom_data("initial_stop")
        return (
            None
            if stop is None
            else stoploss_from_absolute(stop, current_rate, trade.is_short, trade.leverage)
        )

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if trade.get_custom_data("initial_stop") is None:
            return "missing_risk_state"
        if (current_time - trade.open_date_utc).total_seconds() >= 7 * 86400:
            return "seven_day_time_limit"
        return None


class CompressionRetest(CompressionBreakout):
    use_retest = True
