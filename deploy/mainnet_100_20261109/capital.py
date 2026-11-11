"""Explicit native free-balance funding and the preserved historical fixed-cap mode."""

from copy import deepcopy

from freqtrade.persistence import Trade
from freqtrade.wallets import Wallets


CAPITAL = 100.0
STAKE_BUDGET = 99.0  # Leave one USDT outside trading stake for costs.


def wallet_class(config):
    """Validate explicit funding mode, retaining the historical fixed-budget path."""
    mode = config.get("live_capital_mode", "fixed_100")
    if mode == "available_balance":
        if (
            "available_capital" in config
            or "capital_ceiling_usdt" in config
            or config.get("stake_currency") != "USDT"
            or config.get("stake_amount") != "unlimited"
            or config.get("tradable_balance_ratio") != 0.99
        ):
            raise ValueError("Available-balance mode requires the native free-wallet contract")
        return Wallets
    if (
        mode != "fixed_100"
        or config.get("available_capital") != STAKE_BUDGET
        or config.get("capital_ceiling_usdt") != CAPITAL
    ):
        raise ValueError("Unknown live funding contract")
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
