"""Fixed recovery confirmation and risk-scaled exits on the bounded inventory account."""

import math

import numpy as np
from bounded_inventory import InventoryGrid3


def recovered_signals(frame, small=True, core=True):
    frame = frame.copy()
    low = frame[["ema12_1h", "ema48_1h"]].min(axis=1)
    high = frame[["ema12_1h", "ema48_1h"]].max(axis=1)
    known = (
        np.isfinite(frame[["ema12_1h", "ema48_1h", "hour_atr_1h", "trend_4h", "slow_atr_4h"]]).all(
            axis=1
        )
        & frame.hour_atr_1h.gt(0)
        & frame.slow_atr_4h.gt(0)
        & frame.volume.gt(0)
    )
    gaps = {
        "small": np.maximum(0.006 * frame.close, 1.5 * frame.hour_atr_1h),
        "core": np.maximum(0.02 * frame.close, 1.5 * frame.slow_atr_4h),
    }
    location = (frame.close - frame.low) / (frame.high - frame.low).replace(0, np.nan)
    long_body = frame.close.gt(frame.open) & frame.close.gt(frame.close.shift(1)) & location.ge(0.6)
    short_body = (
        frame.close.lt(frame.open) & frame.close.lt(frame.close.shift(1)) & location.le(0.4)
    )
    frame["enter_long"], frame["enter_short"], frame["enter_tag"] = 0, 0, ""
    for tag, allowed in [("small", small), ("core", core)]:
        if not allowed:
            continue
        lower, upper = low - gaps[tag], high + gaps[tag]
        prior_long = (
            (known & frame.close.lt(lower)).shift(1, fill_value=False).rolling(3).max().eq(1)
        )
        prior_short = (
            (known & frame.close.gt(upper)).shift(1, fill_value=False).rolling(3).max().eq(1)
        )
        long = (
            known & prior_long & frame.close.gt(lower) & frame.close.gt(frame.trend_4h) & long_body
        )
        short = (
            known
            & prior_short
            & frame.close.lt(upper)
            & frame.close.lt(frame.trend_4h)
            & short_body
        )
        long &= ~long.shift(1, fill_value=False)
        short &= ~short.shift(1, fill_value=False)
        # Core gets the same priority as in the frozen original account.
        frame.loc[long | short, ["enter_long", "enter_short"]] = 0
        frame.loc[long, ["enter_long", "enter_tag"]] = [1, f"{tag}_inventory"]
        frame.loc[short, ["enter_short", "enter_tag"]] = [1, f"{tag}_inventory"]
    return frame


class InventoryRecovered(InventoryGrid3):
    def populate_entry_trend(self, dataframe, metadata):
        return recovered_signals(dataframe, self.allow_small, self.allow_core)


class InventoryRiskTarget(InventoryGrid3):
    def custom_roi(self, pair, trade, current_time, trade_duration, entry_tag, side, **kwargs):
        plan = trade.get_custom_data("campaign")
        if plan is None or trade.open_trade_value <= 0:
            return None
        sign = -1 if trade.is_short else 1
        stop = plan["initial_stop"]
        loss = trade.amount * (
            -sign * (stop - trade.open_rate)
            + trade.fee_open * trade.open_rate
            + trade.fee_close * stop
        ) - min(float(trade.funding_fees or 0), 0)
        multiple = 0.75 if entry_tag == "small_inventory" else 1.5
        # Native profit_ratio = net cash PnL / fee-adjusted open value * leverage.
        # This targets net cash, including both fees, without using future funding.
        roi = max(loss, 0) * multiple / trade.open_trade_value * trade.leverage
        return float(roi) if math.isfinite(roi) and roi > 0 else None


class InventoryRecoveredRisk(InventoryRiskTarget):
    def populate_entry_trend(self, dataframe, metadata):
        return recovered_signals(dataframe, self.allow_small, self.allow_core)
