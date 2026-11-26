"""Hand-calculated audit fixtures, not financial candidate simulations."""

import numpy as np

from .audit_actual500 import HOUR, funding_history


def run():
    clocks = np.arange(5) * HOUR
    events = np.arange(1, 4) * HOUR

    def fill(hour, amount, entry):
        return {"order_filled_timestamp": int(hour * HOUR), "amount": amount, "ft_is_entry": entry}

    cases = [
        (
            [fill(0.5, 2, True), fill(3.5, 2, False)],
            [1.0, 2.0, 3.0],
            [0.0, 0.0, -2.0, -6.0, -12.0],
            -12.0,
        ),
        (
            [fill(0.5, 2, True), fill(2, 1, True), fill(3.5, 3, False)],
            [1.0, 1.0, 1.0],
            [0.0, 0.0, -2.0, -4.0, -10.0],
            -10.0,
        ),
        (
            [fill(0.5, 2, True), fill(2, 1, False), fill(3.5, 1, False)],
            [1.0, 1.0, 1.0],
            [0.0, 0.0, -2.0, -4.0, -6.0],
            -6.0,
        ),
    ]
    for orders, units, expected, final in cases:
        for short in (False, True):
            values, terminal, boundary = funding_history(
                {"orders": orders, "is_short": short}, events, np.array(units), clocks
            )
            sign = -1 if short else 1
            np.testing.assert_array_equal(values, np.array(expected) * sign)
            assert terminal == final * sign
            assert len(boundary) == len(orders) - 2
    print("6 hand-calculated funding cases passed")


if __name__ == "__main__":
    run()
