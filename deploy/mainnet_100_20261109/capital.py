"""Local native-Wallets extension: profits/deposits cannot expand committed stake."""

from freqtrade.persistence import Trade
from freqtrade.wallets import Wallets


CAPITAL = 100.0
STAKE_BUDGET = 99.0  # Leave one USDT outside trading stake for costs.


def pending_reservations(trades):
    """Native trade.stake_amount omits pending DCA but includes initial orders."""
    total = 0.0
    for trade in trades:
        entries = sum(
            o.stake_amount for o in trade.open_orders if o.ft_order_side == trade.entry_side
        )
        total += (
            entries if trade.nr_of_successful_entries else max(0.0, entries - trade.stake_amount)
        )
    return total


class CappedWallets(Wallets):
    def __init__(self, config, exchange, *args, **kwargs):
        if (
            config.get("available_capital") != STAKE_BUDGET
            or config.get("capital_ceiling_usdt") != CAPITAL
        ):
            raise ValueError("The manual release requires the fixed100USDT contract")
        super().__init__(config, exchange, *args, **kwargs)

    def get_total_stake_amount(self):
        return max(0.0, min(STAKE_BUDGET, super().get_total_stake_amount()))

    def get_available_stake_amount(self):
        reserved = pending_reservations(Trade.get_trades_proxy(is_open=True))
        remaining = self.get_total_stake_amount() - Trade.total_open_trades_stakes() - reserved
        return max(0.0, min(remaining, super().get_available_stake_amount()))
