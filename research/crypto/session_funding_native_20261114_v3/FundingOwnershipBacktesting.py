"""Local extension of the native funding callbacks for fixed-quantity trades.

Everything except funding ownership delegates to pinned native Backtesting.
Active modes admit one entry/full exit, no partial fills/DCA. They are explicit
nominal-clock research assumptions, not exchange/private-settlement evidence.
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.enums import TradingMode
from freqtrade.optimize.backtesting import Backtesting


class FundingOwnershipBacktesting(Backtesting):
    def __init__(self, *args, funding_ownership="inclusive", **kwargs):
        if funding_ownership not in ["inclusive", "before_orders", "after_orders"]:
            raise ValueError("Unsupported funding ownership")
        self.funding_ownership = funding_ownership
        self._funding_closing_clock = {}
        self.funding_ownership_calls = 0
        super().__init__(*args, **kwargs)

    def _funding_total(self, trade, current_time, closing):
        frame = self.futures_data[trade.pair]
        if frame.empty:
            raise ValueError("No admitted native funding/mark data")
        dates = frame["_ff_date_ns"].to_numpy()
        unit_fees = frame["_ff_fee_per_unit"].to_numpy()
        if not np.isfinite(unit_fees).all() or (np.diff(dates) <= 0).any():
            raise ValueError("Invalid/duplicate native funding event input")
        opened, now = pd.Timestamp(trade.open_date_utc).value, pd.Timestamp(current_time).value
        # Before orders: an event at opening belongs to the previous position.
        lower = "right" if self.funding_ownership == "before_orders" else "left"
        # After orders: an event at a FULL exit belongs to the successor/flat
        # state. For an ongoing position, current-time events accrue normally.
        upper = "left" if closing and self.funding_ownership == "after_orders" else "right"
        first, last = dates.searchsorted(opened, lower), dates.searchsorted(now, upper)
        total = sum((unit_fees[first:last] * trade.amount).tolist())
        if not math.isfinite(total):
            raise ValueError("Invalid funding amount")
        return total if trade.is_short else -total

    def _run_funding_fees(self, trade, current_time, force=False):
        if self.funding_ownership == "inclusive":
            return super()._run_funding_fees(trade, current_time, force=force)
        if self.trading_mode != TradingMode.FUTURES:
            raise ValueError("Funding ownership contract is futures-only")
        if not (force or current_time.timestamp() % self.funding_fee_timeframe_secs == 0):
            return None
        if trade.nr_of_successful_entries != 1 or trade.nr_of_successful_exits > 1:
            raise ValueError("Active ownership does not admit DCA/partial exits")
        closing = self._funding_closing_clock.get(trade.id) == pd.Timestamp(current_time).value
        total = self._funding_total(trade, current_time, closing)
        # Native close_bt_order transfers funding_fee_running into order fees.
        # Preserve that ledger; set only the unallocated remainder, so the
        # same cumulative event is not added twice on a post-fill callback.
        booked = sum(o.funding_fee for o in trade.orders if o.funding_fee)
        trade.set_funding_fees(total - booked)
        if abs(trade.funding_fees - total) > 1e-9:
            raise ValueError("Native trade/order funding identity failed")
        self.funding_ownership_calls += 1
        return None

    def _try_close_open_order(self, order, trade, current_date, row):
        if self.funding_ownership == "inclusive":
            return super()._try_close_open_order(order, trade, current_date, row)
        if order and order.ft_order_side == trade.exit_side:
            if order.amount != trade.amount:
                raise ValueError("Active ownership does not admit partial exits")
            if self._get_order_filled(order.ft_price, row):
                self._funding_closing_clock[trade.id] = pd.Timestamp(current_date).value
                try:
                    # Set the final lifecycle amount BEFORE native order.close
                    # copies the running amount; its native post-fill callback
                    # then sees the same closing policy and no remainder.
                    self._run_funding_fees(trade, current_date, force=True)
                    return super()._try_close_open_order(order, trade, current_date, row)
                finally:
                    self._funding_closing_clock.pop(trade.id, None)
        return super()._try_close_open_order(order, trade, current_date, row)


def write_audit(account, output):
    Path(output, "funding_extension_audit.json").write_text(
        json.dumps(
            {
                "policy": account.funding_ownership,
                "active_native_funding_calls": account.funding_ownership_calls,
                "closing_context_empty": not account._funding_closing_clock,
                "matching_wallet_and_strategy_not_reimplemented": True,
                "nominal_clock_scenario_not_actual_settlement": True,
            },
            indent=2,
        )
        + "\n"
    )
