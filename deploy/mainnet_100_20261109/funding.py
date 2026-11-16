"""Funding validation shared by lightweight admission and native wallet selection."""

CAPITAL = 100.0
STAKE_BUDGET = 99.0


def validate_funding(config):
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
    elif (
        mode != "fixed_100"
        or config.get("available_capital") != STAKE_BUDGET
        or config.get("capital_ceiling_usdt") != CAPITAL
    ):
        raise ValueError("Unknown live funding contract")
    return mode
