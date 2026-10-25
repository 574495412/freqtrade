"""Finite parent-signal leases, profitable complete-position recycling and core protection."""

import math
from pathlib import Path

import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.research.data import sha
from research.crypto.strategies.intrabar_small import IntrabarSmallBase
from research.crypto.strategies.ranked_first_hit import rank_pool


def cycle_history(trades):
    history = {}
    for trade in trades:
        if trade.enter_tag != "small_first_hit":
            continue
        lease = trade.get_custom_data("lease_id")
        if lease is None:
            raise ValueError("Small position has no parent lease")
        record = history.setdefault(
            str(lease), {"entries": 0, "blocked": False, "last_close": None}
        )
        record["entries"] += 1
        if not trade.is_open:
            value, clock = trade.close_profit_abs, trade.close_date_utc
            if value is None or not math.isfinite(value) or clock is None:
                raise ValueError("Closed position lacks final net PNL or close time")
            record["blocked"] |= value <= 0
            record["last_close"] = max(record["last_close"] or clock, clock)
    return history


def eligible_leases(frame, history, clock, maximum):
    accepted = []
    for row in frame.itertuples():
        state = history.get(str(row.lease_id), {})
        last = state.get("last_close")
        accepted.append(
            not state.get("blocked", False)
            and state.get("entries", 0) < maximum
            and (last is None or clock > last + pd.Timedelta(minutes=5))
        )
    return frame.loc[accepted].copy()


class LeaseExitBase(IntrabarSmallBase):
    protect_core = False

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        original = super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )
        if original is not None or trade.enter_tag != "core_first_hit" or not self.protect_core:
            return original
        target = trade.get_custom_data("target_roi")
        previous = trade.get_custom_data("observed_open_profit_peak")
        peak = max(float(previous) if previous is not None else current_profit, current_profit)
        trade.set_custom_data("observed_open_profit_peak", peak)
        # Only the current open's actual net profit is observed, never this bar's high/low.
        if peak >= target / 2 and current_profit <= target / 4:
            return "core_profit_lock"
        return None


class SignalLeaseBase(LeaseExitBase):
    policy = "CoreParent"
    maximum_cycles = 1

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        super().order_filled(pair, trade, order, current_time, **kwargs)
        if order.ft_order_side == trade.entry_side:
            row = self.closed_row(pair, trade.open_date_utc)
            if row is None or not isinstance(row.lease_id, str):
                raise ValueError("Entry has no causal parent identity")
            trade.set_custom_data("lease_id", row.lease_id)

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        if not super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
        ):
            return False
        if not hasattr(self, "_lease_rank"):
            source = self.config["research_first_hit_panels"][self.policy]
            path = Path(source["ranking_path"])
            if sha(path) != source["ranking_sha256"]:
                raise ValueError("Frozen lease packet changed")
            self._lease_rank = pd.read_parquet(path)
            self._rank_allowed, self._rank_decisions = {}, []
        clock = pd.Timestamp(current_time).floor("5min")
        key = (clock, entry_tag)
        if key not in self._rank_allowed:
            opened = Trade.get_trades_proxy(is_open=True)
            pairs = sorted(t.pair for t in opened)
            layer = sum(t.enter_tag == entry_tag for t in opened)
            source_date = clock - pd.Timedelta(minutes=5)
            pool = self._lease_rank.loc[self._lease_rank.date.eq(source_date)].copy()
            if entry_tag == "small_first_hit":
                pool = eligible_leases(
                    pool, cycle_history(Trade.get_trades_proxy()), clock, self.maximum_cycles
                )
            ranked, slots = rank_pool(
                pool,
                source_date,
                entry_tag,
                pairs,
                layer,
                len(opened),
                float(self.config["fee"]),
                float(self.config["research_initial_equity_usdt"]) * 0.99,
            )
            self._rank_allowed[key] = {r.pair: int(r.forecast_side) for r in ranked.itertuples()}
            self._rank_decisions.append(
                {
                    "clock": clock.isoformat(),
                    "trigger_pair": pair,
                    "source_date": source_date.isoformat(),
                    "tag": entry_tag,
                    "open_pairs": pairs,
                    "open_layer": layer,
                    "open_total": len(opened),
                    "slots": slots,
                    "allowed": ranked[
                        [
                            "pair",
                            "symbol",
                            "forecast_side",
                            "row_id",
                            "lease_id",
                            "estimated_profit",
                            "score_key",
                        ]
                    ].to_dict("records"),
                }
            )
        return self._rank_allowed[key].get(pair) == (1 if side == "long" else -1)


class LeaseOnce(SignalLeaseBase):
    pass


class LeaseRecycle(SignalLeaseBase):
    maximum_cycles = 4


class LeaseOnceProtect(SignalLeaseBase):
    protect_core = True


class LeaseRecycleProtect(LeaseRecycle):
    protect_core = True
