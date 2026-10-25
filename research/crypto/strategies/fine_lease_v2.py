"""Revalidate each attempted minute entry after the cached five-minute shortlist."""

import pandas as pd

from freqtrade.persistence import Trade
from research.crypto.strategies.fine_lease import FineLeaseOneR, FineLeaseQuarter, FineLeaseRetain
from research.crypto.strategies.signal_lease import cycle_history


class CurrentLeaseGuard:
    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        admitted = super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
        )
        if not admitted or entry_tag != "small_first_hit":
            return admitted
        row = self.closed_row(pair, current_time)
        if row is None:
            return False
        trades = Trade.get_trades_proxy()
        lease = str(row.lease_id)
        state = cycle_history(trades).get(lease, {})
        # The shortlist may predate an earlier detail-minute close in the same5m bucket.
        # Check current complete history again, without promoting a replacement candidate.
        if any(
            t.enter_tag == entry_tag and t.is_open and str(t.get_custom_data("lease_id")) == lease
            for t in trades
        ):
            return False
        last = state.get("last_close")
        return (
            not state.get("blocked", False)
            and state.get("entries", 0) < self.maximum_cycles
            and (
                last is None
                or pd.Timestamp(current_time).floor("5min") > last + pd.Timedelta(minutes=5)
            )
        )


class FineLeaseQuarterV2(CurrentLeaseGuard, FineLeaseQuarter):
    pass


class FineLeaseOneRV2(CurrentLeaseGuard, FineLeaseOneR):
    pass


class FineLeaseRetainV2(CurrentLeaseGuard, FineLeaseRetain):
    pass
