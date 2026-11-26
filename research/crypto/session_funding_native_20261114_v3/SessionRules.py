"""Local funded session implementation of the pinned authors' sign rules.

Binance perpetual, next 5m open, flat for 5m between every timed session.
This differs explicitly from the source's Kraken close-to-close wealth model.
"""

import numpy as np
import pandas as pd

from freqtrade.strategy import IStrategy


class SessionRules(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    startup_candle_count = 300
    process_only_new_candles = True
    minimal_roi = {}
    stoploss = -0.99
    trailing_stop = False
    position_adjustment_enable = False
    use_exit_signal = True
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    mode = "source"

    def populate_indicators(self, dataframe, metadata):
        sym = metadata["pair"].split("/")[0]
        hour = 8 if sym == "BTC" else 5
        clock = dataframe.date
        boundary = clock.dt.minute.eq(0) & clock.dt.hour.isin([hour, hour + 12])
        # At row T=08:00, old same-session end was T-12h and start T-24h.
        # Use the 5m bars ending there: rows T-12h-5m and T-24h-5m.
        end = dataframe.close.shift(145)
        start = dataframe.close.shift(289)
        valid = dataframe.close.gt(0) & np.isfinite(dataframe.close)
        known = valid.rolling(290, min_periods=290).sum().eq(290)
        known &= clock.sub(clock.shift(289)).eq(pd.Timedelta(minutes=1445))
        dataframe["lag_same_session_return"] = end / start - 1
        dataframe["session_boundary"] = boundary
        dataframe["session_known"] = known
        target = -np.sign(dataframe.lag_same_session_return)
        if sym == "ETH":
            target = target.where(clock.dt.hour.eq(hour), 1.0)
        if self.mode != "source":
            target = target * 0 + 1.0
        dataframe["session_target"] = target.where(known, 0.0)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        scheduled = dataframe.session_boundary & dataframe.session_known
        if self.mode == "passive":
            scheduled = dataframe.session_known
        dataframe["enter_long"] = (scheduled & dataframe.session_target.eq(1)).astype(int)
        dataframe["enter_short"] = (scheduled & dataframe.session_target.eq(-1)).astype(int)
        dataframe["enter_tag"] = self.mode
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if self.mode == "passive":
            return None
        hour = 8 if pair.startswith("BTC/") else 5
        if current_time.minute == 0 and current_time.hour in [hour, hour + 12]:
            return "session_end"
        return None


class SourceSessions(SessionRules):
    mode = "source"


class TimedLongSessions(SessionRules):
    mode = "timed_long"


class PassiveLong(SessionRules):
    mode = "passive"
