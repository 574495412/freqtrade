"""Local causal equal-base quantity intent; native wallets own all money and fills."""

from decimal import ROUND_FLOOR, Decimal
from math import lcm


def positive(value):
    if isinstance(value, bool):
        raise ValueError("Explicit positive decimal input required")
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("Explicit positive decimal input required")
    return result


def common_quantity(leg_capital, spot_price, perp_price, spot_step, perp_step, spot_fee, perp_fee):
    """Previous completed quotes, common lot grid, fixed 95% budget; never round up."""
    capital, spot, perp, a, b = map(
        positive, (leg_capital, spot_price, perp_price, spot_step, perp_step)
    )
    fees = [Decimal(str(x)) for x in (spot_fee, perp_fee)]
    if any(not x.is_finite() or x < 0 or x >= 1 for x in fees):
        raise ValueError("Explicit nonnegative fees below one required")
    places = max(-a.as_tuple().exponent, -b.as_tuple().exponent, 0)
    scale = Decimal(10) ** places
    step = Decimal(lcm(int(a * scale), int(b * scale))) / scale
    raw = capital * Decimal(".95") / max(spot * (1 + fees[0]), perp * (1 + fees[1]))
    quantity = (raw / step).to_integral_value(rounding=ROUND_FLOOR) * step
    if quantity <= 0:
        raise ValueError("Native common lot cannot fit either original wallet")
    return quantity, step
