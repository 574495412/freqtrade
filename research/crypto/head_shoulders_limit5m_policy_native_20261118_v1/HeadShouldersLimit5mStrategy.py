"""Independent fixed LIMIT control; inherits the original source exit algorithm."""

from HeadShouldersLimit5mInputs import (
    NATIVE_START,
    OriginalStrategy,
    closed_hourly,
    prepared_source,
)
from freqtrade.persistence import Trade


class HeadShouldersLimit5mStrategy(OriginalStrategy):
    timeframe = "5m"
    startup_candle_count = 800
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    def _context(self):
        super()._context()
        timeout = self.config.get("unfilledtimeout", {})
        if (
            not self.config.get("research_head_shoulders_limit5m")
            or self.config.get("timeframe") != "5m"
            or self.config.get("timeframe_detail", "") != ""
            or self.config.get("timerange") != "1735689600-1788220799"
            or self.startup_candle_count != 800
            or timeout.get("entry") != 3
            or timeout.get("exit") != 2
            or timeout.get("unit") != "minutes"
            or self.order_types["entry"] != "limit"
            or self.order_types["exit"] != "limit"
            or self.order_time_in_force != {"entry": "GTC", "exit": "GTC"}
            or self.config.get("research_idle_auxiliary_enabled", False)
        ):
            raise ValueError("Fixed independent 5m LIMIT execution control only")

    def populate_indicators(self, dataframe, metadata):
        self._context()
        pair = metadata["pair"]
        if pair not in self.config["exchange"]["pair_whitelist"]:
            raise ValueError("Pair outside original ordered pool")
        merged, records = prepared_source(dataframe, pair, self.config, self._source, self._author)
        for event in self._pair_events.get(pair, []):
            self._events.pop(event["event_id"], None)
        self._pair_events[pair] = records
        self._events.update({event["event_id"]: event for event in records})
        return merged

    def _closed(self, pair, now):
        data, _ = self.dp.get_analyzed_dataframe(pair, "5m")
        return closed_hourly(data, now)

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
            if now != event["entry_available"] or now <= NATIVE_START:
                self._audit("REJECT_NONINITIAL_ENTRY_CLOCK", event, now)
                return False
            if entry_tag in self._blocked or entry_tag in self._entry_consumed:
                self._audit("REJECT_BLOCKED_OR_REPEATED_EVENT", event, now)
                return False
            open_trades = Trade.get_trades_proxy(pair=pair, is_open=True)
            closed_now = [
                trade
                for trade in Trade.get_trades_proxy(pair=pair, is_open=False)
                if trade.close_date_utc == now
            ]
            if open_trades or closed_now:
                self._blocked.add(entry_tag)
                self._audit("IGNORED_EXISTING_OR_JUST_CLOSED_POSITION", event, now)
                return False
            data = self._closed(pair, now)
            match = data.loc[data.date == event["confirmation_bar_open"]]
            if len(match) != 1 or match.iloc[0].hs_event_id != entry_tag:
                self._audit("REJECT_CONFIRMATION_NOT_AVAILABLE", event, now)
                return False
            if order_type != "limit" or time_in_force != "GTC":
                self._audit("REJECT_NONLIMIT_GTC_ENTRY", event, now)
                return False
            self._entry_consumed.add(entry_tag)
            self._audit("ENTRY_LIMIT_REQUEST_CONFIRMED", event, now, fill_status="UNKNOWN")
            return True
        except Exception as error:
            self.callback_errors.append({"callback": "confirm_trade_entry", "error": str(error)})
            return False

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        key = (pair, trade.enter_tag, str(trade.open_date_utc))
        previous = key in self._exit_reason
        reason = super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )
        if not previous and key in self._exit_reason:
            self._audit(
                "SOURCE_DECISION_OBSERVED",
                self._events[trade.enter_tag],
                self._utc(current_time),
                source_reason=reason,
                source_available=str(self._exit_clock[key]),
                fill_status="UNKNOWN",
            )
        return reason

    def confirm_trade_exit(
        self,
        pair,
        trade,
        order_type,
        amount,
        rate,
        time_in_force,
        exit_reason,
        current_time,
        **kwargs,
    ):
        try:
            self._context()
            now = self._utc(current_time)
            key = (pair, trade.enter_tag, str(trade.open_date_utc))
            available = self._exit_clock.get(key)
            source_reason = exit_reason in {
                "hs_source_target",
                "hs_source_stop",
                "hs_source_expiry",
            }
            if source_reason and (
                available is None
                or self._exit_reason.get(key) != exit_reason
                or now < available
                or order_type != "limit"
                or time_in_force != "GTC"
            ):
                raise ValueError("Source exit LIMIT request is not bound to an observed decision")
            self._audit(
                "EXIT_REQUEST_CONFIRMED",
                self._events.get(trade.enter_tag),
                now,
                pair=pair,
                reason=exit_reason,
                order_type=order_type,
                time_in_force=time_in_force,
                source_available=str(available) if available is not None else None,
                request_delay_seconds=(now - available).total_seconds() if source_reason else None,
                fill_status="UNKNOWN",
            )
            return True
        except Exception as error:
            self.callback_errors.append({"callback": "confirm_trade_exit", "error": str(error)})
            return False
