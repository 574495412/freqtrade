"""Scope Binance market discovery to the authorized spot market.

Pure configuration extension. This module never starts a worker or sends orders.
"""

import copy


def scoped_config(config):
    result = copy.deepcopy(config)
    if (
        result.get("trading_mode") != "spot"
        or result.get("capital_ceiling_usdt") != 100
        or result.get("available_capital") != 99
    ):
        raise ValueError("Expected existing100USDT spot contract")
    exchange = result["exchange"]
    for name in ("ccxt_config", "ccxt_async_config"):
        options = exchange.setdefault(name, {}).setdefault("options", {})
        options["fetchMarkets"] = {"types": ["spot"], "loadAllOptions": False}
        options["fetchMargins"] = False
    return result
