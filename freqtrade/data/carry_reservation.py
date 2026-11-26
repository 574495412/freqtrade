"""Native stake reservation adapter preserving an immutable base-quantity intent.

This never lowers the native minimum or market minimum. A padded native stake
request is permitted only when native contract rounding still yields exactly
the precommitted quantity and both the padded reservation and fee fit cash.
"""

import math

from ccxt import TICK_SIZE

from freqtrade.exchange.exchange_utils import amount_to_contract_precision


def reserve_for_quantity(quantity, rate, minimum_stake, maximum_stake, market, fee):
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
        for x in (quantity, rate, minimum_stake, maximum_stake, fee)
    ):
        raise ValueError("Finite native leg quantity, clocks and stake limits required")
    if quantity <= 0 or rate <= 0 or minimum_stake <= 0 or maximum_stake <= 0 or not 0 <= fee < 1:
        raise ValueError("Positive native quantity/rate/limits required")
    size = market.get("contractSize") or 1.0
    if size != 1:
        raise ValueError("Only native linear unit-contract carry is supported")
    notional = quantity * rate
    raw_amount = market["limits"]["amount"]["min"]
    raw_cost = market["limits"]["cost"]["min"]
    if raw_amount is None or raw_cost is None or quantity < raw_amount or notional < raw_cost:
        raise ValueError("Frozen quantity fails actual market minimum")
    stake = max(notional, minimum_stake)
    actual = amount_to_contract_precision(
        stake / rate, market["precision"]["amount"], TICK_SIZE, size
    )
    if abs(actual - quantity) > 1e-12:
        raise ValueError("Native reserve would change immutable base quantity")
    if stake * (1 + fee) > maximum_stake:
        raise ValueError("Native reserve and entry cost exceed available wallet")
    return stake
