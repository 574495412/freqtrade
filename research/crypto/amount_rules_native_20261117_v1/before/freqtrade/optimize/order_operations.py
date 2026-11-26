"""Local native backtest permissions, independent of pending-order matching."""

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType
from freqtrade.optimize.dated_price_precision import DatedPricePrecisionMixin, aware


@dataclass(frozen=True)
class OrderOperationWindow:
    pair: str
    start: datetime
    end: datetime
    known_at: datetime
    source_sha256: str
    pending_matching: str = "unknown"

    def __post_init__(self):
        for clock in (self.start, self.end, self.known_at):
            aware(clock)
        if self.start >= self.end or self.known_at > self.start:
            raise ValueError("Known nonempty operation window required")
        if self.pending_matching not in ("unknown", "continue", "stop"):
            raise ValueError("Explicit pending matching policy required")
        if len(self.source_sha256) != 64:
            raise ValueError("Window source SHA256 required")
        int(self.source_sha256, 16)


class OrderOperationMixin(DatedPricePrecisionMixin):
    """Actual native matching/wallets remain inherited; no operational callbacks."""

    def setup_order_operations(self, book, windows):
        self.setup_dated_price_rules(book)
        if not isinstance(windows, tuple) or not windows:
            raise ValueError("Frozen operation windows required")
        if self.timeframe_detail != "1m" or self.timeframe != "5m":
            raise ValueError("5m decisions with actual1m detail required")
        for window in windows:
            if not isinstance(window, OrderOperationWindow) or window.pair not in book.pairs:
                raise ValueError("Operation window outside frozen universe")
            if any(t.second or t.microsecond for t in (window.start, window.end)):
                raise ValueError("Sub-minute windows require finer native input")
        for i, left in enumerate(windows):
            for right in windows[i + 1 :]:
                if left.pair == right.pair and max(left.start, right.start) < min(
                    left.end, right.end
                ):
                    raise ValueError("Overlapping operation windows")
        self._operation_windows = windows
        self._bound_operation_windows = windows
        self.operation_trace = []

    def _operation_window(self, pair, current_time):
        self._require_dated_context()
        aware(current_time)
        if self._operation_windows is not self._bound_operation_windows:
            raise ValueError("Operation contract changed")
        return next(
            (
                w
                for w in self._operation_windows
                if w.pair == pair and w.start <= current_time < w.end
            ),
            None,
        )

    def _blocked(self, action, pair, current_time):
        window = self._operation_window(pair, current_time)
        if window:
            self.operation_trace.append(
                {"action": action, "pair": pair, "at": current_time.isoformat()}
            )
        return window

    def _load_bt_data_detail(self):
        super()._load_bt_data_detail()
        # Read the raw detail contract independently of native gap filling.
        # Entire5m candles intersecting an event must have genuine1m inputs.
        for window in self._operation_windows:
            raw = load_pair_history(
                pair=window.pair,
                timeframe="1m",
                datadir=self.config["datadir"],
                data_format=self.config["dataformat_ohlcv"],
                candle_type=CandleType.FUTURES,
                fill_up_missing=False,
                drop_incomplete=False,
            )
            start = pd.Timestamp(window.start).floor("5min")
            end = pd.Timestamp(window.end).ceil("5min")
            dates = raw.loc[(raw.date >= start) & (raw.date < end), "date"]
            expected = pd.date_range(start, end, freq="min", inclusive="left")
            if not pd.DatetimeIndex(dates).as_unit("ns").equals(expected.as_unit("ns")):
                raise ValueError("Missing genuine1m input around operation window")

    def backtest_loop(self, row, pair, current_time, trade_dir, can_enter):
        self._operation_window(pair, current_time)
        if row[0] != current_time:
            raise ValueError("Native detail and execution clocks disagree")
        return super().backtest_loop(row, pair, current_time, trade_dir, can_enter)

    def _enter_trade(self, pair, row, direction, *args, **kwargs):
        if self._blocked("new_entry", pair, row[0]):
            return kwargs.get("trade")
        return super()._enter_trade(pair, row, direction, *args, **kwargs)

    def _exit_trade(self, trade, sell_row, close_rate, amount, exit_reason):
        if self._blocked("new_exit", trade.pair, sell_row[0]):
            return None
        return super()._exit_trade(trade, sell_row, close_rate, amount, exit_reason)

    def manage_open_orders(self, trade, current_time, row):
        if self._blocked("manage_cancel_amend", trade.pair, current_time):
            return False
        return super().manage_open_orders(trade, current_time, row)

    def check_order_replace(self, trade, order, current_time, row):
        if self._blocked("replace", trade.pair, current_time):
            return False
        return super().check_order_replace(trade, order, current_time, row)

    def check_order_cancel(self, trade, order, current_time):
        if self._blocked("cancel_check", trade.pair, current_time):
            return False
        return super().check_order_cancel(trade, order, current_time)

    def cancel_open_orders(self, trade, current_time):
        if self._blocked("cancel", trade.pair, current_time):
            return None
        return super().cancel_open_orders(trade, current_time)

    def _try_close_open_order(self, order, trade, current_time, row):
        window = self._operation_window(trade.pair, current_time)
        if window and order:
            if window.pending_matching == "stop":
                return False
            if window.pending_matching == "unknown" and self._get_order_filled(order.ft_price, row):
                raise ValueError("Unknown pending matching policy at reachable old order")
        return super()._try_close_open_order(order, trade, current_time, row)
