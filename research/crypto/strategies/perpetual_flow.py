"""Public-flow perpetual hypotheses with fill-owned and bounded profit rearming."""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, sha
from freqtrade.strategy import IStrategy, stoploss_from_absolute


def simple_atr(frame):
    previous = frame.close.shift(1)
    ranges = pd.concat(
        [frame.high - frame.low, (frame.high - previous).abs(), (frame.low - previous).abs()],
        axis=1,
    ).max(axis=1)
    return ranges.rolling(14, min_periods=14).mean()


def price_profit(trade, current_rate):
    sign = -1 if trade.is_short else 1
    return sign * (current_rate / trade.open_rate - 1)


class PerpFlowFast(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    startup_candle_count = 200
    can_short = True
    position_adjustment_enable = False
    process_only_new_candles = True
    use_custom_stoploss = True
    stoploss = -0.05
    minimal_roi = {"0": 1000.0}
    risk_fraction = 0.0025
    full_profit_fraction = 0.01

    def bot_start(self, **kwargs):
        mode = getattr(self.config.get("runmode"), "value", "")
        if mode in {"live", "dry_run"}:
            raise ValueError(
                "Research public-flow adapter needs a validated current-bar feed before runtime"
            )
        self.panel = Path(self.config["research_flow_panel"])
        if sha(self.panel / "receipts.json") != self.config["research_flow_receipts_sha256"]:
            raise ValueError("Public-flow panel receipt changed")
        self.panel_receipts = read(self.panel / "receipts.json")

    def populate_indicators(self, dataframe, metadata):
        symbol = metadata["pair"].split("/")[0] + "USDT"
        file = self.panel / f"{symbol}.parquet"
        if sha(file) != self.panel_receipts[file.name]:
            raise ValueError("Public-flow feature file changed")
        features = pd.read_parquet(
            file,
            filters=[("date", ">=", dataframe.date.min()), ("date", "<=", dataframe.date.max())],
        )
        features = features.rename(columns={"entry": "flow_entry"})
        dataframe = dataframe.merge(features, on="date", how="left", validate="one_to_one")
        dataframe["risk_atr"] = simple_atr(dataframe)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        valid = dataframe.risk_atr.gt(0) & dataframe.volume.gt(0)
        dataframe["enter_long"] = (valid & dataframe.flow_entry.eq(1)).astype(int)
        dataframe["enter_short"] = (valid & dataframe.flow_entry.eq(-1)).astype(int)
        dataframe["enter_tag"] = "public_flow_fresh_5m"
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def closed_row(self, pair, when):
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        clock = pd.Timestamp(when).floor("5min") - pd.Timedelta(minutes=5)
        rows = frame.loc[frame.date.eq(clock)]
        return rows.iloc[0] if len(rows) == 1 else None

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
        row = self.closed_row(pair, current_time)
        if row is None or current_rate <= 0 or not math.isfinite(row.risk_atr):
            return 0.0
        distance = 2 * row.risk_atr / current_rate
        if not 0 < distance < 0.05:
            return 0.0
        target = self.wallets.get_total_stake_amount() * self.risk_fraction / (distance * leverage)
        stake = min(target, proposed_stake, max_stake)
        return float(stake) if min_stake is None or stake >= min_stake else 0.0

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side == trade.entry_side:
            if trade.get_custom_data("fixed_stop_price") is None:
                row = self.closed_row(pair, trade.open_date_utc)
                if row is not None and np.isfinite(row.risk_atr) and row.risk_atr > 0:
                    stop = trade.open_rate + (
                        2 * row.risk_atr if trade.is_short else -2 * row.risk_atr
                    )
                    trade.set_custom_data("fixed_stop_price", float(stop))
                    trade.set_custom_data("initial_margin", float(trade.stake_amount))
            if trade.nr_of_successful_entries > 1:
                trade.set_custom_data("profit_authorized_add_order", order.order_id)
        elif (
            order.ft_order_side == trade.exit_side
            and trade.get_custom_data("harvest_fill_time") is None
        ):
            trade.set_custom_data("harvest_fill_time", pd.Timestamp(current_time).isoformat())

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        stop = trade.get_custom_data("fixed_stop_price")
        if stop is None:
            return None
        return stoploss_from_absolute(
            stop, current_rate, is_short=trade.is_short, leverage=trade.leverage
        )

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if trade.get_custom_data("fixed_stop_price") is None:
            return "risk_state_unavailable"
        if (current_time - trade.open_date_utc).total_seconds() >= 3600:
            return "campaign_time_limit"
        if price_profit(trade, current_rate) >= self.full_profit_fraction:
            return "full_profit_harvest"
        row = self.closed_row(pair, current_time)
        if row is None or not np.isfinite([row.residual_20_pct, row.flow_5m]).all():
            return None
        sign = -1 if trade.is_short else 1
        if sign * row.residual_20_pct <= 0 or sign * row.flow_5m <= 0:
            return "public_confirmation_lost"
        return None


class PerpFlowFunded(PerpFlowFast):
    position_adjustment_enable = True
    max_entry_position_adjustment = 1
    full_profit_fraction = 0.02

    def adjust_trade_position(
        self, trade, current_time, current_rate, current_profit, min_stake, max_stake, **kwargs
    ):
        if trade.has_open_orders or trade.nr_of_successful_entries > 1:
            return None
        if trade.nr_of_successful_exits == 0:
            if price_profit(trade, current_rate) >= 0.01:
                return -(trade.stake_amount / 2), "half_profit_harvest"
            return None
        credit = trade.realized_profit
        harvested = trade.get_custom_data("harvest_fill_time")
        stop = trade.get_custom_data("fixed_stop_price")
        initial = trade.get_custom_data("initial_margin")
        if not credit or credit <= 0 or not harvested or stop is None or initial is None:
            return None
        row = self.closed_row(trade.pair, current_time)
        if row is None or row.date < pd.Timestamp(harvested).ceil("5min"):
            return None
        sign = -1 if trade.is_short else 1
        if row.direction != sign:
            return None
        distance = sign * (current_rate - stop) / current_rate
        if not 0 < distance < 0.05:
            return None
        stake = min(initial / 2, credit / (distance * trade.leverage), max_stake)
        if stake <= 0 or (min_stake is not None and stake < min_stake):
            return None
        return float(stake), "realized_profit_authorized_add"
