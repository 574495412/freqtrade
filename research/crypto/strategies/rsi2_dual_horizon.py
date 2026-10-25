"""RSI2 pullback adaptation: hourly small trades and daily larger swing trades."""

import math

import talib.abstract as ta
from compression_retest import CompressionBreakout

from freqtrade.persistence import Trade
from freqtrade.strategy import informative


class RSI2DualHorizon(CompressionBreakout):
    timeframe = "1h"
    startup_candle_count = 800
    allow_hourly = True
    allow_daily = True

    @informative("1d")
    def populate_daily(self, dataframe, metadata):
        dataframe["rsi2"] = ta.RSI(dataframe, timeperiod=2)
        dataframe["mean5"] = dataframe.close.rolling(5).mean()
        dataframe["mean200"] = dataframe.close.rolling(200).mean()
        dataframe["daily_atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    def populate_indicators(self, dataframe, metadata):
        dataframe["rsi2"] = ta.RSI(dataframe, timeperiod=2)
        dataframe["mean5"] = dataframe.close.rolling(5).mean()
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = dataframe.copy()
        dataframe["enter_long"], dataframe["enter_short"], dataframe["enter_tag"] = 0, 0, ""
        bullish = dataframe.close_1d.gt(dataframe.mean200_1d)
        bearish = dataframe.close_1d.lt(dataframe.mean200_1d)
        valid = dataframe.volume.gt(0)
        if self.allow_hourly:
            long = valid & bullish & dataframe.rsi2.lt(5) & dataframe.close.lt(dataframe.mean5)
            short = valid & bearish & dataframe.rsi2.gt(95) & dataframe.close.gt(dataframe.mean5)
            # Use new oversold/overbought events; no same-event averaging or repeated signals.
            long &= ~long.shift(1, fill_value=False)
            short &= ~short.shift(1, fill_value=False)
            dataframe.loc[long, ["enter_long", "enter_tag"]] = [1, "small_hourly_rsi2"]
            dataframe.loc[short, ["enter_short", "enter_tag"]] = [1, "small_hourly_rsi2"]
        if self.allow_daily:
            # The daily candle becomes visible on the23:00 hourly signal; entry is00:00.
            daily = valid & dataframe.date.dt.hour.eq(23)
            long = daily & bullish & dataframe.rsi2_1d.lt(5)
            short = daily & bearish & dataframe.rsi2_1d.gt(95)
            dataframe.loc[long | short, ["enter_long", "enter_short"]] = 0
            dataframe.loc[long, ["enter_long", "enter_tag"]] = [1, "core_daily_rsi2"]
            dataframe.loc[short, ["enter_short", "enter_tag"]] = [1, "core_daily_rsi2"]
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"], dataframe["exit_short"] = 0, 0
        return dataframe

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        if entry_tag not in {"small_hourly_rsi2", "core_daily_rsi2"}:
            return False
        count = sum(t.enter_tag == entry_tag for t in Trade.get_trades_proxy(is_open=True))
        return count < (2 if entry_tag == "small_hourly_rsi2" else 3)

    def tag_distance(self, row, rate, tag):
        if row is None or rate <= 0:
            return None
        small = tag == "small_hourly_rsi2"
        atr = row.atr if small else row.daily_atr_1d
        if not math.isfinite(atr) or atr <= 0:
            return None
        floor, ceiling = (0.01, 0.05) if small else (0.03, 0.30)
        distance = max(floor, 2 * atr / rate)
        return distance if distance <= ceiling else None

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
        distance = self.tag_distance(self.closed_row(pair, current_time), current_rate, entry_tag)
        if distance is None or leverage != 2.0:
            return 0.0
        fee = float(self.config["fee"])
        sign = -1 if side == "short" else 1
        loss = distance + fee + fee * (1 - sign * distance)
        capital = self.wallets.get_total_stake_amount()
        risk, margin = (0.0005, 0.02) if entry_tag == "small_hourly_rsi2" else (0.0025, 0.10)
        stake = min(capital * margin, capital * risk / (loss * leverage), max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side == trade.entry_side:
            row = self.closed_row(pair, trade.open_date_utc)
            distance = self.tag_distance(row, trade.open_rate, trade.enter_tag)
            if distance is not None:
                sign = -1 if trade.is_short else 1
                trade.set_custom_data("initial_stop", trade.open_rate * (1 - sign * distance))

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if trade.get_custom_data("initial_stop") is None:
            return "missing_risk_state"
        row = self.closed_row(pair, current_time)
        elapsed = (current_time - trade.open_date_utc).total_seconds() / 3600
        sign = -1 if trade.is_short else 1
        if trade.enter_tag == "small_hourly_rsi2":
            if row is not None and sign * (row.close - row.mean5) > 0:
                return "hourly_mean_recovery"
            if elapsed >= 12:
                return "hourly_time_limit"
        elif current_time.hour == 0:
            if row is not None and sign * (row.close_1d - row.mean5_1d) > 0:
                return "daily_mean_recovery"
            if elapsed >= 240:
                return "daily_time_limit"
        return None


class RSI2HourlyOnly(RSI2DualHorizon):
    allow_daily = False


class RSI2DailyOnly(RSI2DualHorizon):
    allow_hourly = False
