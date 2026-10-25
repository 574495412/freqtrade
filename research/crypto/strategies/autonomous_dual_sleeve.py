"""One perpetual account: small trend pullbacks plus larger confirmed breakouts."""

import math

import numpy as np
import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute


def indicators(frame):
    frame = frame.copy()
    previous = frame.close.shift(1)
    frame["atr"] = (
        pd.concat(
            [frame.high - frame.low, (frame.high - previous).abs(), (frame.low - previous).abs()],
            axis=1,
        )
        .max(axis=1)
        .rolling(14, min_periods=14)
        .mean()
    )
    change = frame.close.diff()
    gain = change.clip(lower=0).rolling(14).sum()
    loss = (-change.clip(upper=0)).rolling(14).sum()
    frame["rsi"] = 100 * gain / (gain + loss).replace(0, np.nan)
    for period in [20, 48, 144, 288]:
        frame[f"mean_{period}"] = frame.close.rolling(period).mean()
    frame["channel_high"] = frame.high.rolling(48).max().shift(1)
    frame["channel_low"] = frame.low.rolling(48).min().shift(1)
    frame["volume_ratio"] = frame.volume / frame.volume.shift(1).rolling(12).mean()
    frame["pullback"] = frame.close / frame.close.shift(3) - 1
    frame["slope"] = frame.mean_48 / frame.mean_48.shift(12) - 1
    return frame


def signals(frame, allow_small=True, allow_core=True):
    frame = frame.copy()
    valid = frame.atr.gt(0) & frame.volume.gt(0)
    calm = valid & frame.volume_ratio.between(0.5, 2) & (2 * frame.atr / frame.close).lt(0.025)
    small_long = (
        calm
        & frame.mean_48.gt(frame.mean_144)
        & frame.close.gt(frame.mean_144)
        & frame.pullback.lt(0)
        & frame.close.gt(frame.open)
        & frame.rsi.shift(1).lt(40)
        & frame.rsi.gt(frame.rsi.shift(1))
        & frame.rsi.lt(50)
    )
    small_short = (
        calm
        & frame.mean_48.lt(frame.mean_144)
        & frame.close.lt(frame.mean_144)
        & frame.pullback.gt(0)
        & frame.close.lt(frame.open)
        & frame.rsi.shift(1).gt(60)
        & frame.rsi.lt(frame.rsi.shift(1))
        & frame.rsi.gt(50)
    )
    core_valid = valid & frame.volume_ratio.ge(1.5) & (3 * frame.atr / frame.close).lt(0.05)
    core_long = (
        core_valid
        & frame.mean_48.gt(frame.mean_288)
        & frame.slope.gt(0)
        & frame.close.gt(frame.channel_high)
        & frame.close.shift(1).le(frame.channel_high.shift(1))
    )
    core_short = (
        core_valid
        & frame.mean_48.lt(frame.mean_288)
        & frame.slope.lt(0)
        & frame.close.lt(frame.channel_low)
        & frame.close.shift(1).ge(frame.channel_low.shift(1))
    )
    small_long &= ~small_long.shift(1, fill_value=False)
    small_short &= ~small_short.shift(1, fill_value=False)
    frame["enter_long"], frame["enter_short"], frame["enter_tag"] = 0, 0, ""
    if allow_small:
        frame.loc[small_long, ["enter_long", "enter_tag"]] = [1, "small_pullback"]
        frame.loc[small_short, ["enter_short", "enter_tag"]] = [1, "small_pullback"]
    if allow_core:
        frame.loc[core_long | core_short, ["enter_long", "enter_short"]] = 0
        frame.loc[core_long, ["enter_long", "enter_tag"]] = [1, "core_breakout"]
        frame.loc[core_short, ["enter_short", "enter_tag"]] = [1, "core_breakout"]
    return frame


class AutonomousDualSleeve(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    startup_candle_count = 400
    can_short = True
    process_only_new_candles = True
    position_adjustment_enable = False
    use_custom_stoploss = True
    stoploss = -0.12
    minimal_roi = {"0": 1000.0}
    allow_small = True
    allow_core = True
    max_small = 2
    max_core = 4

    def populate_indicators(self, dataframe, metadata):
        return indicators(dataframe)

    def populate_entry_trend(self, dataframe, metadata):
        return signals(dataframe, self.allow_small, self.allow_core)

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"], dataframe["exit_short"] = 0, 0
        return dataframe

    def closed_row(self, pair, when):
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        clock = pd.Timestamp(when).floor("5min") - pd.Timedelta(minutes=5)
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
        if entry_tag not in {"small_pullback", "core_breakout"}:
            return False
        opened = Trade.get_trades_proxy(is_open=True)
        count = sum(trade.enter_tag == entry_tag for trade in opened)
        return count < (self.max_small if entry_tag == "small_pullback" else self.max_core)

    def distance(self, row, rate, tag):
        if row is None or rate <= 0 or not math.isfinite(row.atr) or row.atr <= 0:
            return None
        if tag == "small_pullback":
            distance = max(0.008, 2 * row.atr / rate)
            return distance if distance < 0.025 else None
        if tag == "core_breakout":
            distance = max(0.01, 3 * row.atr / rate)
            return distance if distance < 0.05 else None
        return None

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
        if leverage != 2.0:
            return 0.0
        distance = self.distance(self.closed_row(pair, current_time), current_rate, entry_tag)
        if distance is None:
            return 0.0
        capital = self.wallets.get_total_stake_amount()
        small = entry_tag == "small_pullback"
        risk, margin = (0.0005, 0.02) if small else (0.005, 0.20)
        fee = float(self.config["fee"])
        # Fees at entry and at the fixed stop are included in the initial risk estimate.
        sign = -1 if side == "short" else 1
        loss_fraction = distance + fee + fee * (1 - sign * distance)
        stake = min(capital * margin, capital * risk / (loss_fraction * leverage), max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side != trade.entry_side:
            return
        row = self.closed_row(pair, trade.open_date_utc)
        distance = self.distance(row, trade.open_rate, trade.enter_tag)
        if distance is not None:
            sign = -1 if trade.is_short else 1
            trade.set_custom_data("entry_risk_distance", distance)
            trade.set_custom_data("initial_stop", trade.open_rate * (1 - sign * distance))

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        stop = trade.get_custom_data("initial_stop")
        distance = trade.get_custom_data("entry_risk_distance")
        if stop is None or distance is None:
            return None
        if trade.enter_tag == "core_breakout":
            row = self.closed_row(pair, current_time)
            sign = -1 if trade.is_short else 1
            if row is not None and sign * (row.close / trade.open_rate - 1) >= 2 * distance:
                trail = row.close - sign * trade.open_rate * distance
                stop = min(stop, trail) if trade.is_short else max(stop, trail)
        return stoploss_from_absolute(stop, current_rate, trade.is_short, trade.leverage)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        distance = trade.get_custom_data("entry_risk_distance")
        if distance is None:
            return "risk_state_unavailable"
        sign = -1 if trade.is_short else 1
        price_profit = sign * (current_rate / trade.open_rate - 1)
        elapsed = (current_time - trade.open_date_utc).total_seconds() / 60
        if trade.enter_tag == "small_pullback":
            if price_profit >= 0.006:
                return "small_profit_target"
            if elapsed >= 120:
                return "small_time_limit"
            row = self.closed_row(pair, current_time)
            if (
                row is not None
                and sign * (row.close - row.mean_20) >= 0
                and current_profit > 0.0005
            ):
                return "small_mean_recovery"
        elif trade.enter_tag == "core_breakout":
            if price_profit >= max(0.03, 3 * distance):
                return "core_profit_target"
            if elapsed >= 4320:
                return "core_time_limit"
            row = self.closed_row(pair, current_time)
            if row is not None and elapsed >= 60 and sign * (row.close - row.mean_48) < 0:
                return "core_trend_lost"
        return None


class AutonomousScalpOnly(AutonomousDualSleeve):
    allow_core = False


class AutonomousCoreOnly(AutonomousDualSleeve):
    allow_small = False
