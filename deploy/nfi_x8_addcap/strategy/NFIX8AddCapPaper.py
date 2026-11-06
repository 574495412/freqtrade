"""NFI X8 with a lifetime budget for additional entry orders."""

from decimal import Decimal
from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier


with PathModifier(Path(__file__).resolve().parents[2] / "strategy/nfi-x8"):
    from NostalgiaForInfinityX8 import NostalgiaForInfinityX8


class NFIX8AddCapPaper(NostalgiaForInfinityX8):
    def version(self) -> str:
        return f"{super().version()}-addcap1"

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
        proposed = super().adjust_trade_position(
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
        amount = proposed[0] if isinstance(proposed, tuple) else proposed
        if amount is None:
            return proposed
        stake = Decimal(str(amount))
        if not stake.is_finite():
            return None
        if stake <= 0:
            return proposed

        tags = set((trade.enter_tag or "").split())
        if not trade.is_short and tags and tags.issubset(self.long_top_coins_mode_tags):
            return proposed
        if trade.leverage != 1:
            return None

        remaining = self.remaining_add_stake(trade)
        if stake > remaining or (min_stake is not None and stake < Decimal(str(min_stake))):
            return None
        return proposed

    @staticmethod
    def remaining_add_stake(trade):
        entries = [order for order in trade.orders if order.ft_order_side == trade.entry_side]
        if any(order.ft_is_open for order in entries):
            return Decimal(0)
        fills = []
        for order in entries:
            quantity = Decimal(str(order.safe_filled))
            if not quantity.is_finite() or quantity < 0:
                return Decimal(0)
            if quantity:
                price = Decimal(str(order.safe_price or 0))
                if not price.is_finite() or price <= 0:
                    return Decimal(0)
                fills.append(quantity * price)

        # Native orders are stored in insertion order. Partially filled canceled
        # orders count, and a later reduction does not replenish the budget.
        if not fills or len(fills) >= 3:
            return Decimal(0)
        return max(Decimal(0), fills[0] - sum(fills[1:]))
