"""Shared execution rules for the 14-slot NFI candidate."""

import logging
from math import fsum, isfinite


logger = logging.getLogger(__name__)


class FourteenSlotShortRebuyCap:
    def custom_stake_amount(
        self, pair, current_time, current_rate, proposed_stake, min_stake,
        max_stake, leverage, entry_tag, side, **kwargs,
    ):
        proposal = self.wallets.get_trade_stake_amount(pair, 12, update=False)
        return super().custom_stake_amount(
            pair, current_time, current_rate, proposal, min_stake,
            max_stake, leverage, entry_tag, side, **kwargs,
        )

    def adjust_trade_position(
        self, trade, current_time, current_rate, current_profit, min_stake, max_stake, **kwargs,
    ):
        proposed = super().adjust_trade_position(
            trade, current_time, current_rate, current_profit, min_stake, max_stake, **kwargs,
        )
        amount = proposed[0] if isinstance(proposed, tuple) else proposed
        tags = set((trade.enter_tag or "").split())
        if not trade.is_short or not tags or not tags.issubset(self.short_rebuy_mode_tags):
            return proposed
        if amount is None or amount <= 0:
            return proposed
        # Native closed fills exclude partially filled orders that remain open.
        if any(order.ft_order_side == trade.entry_side for order in trade.open_orders):
            return None
        entries = trade.select_filled_orders(trade.entry_side)
        if not entries:
            return None
        stakes = [order.stake_amount_filled for order in entries]
        # Wallet validation can raise a positive requested stake to its minimum.
        if all(isfinite(stake) and stake > 0 for stake in (*stakes, amount)):
            total = fsum(stakes)
            if total + max(amount, min_stake or 0.0) <= 2 * stakes[0] + 1e-9:
                return proposed
            logger.info("Short rebuy budget rejected %s, used %.8f, new %.8f, limit %.8f",
                        trade.pair, total, amount, 2 * stakes[0])
        return None
