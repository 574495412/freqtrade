"""Use supported native exit/entry flags to carry unchanged session directions.

Prices and sign rules remain pinned. Quantities remain fixed during a held
position; this is not the paper's free notional rebalance at every session.
"""

import numpy as np
import pandas as pd
from SessionRules import SessionRules


class CarryRules(SessionRules):
    mode = "source"
    delayed_entry = False

    def populate_indicators(self, dataframe, metadata):
        dataframe = super().populate_indicators(dataframe, metadata)
        boundary_time = dataframe.date + pd.Timedelta(minutes=5)
        hour = 8 if metadata["pair"].startswith("BTC/") else 5
        scheduled = boundary_time.dt.minute.eq(0) & boundary_time.dt.hour.isin([hour, hour + 12])
        # Row T-5m is complete at boundary T. Prior same-session endpoints
        # are T-24h and T-12h; their closing 5m rows are T-24h-5m/T-12h-5m.
        lagged = dataframe.close.shift(144) / dataframe.close.shift(288) - 1
        valid = dataframe.close.gt(0) & np.isfinite(dataframe.close)
        known = valid.rolling(289, min_periods=289).sum().eq(289)
        known &= dataframe.date.sub(dataframe.date.shift(288)).eq(pd.Timedelta(hours=24))
        target = -np.sign(lagged)
        if metadata["pair"].startswith("ETH/"):
            target = target.where(boundary_time.dt.hour.eq(hour), 1.0)
        if self.mode != "source":
            target = target * 0 + 1.0
        dataframe["next_session_boundary"] = scheduled
        dataframe["next_session_known"] = known
        dataframe["next_session_lag_return"] = lagged
        dataframe["next_session_target"] = target.where(known, 0.0)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        if self.delayed_entry:
            return super().populate_entry_trend(dataframe, metadata)
        pulse = dataframe.next_session_boundary & dataframe.next_session_known
        dataframe["enter_long"] = (pulse & dataframe.next_session_target.eq(1)).astype(int)
        dataframe["enter_short"] = (pulse & dataframe.next_session_target.eq(-1)).astype(int)
        dataframe["enter_tag"] = "carry_boundary"
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        pulse = dataframe.next_session_boundary
        dataframe["exit_long"] = (pulse & dataframe.next_session_target.ne(1)).astype(int)
        dataframe["exit_short"] = (pulse & dataframe.next_session_target.ne(-1)).astype(int)
        dataframe["exit_tag"] = "session_direction_change"
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        return None


class BoundaryCarrySource(CarryRules):
    pass


class ClockMatchedCarrySource(CarryRules):
    delayed_entry = True


class BoundaryCarryLong(CarryRules):
    mode = "long"
