"""Unchanged NFI entries at 1x with fixed loss budgets and bounded lifetime additions."""

import math

from nfi2023_compact import NFI2023OneXCompact

from freqtrade.persistence import Trade
from freqtrade.strategy import stoploss_from_absolute


KEY = "nfi_risk_cap_v1"


def stop_risk(trade, state):
    """Conservative known loss at the locked stop; gaps/future funding remain unbounded."""
    stop = max(state["absolute_stop"], float(trade.stop_loss or 0))
    price_loss = trade.amount * max(
        0, trade.open_rate - stop + trade.fee_open * trade.open_rate + trade.fee_close * stop
    )
    return (
        price_loss
        + max(0, -float(trade.realized_profit or 0))
        + max(0, -float(trade.funding_fees or 0))
    )


class NFIRiskCapOneX(NFI2023OneXCompact):
    stoploss = -0.12
    use_custom_stoploss = True
    hold_support_enabled = False
    max_entry_position_adjustment = 2
    allow_additions = True
    maximum_entries = 3
    maximum_holding_minutes = 14 * 24 * 60

    def reference(self):
        return float(self.config["research_initial_equity_usdt"])

    def remaining_capacity(self):
        risk, margin = 0.0, 0.0
        for trade in Trade.get_trades_proxy(is_open=True):
            state = trade.get_custom_data(KEY)
            if state is None:
                return 0.0, 0.0
            risk += stop_risk(trade, state)
            margin += trade.stake_amount
        return max(0, self.reference() * 0.08 - risk), max(0, self.reference() * 0.80 - margin)

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
        if side != "long" or leverage != 1 or not entry_tag or entry_tag == "force_entry":
            return 0.0
        source = super().custom_stake_amount(
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
        )
        fee = float(self.config["fee"])
        per_stake = 0.12 + fee * 1.88
        risk, margin = self.remaining_capacity()
        # Same initial stake in both variants; reserve for 3 equal entries plus
        # 0.25% of initial equity per campaign for adverse known funding/rounding.
        stake = min(
            source,
            self.reference() * 0.0175 / (3 * per_stake),
            risk / (3 * per_stake),
            self.reference() * 0.20 / 3,
            margin / 3,
            max_stake,
        )
        return float(stake) if stake > 0 and (min_stake is None or stake >= min_stake) else 0.0

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        if side != "long" or entry_tag == "force_entry":
            return False
        risk, margin = self.remaining_capacity()
        fee = float(self.config["fee"])
        if amount * rate > margin or amount * rate * (0.12 + fee * 1.88) > risk:
            return False
        return super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, **kwargs
        )

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        if (
            order.ft_order_side == trade.entry_side
            and trade.nr_of_successful_entries == 1
            and trade.get_custom_data(KEY) is None
        ):
            trade.set_custom_data(
                KEY,
                {
                    "version": 1,
                    "absolute_stop": float(trade.open_rate * 0.88),
                    "first_price": float(trade.open_rate),
                    "first_stake": float(trade.stake_amount),
                    "risk_budget": self.reference() * 0.02,
                    "margin_cap": self.reference() * 0.20,
                    "last_add_clock": None,
                },
            )

    def custom_stoploss(
        self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs
    ):
        state = trade.get_custom_data(KEY)
        if state is None:
            return None
        stop = max(state["absolute_stop"], float(trade.stop_loss or 0))
        return stoploss_from_absolute(stop, current_rate, trade.is_short, trade.leverage)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        state = trade.get_custom_data(KEY)
        if state is None:
            return "risk_cap_missing_state"
        if stop_risk(trade, state) > state["risk_budget"] + 1e-7:
            return "risk_cap_budget_breach"
        if trade.max_stake_amount > state["margin_cap"] + 1e-7:
            return "risk_cap_margin_breach"
        if (
            current_time - trade.open_date_utc
        ).total_seconds() >= self.maximum_holding_minutes * 60:
            return "risk_cap_time_limit"
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
        self._remove_profit_target(pair)
        return True

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
        state = trade.get_custom_data(KEY)
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
            return -min(-amount, trade.stake_amount), "risk_cap_source_reduction"
        clock = int(current_time.timestamp()) // 300
        if (
            not self.allow_additions
            or trade.nr_of_successful_entries >= self.maximum_entries
            or trade.nr_of_successful_exits
            or state["last_add_clock"] == clock
        ):
            return None
        stop = max(state["absolute_stop"], float(trade.stop_loss or 0))
        if not stop < current_entry_rate <= state["first_price"]:
            return None
        per_stake = (
            (1 - stop / current_entry_rate)
            + trade.fee_open
            + (trade.fee_close * stop / current_entry_rate)
        )
        risk, margin = self.remaining_capacity()
        stake = min(
            amount,
            state["first_stake"],
            max_stake,
            margin,
            max(0, state["margin_cap"] - trade.max_stake_amount),
            max(0, state["risk_budget"] - stop_risk(trade, state)) / per_stake,
            risk / per_stake,
        )
        if stake <= 0 or (min_stake is not None and stake < min_stake):
            return None
        state["last_add_clock"] = clock
        trade.set_custom_data(KEY, state)
        return float(stake), "risk_cap_add"


class NFIRiskCapNoDCA(NFIRiskCapOneX):
    allow_additions = False
    max_entry_position_adjustment = 0
