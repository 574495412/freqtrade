"""Historical Turtle System 2 adaptation using completed UTC daily signals."""

import hashlib
import io
import math
from pathlib import Path

import pandas as pd

from freqtrade.strategy import stoploss_from_absolute
from research.crypto.donchian60_tail_native_20261125_v1.Donchian60Tail import (
    DAY,
    LONG,
    SHORT,
    Donchian60Tail,
)


def daily_features(history):
    dates = pd.DatetimeIndex(history.date).as_unit("ns")
    if not len(dates) or not dates.equals(pd.date_range(dates[0].floor("D"), periods=len(dates), tz="UTC")):
        raise ValueError("Expected contiguous completed UTC daily bars")
    high, low, close = (history[column].astype(float) for column in ("high", "low", "close"))
    for value in (high, low, close):
        if not (value.map(math.isfinite) & value.gt(0)).all():
            raise ValueError("Non-finite or nonpositive daily price")
    if not (high.ge(close) & low.le(close)).all():
        raise ValueError("Daily OHLC is inconsistent")
    previous = close.shift()
    tr = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
    seeded = tr.copy()
    seeded.iloc[:19] = math.nan
    if len(seeded) >= 20:
        seeded.iloc[19] = tr.iloc[:20].mean()
    n = seeded.ewm(alpha=1 / 20, adjust=False).mean()
    direction = close.gt(high.shift().rolling(55).max()).astype(int) - close.lt(low.shift().rolling(55).min()).astype(int)
    return pd.DataFrame({
        "date": history.date,
        "close": close,
        "direction": direction,
        "n": n,
        "exit_long": close.lt(low.shift().rolling(20).min()),
        "exit_short": close.gt(high.shift().rolling(20).max()),
    })


class Turtle55Close(Donchian60Tail):
    position_adjustment_enable = True
    max_entry_position_adjustment = 3
    use_custom_stoploss = True

    def populate_indicators(self, dataframe, metadata):
        pair = metadata["pair"]
        record = self.config["research_donchian60_daily"][pair]
        data = Path(record["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("Frozen daily source differs")
        history = pd.read_feather(io.BytesIO(data))
        asof = (dataframe.date.iloc[-1] + pd.Timedelta(hours=1)).floor("D")
        features = daily_features(history.loc[history.date < asof])
        self.daily[pair] = {row.date.date(): row for row in features.itertuples(index=False)}
        signals = features.set_index(features.date + pd.Timedelta(hours=23)).direction
        dataframe["dc60_direction"] = dataframe.date.map(signals).fillna(0).astype("int8")
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = super().populate_entry_trend(dataframe, metadata)
        dataframe["enter_tag"] = dataframe["enter_tag"].replace({LONG: "turtle55_long", SHORT: "turtle55_short"})
        return dataframe

    def entry_row(self, pair, time, side, tag):
        if not self.midnight(time):
            return None
        row = self.daily[pair][time.date() - DAY]
        direction = 1 if side == "long" else -1
        if row.direction == direction and tag == f"turtle55_{side}" and math.isfinite(row.n) and row.n > 0:
            return row
        return None

    def custom_stake_amount(
        self, pair, current_time, current_rate, proposed_stake, min_stake,
        max_stake, leverage, entry_tag, side, **kwargs,
    ):
        try:
            bar = self.entry_row(pair, current_time, side, entry_tag)
            if bar is None or current_rate <= 2 * bar.n:
                return 0.0
            unit = self.wallets.get_total_stake_amount() * 0.01 * current_rate / bar.n
            stake = min(unit, proposed_stake / 4, max_stake)
            return stake if min_stake is None or stake >= min_stake else 0.0
        except Exception as error:
            self.callback_errors.append(("custom_stake_amount", str(error)))
            return 0.0

    def campaign_n(self, trade):
        side = "short" if trade.is_short else "long"
        bar = self.entry_row(trade.pair, trade.open_date_utc, side, trade.enter_tag)
        if bar is None:
            raise ValueError("Opening order does not bind to a completed signal")
        return bar.n

    @staticmethod
    def channel_exit(trade, bar):
        return bar.exit_short if trade.is_short else bar.exit_long

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if not self.midnight(current_time) or current_time <= trade.open_date_utc:
            return None
        try:
            bar = self.daily[pair][current_time.date() - DAY]
            if self.channel_exit(trade, bar):
                self.source_exits.append({"pair": pair, "entry": trade.open_date_utc.isoformat(),
                                          "decision_clock": current_time.isoformat(), "reason": "turtle20"})
                return "turtle20"
        except Exception as error:
            self.callback_errors.append(("custom_exit", str(error)))
        return None

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit,
                              min_stake, max_stake, **kwargs):
        if not self.midnight(current_time) or current_time <= trade.open_date_utc or trade.has_open_orders:
            return None
        try:
            entries = trade.select_filled_orders(trade.entry_side)
            if len(entries) >= 4 or not entries:
                return None
            bar = self.daily[trade.pair][current_time.date() - DAY]
            if self.channel_exit(trade, bar):
                return None
            direction = -1 if trade.is_short else 1
            last = entries[-1].safe_price
            favorable = min(direction * (price - last) for price in (bar.close, current_rate))
            if favorable < 0.5 * self.campaign_n(trade):
                return None
            stake = entries[0].safe_filled * current_rate
            if (min_stake or 0.0) <= stake <= max_stake:
                return stake, "turtle_add"
        except Exception as error:
            self.callback_errors.append(("adjust_trade_position", str(error)))
        return None

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs):
        try:
            entries = trade.select_filled_orders(trade.entry_side)
            if entries:
                direction = -1 if trade.is_short else 1
                price = entries[-1].safe_price - direction * 2 * self.campaign_n(trade)
                if price > 0:
                    return stoploss_from_absolute(price, current_rate, is_short=trade.is_short, leverage=trade.leverage)
        except Exception as error:
            self.callback_errors.append(("custom_stoploss", str(error)))
        return None
