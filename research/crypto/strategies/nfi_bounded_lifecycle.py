"""NFI entry/management signals under fixed campaign budgets and complete-exit controls."""

import math

import pandas as pd
from nfi2023_compact import NFI2023OneXCompact

from freqtrade.persistence import Trade
from freqtrade.strategy import stoploss_from_absolute


def layer(tag):
    tokens = set((tag or "").split())
    allowed = {
        str(i) for i in [*range(1, 10), 21, 22, 23, *range(41, 46), 61, 81, 82, *range(101, 106)]
    }
    if not tokens or not tokens <= allowed:
        return None
    return "core" if tokens == {"61"} else "small"


class NFIBoundedPartial(NFI2023OneXCompact):
    futures_mode_leverage = 2.0
    futures_mode_leverage_rebuy_mode = 2.0
    use_custom_stoploss = True
    max_entry_position_adjustment = 1
    hold_support_enabled = False
    reduction_mode = "partial"

    def populate_indicators(self, dataframe, metadata):
        if self.dp.runmode.value != "backtest":
            raise ValueError("Bounded NFI lifecycle is an unqualified research experiment")
        frame = super().populate_indicators(dataframe, metadata)
        previous = frame.close.shift(1)
        frame["bounded_atr"] = (
            pd.concat(
                [
                    frame.high - frame.low,
                    (frame.high - previous).abs(),
                    (frame.low - previous).abs(),
                ],
                axis=1,
            )
            .max(axis=1)
            .rolling(14, min_periods=14)
            .mean()
        )
        return frame

    def closed_row(self, pair, when):
        frame, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        clock = pd.Timestamp(when).floor("5min") - pd.Timedelta(minutes=5)
        rows = frame.loc[frame.date.eq(clock)]
        return rows.iloc[0] if len(rows) == 1 else None

    @staticmethod
    def distance(atr, rate, sleeve):
        if rate <= 0 or not math.isfinite(atr) or atr <= 0:
            return None
        floor, multiple, ceiling = (0.06, 4, 0.12) if sleeve == "small" else (0.10, 8, 0.20)
        value = max(floor, multiple * atr / rate)
        return value if value <= ceiling else None

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        sleeve = layer(entry_tag)
        if sleeve is None or side != "long" or self.dp.runmode.value != "backtest":
            return False
        if sum(layer(t.enter_tag) == sleeve for t in Trade.get_trades_proxy(is_open=True)) >= 2:
            return False
        return super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, **kwargs
        )

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
        sleeve, row = layer(entry_tag), self.closed_row(pair, current_time)
        if sleeve is None or row is None or side != "long" or leverage != 2:
            return 0.0
        distance = self.distance(row.bounded_atr, current_rate, sleeve)
        if distance is None:
            return 0.0
        # Absolute reference budgets avoid reinvesting open-position partial profits.
        reference = float(self.config["research_initial_equity_usdt"]) * 0.99
        budget = reference * (0.001 if sleeve == "small" else 0.005)
        margin = reference * (0.02 if sleeve == "small" else 0.20)
        fee = float(self.config["fee"])
        loss = distance + fee * (2 - distance)
        # Reserve for one more fill at no more than the first stake/price; one-percent buffer.
        stake = min(0.99 * budget / (2 * leverage * loss), margin / 2, max_stake)
        if min_stake is not None and stake < min_stake:
            return 0.0
        if not hasattr(self, "_bounded_pending"):
            self._bounded_pending = {}
        self._bounded_pending[pair] = {
            "sleeve": sleeve,
            "risk_budget": budget,
            "margin_cap": margin,
            "source_atr": float(row.bounded_atr),
        }
        return float(stake)

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if order.ft_order_side != trade.entry_side or trade.nr_of_successful_entries != 1:
            return
        state = getattr(self, "_bounded_pending", {}).pop(pair, None)
        if state is None:
            return
        distance = self.distance(state["source_atr"], trade.open_rate, state["sleeve"])
        if distance is None:
            return
        state.update(
            first_price=float(trade.open_rate),
            first_stake=float(trade.stake_amount),
            initial_stop=float(trade.open_rate * (1 - distance)),
        )
        trade.set_custom_data("bounded_nfi_campaign", state)

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        state = trade.get_custom_data("bounded_nfi_campaign")
        return (
            None
            if state is None
            else stoploss_from_absolute(
                state["initial_stop"], current_rate, trade.is_short, trade.leverage
            )
        )

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        state = trade.get_custom_data("bounded_nfi_campaign")
        if state is None:
            return "missing_bounded_nfi_state"
        minutes = (current_time - trade.open_date_utc).total_seconds() / 60
        if minutes >= (360 if state["sleeve"] == "small" else 4320):
            return "bounded_nfi_time_limit"
        return super().custom_exit(
            pair, trade, current_time, current_rate, current_profit, **kwargs
        )

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
        # The parent rejects stop_loss; this experiment permits every risk/full-exit reason.
        self._remove_profit_target(pair)
        return True

    def full_reduction(self, trade, state, rate):
        if self.reduction_mode == "full":
            return True
        return (
            self.reduction_mode == "dual"
            and state["sleeve"] == "small"
            and trade.calculate_profit(rate).total_profit > 0
        )

    def adjust_trade_position(
        self,
        trade,
        current_time,
        current_rate,
        current_profit,
        min_stake,
        max_stake,
        current_entry_rate,
        current_exit_rate,
        current_entry_profit,
        current_exit_profit,
        **kwargs,
    ):
        state = trade.get_custom_data("bounded_nfi_campaign")
        if state is None or trade.has_open_orders:
            return None
        proposal = super().adjust_trade_position(
            trade,
            current_time,
            current_rate,
            current_profit,
            min_stake,
            max_stake,
            current_entry_rate,
            current_exit_rate,
            current_entry_profit,
            current_exit_profit,
            **kwargs,
        )
        if proposal is None:
            return None
        amount = float(proposal[0] if isinstance(proposal, tuple) else proposal)
        if not math.isfinite(amount) or amount == 0:
            return None
        if amount < 0:
            if self.full_reduction(trade, state, current_exit_rate):
                return -float(trade.stake_amount), "bounded_full_reduction"
            return -min(-amount, float(trade.stake_amount)), "bounded_partial_reduction"
        if trade.nr_of_successful_entries >= 2 or trade.nr_of_successful_exits:
            return None
        stop = state["initial_stop"]
        if not stop < current_entry_rate <= state["first_price"]:
            return None
        loss = trade.amount * (
            (trade.open_rate - stop) + trade.fee_open * trade.open_rate + trade.fee_close * stop
        )
        loss += max(-float(trade.realized_profit), 0) - min(float(trade.funding_fees or 0), 0)
        per_stake = trade.leverage * (
            (1 - stop / current_entry_rate)
            + trade.fee_open
            + trade.fee_close * stop / current_entry_rate
        )
        stake = min(
            amount,
            state["first_stake"],
            state["margin_cap"] - trade.stake_amount,
            max(0, state["risk_budget"] - loss) / per_stake,
            max_stake,
        )
        if stake <= 0 or (min_stake is not None and stake < min_stake):
            return None
        return float(stake), "bounded_nfi_add"


class NFIBoundedFullExit(NFIBoundedPartial):
    reduction_mode = "full"


class NFIBoundedDualExit(NFIBoundedPartial):
    reduction_mode = "dual"
