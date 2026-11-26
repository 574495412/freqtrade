"""Closed-day channel entry and direction-aware midpoint trail for a native wallet."""

import hashlib
import io
import math
from datetime import timedelta
from pathlib import Path

import pandas as pd

from freqtrade.strategy import IStrategy


DAY = timedelta(days=1)
LONG = "dc60_long"
SHORT = "dc60_short"


def daily_features(daily):
    ends = pd.DatetimeIndex(daily.date)
    if (
        not len(ends)
        or str(ends.tz) != "UTC"
        or not ends.as_unit("ns").equals(
            pd.date_range(ends[0].floor("D"), periods=len(ends), freq="D").as_unit("ns")
        )
    ):
        raise ValueError("Expected an unbroken UTC daily calendar")
    close = daily.close.astype(float)
    if not close.map(math.isfinite).all() or (close <= 0).any():
        raise ValueError("Invalid daily close")
    high = close.rolling(60).max()
    low = close.rolling(60).min()
    signal = pd.Series(0, index=daily.index)
    signal.loc[high.gt(low) & close.eq(high)] = 1
    signal.loc[high.gt(low) & close.eq(low)] = -1
    volatility = close.pct_change(fill_method=None).rolling(90).std() * math.sqrt(365)
    return pd.DataFrame(
        {"date": daily.date, "close": close, "direction": signal,
         "midpoint": (high + low) / 2, "volatility": volatility}
    )


def trail_step(direction, previous, close, midpoint, breakout):
    if breakout == -direction:
        return previous, "dc60_opposite"
    breached = close < previous if direction > 0 else close >= previous
    if breached:
        return previous, "dc60_trail"
    return (max(previous, midpoint) if direction > 0 else min(previous, midpoint)), None


class Donchian60Tail(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    startup_candle_count = 0
    can_short = True
    process_only_new_candles = True
    minimal_roi = {}
    stoploss = -0.99
    trailing_stop = False
    position_adjustment_enable = False
    use_exit_signal = True

    def bot_start(self, **kwargs):
        mode = self.config.get("runmode")
        if (
            getattr(mode, "value", mode) != "backtest"
            or self.config.get("dry_run") is not True
            or not self.config.get("research_donchian60")
            or self.config.get("timeframe_detail") != "5m"
            or self.config.get("max_open_trades") != 6
        ):
            raise ValueError("Fixed historical Donchian60 research only")
        self.callback_errors = []
        self.daily = {}
        self.exit_state = {}
        self.source_exits = []

    def populate_indicators(self, dataframe, metadata):
        pair = metadata["pair"]
        record = self.config["research_donchian60_daily"][pair]
        raw = Path(record["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Frozen daily source differs")
        history = pd.read_feather(io.BytesIO(raw))
        asof = (dataframe.date.iloc[-1] + pd.Timedelta(hours=1)).floor("D")
        daily = daily_features(history.loc[history.date < asof])
        self.daily[pair] = {
            row.date.date(): row for row in daily.itertuples(index=False)
        }
        signals = daily.set_index(daily.date + pd.Timedelta(hours=23)).direction
        dataframe["dc60_direction"] = dataframe.date.map(signals).fillna(0).astype("int8")
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        valid = dataframe.volume.gt(0)
        long = dataframe.dc60_direction.eq(1) & valid
        short = dataframe.dc60_direction.eq(-1) & valid
        dataframe["enter_long"] = long.astype("int8")
        dataframe["enter_short"] = short.astype("int8")
        dataframe.loc[long, "enter_tag"] = LONG
        dataframe.loc[short, "enter_tag"] = SHORT
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage,
                 entry_tag, side, **kwargs):
        return 1.0

    @staticmethod
    def midnight(time):
        return time.hour == time.minute == time.second == time.microsecond == 0

    def entry_row(self, pair, time, side, tag):
        if not self.midnight(time):
            return None
        row = self.daily[pair][time.date() - DAY]
        expected = (1, LONG) if side == "long" else (-1, SHORT)
        return row if (row.direction, tag) == expected else None

    def custom_stake_amount(
        self, pair, current_time, current_rate, proposed_stake, min_stake,
        max_stake, leverage, entry_tag, side, **kwargs,
    ):
        try:
            row = self.entry_row(pair, current_time, side, entry_tag)
            if row is None or not math.isfinite(row.volatility) or row.volatility <= 0:
                return 0.0
            stake = min(proposed_stake * min(1.0, 0.25 / row.volatility), max_stake)
            return stake if min_stake is None or stake >= min_stake else 0.0
        except Exception as error:
            self.callback_errors.append(("custom_stake_amount", str(error)))
            return 0.0

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs,
    ):
        try:
            return (
                order_type == "market"
                and amount > 0
                and self.entry_row(pair, current_time, side, entry_tag) is not None
            )
        except Exception as error:
            self.callback_errors.append(("confirm_trade_entry", str(error)))
            return False

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if not self.midnight(current_time):
            return None
        try:
            direction = -1 if trade.is_short else 1
            open_time = trade.open_date_utc
            row = self.entry_row(pair, open_time, "short" if trade.is_short else "long", trade.enter_tag)
            if row is None or not math.isfinite(row.midpoint):
                raise ValueError("Trade does not bind to its completed entry day")
            key = (pair, trade.is_short, open_time)
            previous, trail, reason = self.exit_state.get(key, (row.date.date(), row.midpoint, None))
            last_closed = current_time.date() - DAY
            while reason is None and previous < last_closed:
                previous += DAY
                next_day = self.daily[pair][previous]
                trail, reason = trail_step(direction, trail, next_day.close, next_day.midpoint, next_day.direction)
                if reason:
                    self.source_exits.append(
                        {"pair": pair, "entry": open_time.isoformat(),
                         "available_day": (previous + DAY).isoformat(),
                         "decision_clock": current_time.isoformat(), "reason": reason}
                    )
            self.exit_state[key] = (previous, trail, reason)
            return reason
        except Exception as error:
            self.callback_errors.append(("custom_exit", str(error)))
            return "dc60_policy_error"
