"""Supported IStrategy policy for fixed original head/shoulders events."""

import numpy as np
import pandas as pd
from HeadShouldersInputs import (
    assembled_history,
    event_records,
    original_source,
    projected_indicators,
)

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class HeadShouldersStrategy(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "1h"
    startup_candle_count = 0
    process_only_new_candles = True
    minimal_roi = {}
    stoploss = -0.99
    trailing_stop = False
    position_adjustment_enable = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    order_types = {
        "entry": "market",
        "exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    def _context(self):
        mode = getattr(self.config.get("runmode"), "value", self.config.get("runmode"))
        if (
            not self.config.get("research_head_shoulders")
            or self.config.get("dry_run") is not True
            or self.config.get("trading_mode") != "futures"
            or mode != "backtest"
            or self.config.get("max_open_trades") != 6
            or self.config.get("position_stacking", False)
        ):
            raise ValueError("Fixed isolated historical head/shoulders interface only")

    def bot_start(self, **kwargs):
        self._context()
        self._source, self._author = original_source(self.config)
        self._events = {}
        self._pair_events = {}
        self._blocked = set()
        self._entry_consumed = set()
        self._exit_seen = {}
        self._exit_reason = {}
        self._exit_clock = {}
        self.policy_audit = []
        self.callback_errors = []

    def _audit(self, status, event=None, now=None, **extra):
        self.policy_audit.append(
            {
                "status": status,
                "event_id": event["event_id"] if event else None,
                "pair": event["pair"] if event else extra.pop("pair", None),
                "at": str(now) if now is not None else None,
                **extra,
            }
        )

    def populate_indicators(self, dataframe, metadata):
        self._context()
        pair = metadata["pair"]
        if pair not in self.config["exchange"]["pair_whitelist"]:
            raise ValueError("Pair outside the frozen ordered pool")
        combined, before = assembled_history(dataframe, pair, self.config, self._source)
        records = event_records(combined, pair, self._source, self._author)
        old = self._pair_events.get(pair, [])
        for event in old:
            self._events.pop(event["event_id"], None)
        self._pair_events[pair] = records
        for event in records:
            self._events[event["event_id"]] = event
        projected = projected_indicators(combined, records).iloc[before:]
        for name in ["hs_event_id", "hs_direction", "hs_self_terminal"]:
            dataframe[name] = projected[name].to_numpy(copy=True)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        executable = dataframe.hs_event_id.ne("") & ~dataframe.hs_self_terminal
        dataframe["enter_long"] = (executable & dataframe.hs_direction.eq(1)).astype(int)
        dataframe["enter_short"] = (executable & dataframe.hs_direction.eq(-1)).astype(int)
        dataframe["enter_tag"] = dataframe.hs_event_id.where(executable, None)
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        dataframe["exit_tag"] = None
        return dataframe

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
        return 1.0

    @staticmethod
    def _utc(now):
        value = pd.Timestamp(now)
        if value.tzinfo is None:
            raise ValueError("Callback time must be timezone-aware")
        return value.tz_convert("UTC")

    def _closed(self, pair, now):
        data, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if data.empty:
            return data
        if data.date.duplicated().any() or not data.date.is_monotonic_increasing:
            raise ValueError("Analyzed callback rows must be ordered and unique")
        return data.loc[data.date + pd.Timedelta(hours=1) <= now]

    def bot_loop_start(self, current_time, **kwargs):
        try:
            now = self._utc(current_time)
            self._context()
            open_pairs = {trade.pair for trade in Trade.get_trades_proxy(is_open=True)}
            for pair, records in self._pair_events.items():
                for event in records:
                    if event["entry_available"] != now:
                        continue
                    if event["self_terminal_at_confirmation"]:
                        self._blocked.add(event["event_id"])
                        self._audit("MASKED_SELF_TERMINAL", event, now)
                    elif pair in open_pairs:
                        self._blocked.add(event["event_id"])
                        self._audit("IGNORED_EXISTING_POSITION", event, now)
        except Exception as error:
            self.callback_errors.append({"callback": "bot_loop_start", "error": str(error)})

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        try:
            self._context()
            now = self._utc(current_time)
            event = self._events.get(entry_tag)
            if event is None or event["pair"] != pair or event["side"] != side:
                self._audit("REJECT_INVALID_EVENT_BINDING", now=now, pair=pair, tag=entry_tag)
                return False
            if event["self_terminal_at_confirmation"]:
                self._audit("MASKED_SELF_TERMINAL", event, now)
                return False
            if now != event["entry_available"]:
                self._audit("REJECT_NONINITIAL_ENTRY_CLOCK", event, now)
                return False
            if entry_tag in self._blocked or entry_tag in self._entry_consumed:
                self._audit("REJECT_BLOCKED_OR_REPEATED_EVENT", event, now)
                return False
            trades = Trade.get_trades_proxy(pair=pair, is_open=True)
            closed_now = [
                trade
                for trade in Trade.get_trades_proxy(pair=pair, is_open=False)
                if trade.close_date_utc == now
            ]
            if trades or closed_now:
                self._blocked.add(entry_tag)
                self._audit("IGNORED_EXISTING_OR_JUST_CLOSED_POSITION", event, now)
                return False
            data = self._closed(pair, now)
            match = data.loc[data.date == event["confirmation_bar_open"]]
            if len(match) != 1 or match.iloc[0].hs_event_id != entry_tag:
                self._audit("REJECT_CONFIRMATION_NOT_AVAILABLE", event, now)
                return False
            if order_type != "market":
                self._audit("REJECT_NONMARKET_ENTRY", event, now)
                return False
            self._entry_consumed.add(entry_tag)
            self._audit("ENTRY_CONFIRMED_INTERFACE_ONLY", event, now)
            return True
        except Exception as error:
            self.callback_errors.append({"callback": "confirm_trade_entry", "error": str(error)})
            return False  # Native wrapper otherwise defaults to True on an exception.

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        try:
            self._context()
            now = self._utc(current_time)
            event = self._events.get(trade.enter_tag)
            if (
                event is None
                or event["pair"] != pair
                or event["inverted"] == trade.is_short
                or event["self_terminal_at_confirmation"]
                or pd.Timestamp(trade.open_date_utc) != event["entry_available"]
            ):
                raise ValueError("Trade is not bound to its original executable event")
            key = (pair, trade.enter_tag, str(trade.open_date_utc))
            if now < event["entry_available"]:
                raise ValueError("Exit callback precedes source event availability")
            if key in self._exit_reason:
                if now < self._exit_clock[key]:
                    raise ValueError("Callback clock moved behind an observed exit")
                return self._exit_reason[key]
            data = self._closed(pair, now)
            previous = self._exit_seen.get(
                key, event["confirmation_bar_open"] - pd.Timedelta(hours=1)
            )
            fresh = data.loc[
                (data.date > previous)
                & (data.date >= event["confirmation_bar_open"])
                & (data.date <= event["expiry_bar_open"])
            ]
            for row in fresh.itertuples():
                if row.date != previous + pd.Timedelta(hours=1):
                    raise ValueError("Completed source-close history is missing at callback")
                price = float(np.log(row.close))
                if not np.isfinite(price):
                    raise ValueError("Invalid completed close")
                target = event["source_target_log_price"]
                stop = event["source_stop_log_price"]
                hit_target = price > target if event["inverted"] else price < target
                hit_stop = price < stop if event["inverted"] else price > stop
                reason = (
                    "hs_source_target" if hit_target else "hs_source_stop" if hit_stop else None
                )
                if reason is None and row.date == event["expiry_bar_open"]:
                    reason = "hs_source_expiry"
                previous = row.date
                self._exit_seen[key] = previous
                if reason:
                    self._exit_reason[key] = reason
                    self._exit_clock[key] = row.date + pd.Timedelta(hours=1)
                    self._audit(reason, event, now, source_close_bar=str(row.date))
                    return reason
            expected = min(now.floor("h") - pd.Timedelta(hours=1), event["expiry_bar_open"])
            if previous < expected:
                raise ValueError("Completed source-close history is stale at callback")
            return None
        except Exception as error:
            self.callback_errors.append({"callback": "custom_exit", "error": str(error)})
            return "hs_policy_error_exit"  # Separate invalid-policy reason; never a source exit.
