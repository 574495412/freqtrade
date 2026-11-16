"""Explicit native free-balance funding and the preserved historical fixed-cap mode."""

from copy import deepcopy

from deploy.mainnet_100_20261109.funding import CAPITAL, STAKE_BUDGET, validate_funding
from freqtrade.persistence import Trade
from freqtrade.wallets import Wallets


def wallet_class(config):
    """Validate explicit funding mode, retaining the historical fixed-budget path."""
    mode = validate_funding(config)
    if mode == "available_balance":
        return Wallets
    return CappedWallets


def use_available_balance(config):
    """Derive the user-authorized native mode without changing strategy allocation."""
    wallet_class(config)
    result = deepcopy(config)
    result.pop("available_capital", None)
    result.pop("capital_ceiling_usdt", None)
    result["live_capital_mode"] = "available_balance"
    wallet_class(result)
    return result


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
