"""Minute-observed small reward extension; parent signals and capital sizing stay fixed."""

import pandas as pd

from research.crypto.strategies.intrabar_small import IntrabarSmallBase
from research.crypto.strategies.signal_lease import SignalLeaseBase


class FineExitBase(IntrabarSmallBase):
    small_reward_r = 0.25
    retain_small = False

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        super().order_filled(pair, trade, order, current_time, **kwargs)
        if order.ft_order_side != trade.entry_side:
            return
        row = self.closed_row(pair, trade.open_date_utc)
        expiry = pd.Timestamp(row.parent_date) + pd.Timedelta(hours=24, minutes=5)
        trade.set_custom_data("parent_expiry", expiry.isoformat())
        if trade.enter_tag == "small_first_hit":
            one_r = trade.get_custom_data("target_roi") * 4
            trade.set_custom_data("small_one_r_roi", one_r)
            trade.set_custom_data("target_roi", one_r * self.small_reward_r)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        expiry = trade.get_custom_data("parent_expiry")
        if expiry is None:
            return "missing_parent_expiry"
        if pd.Timestamp(current_time) >= pd.Timestamp(expiry):
            return "parent_lease_time"
        original = super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )
        if trade.enter_tag == "core_first_hit":
            # Core profit checkpoints remain5minute observations; its hardstop is finer.
            if original == "first_hit_profit" and current_time.minute % 5:
                return None
            return original
        if original is not None or not self.retain_small:
            return original
        one_r = trade.get_custom_data("small_one_r_roi")
        previous = trade.get_custom_data("small_observed_peak")
        peak = max(float(previous) if previous is not None else current_profit, current_profit)
        trade.set_custom_data("small_observed_peak", peak)
        if peak >= one_r * 0.25 and current_profit <= one_r * 0.1:
            return "small_profit_retrace"
        return None


class FineLeaseBase(FineExitBase, SignalLeaseBase):
    maximum_cycles = 4
    protect_core = False

    def confirm_trade_entry(self, *args, **kwargs):
        before = len(getattr(self, "_rank_decisions", []))
        result = super().confirm_trade_entry(*args, **kwargs)
        if len(getattr(self, "_rank_decisions", [])) > before:
            when = kwargs.get("current_time", args[5] if len(args) > 5 else None)
            self._rank_decisions[-1]["decision_time"] = pd.Timestamp(when).isoformat()
        return result


class FineLeaseQuarter(FineLeaseBase):
    pass


class FineLeaseOneR(FineLeaseBase):
    small_reward_r = 1.0


class FineLeaseRetain(FineLeaseOneR):
    retain_small = True
