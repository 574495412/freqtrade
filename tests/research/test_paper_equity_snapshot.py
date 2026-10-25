"""Partial reductions must not double count funding; missing prices cannot become zero PnL."""

import pytest

from research.crypto.capture_paper_equity import check_marks, marked_campaign


@pytest.mark.parametrize("change", ["missing", "stale", "future", "negative", "skew"])
def test_invalid_public_marks_rejected(change):
    now = 1_000_000
    marks = [
        {"symbol": "ETHUSDT", "markPrice": "100", "time": now},
        {"symbol": "SOLUSDT", "markPrice": "100", "time": now},
    ]
    if change == "missing":
        marks.pop()
    elif change == "stale":
        marks[0]["time"] -= 121_000
    elif change == "future":
        marks[0]["time"] += 6000
    elif change == "negative":
        marks[0]["markPrice"] = "-1"
    else:
        marks[0]["time"] -= 6000
    with pytest.raises(ValueError):
        check_marks(marks, ["ETHUSDT", "SOLUSDT"], now)


@pytest.mark.parametrize("mode", ["futures", "FUTURES"])
def test_partial_realized_funding_is_not_counted_twice(mode):
    row = {
        "id": 1,
        "pair": "ETH/USDT:USDT",
        "is_short": False,
        "amount": 1.0,
        "open_rate": 100.0,
        "fee_open": 0.001,
        "fee_close": 0.001,
        "stake_amount": 100.0,
        "max_stake_amount": 200.0,
        "funding_fees": -3.0,
        "realized_profit": 8.0,
        "leverage": 1.0,
        "trading_mode": mode,
    }
    orders = [
        {"ft_is_open": False, "filled": 2, "ft_order_side": "buy", "funding_fee": 0},
        {"ft_is_open": False, "filled": 1, "ft_order_side": "sell", "funding_fee": -2},
    ]
    # Remaining price gain10 - open/close fees.21 - remaining funding1 + prior netcash8.
    assert marked_campaign(row, orders, 110) == pytest.approx(16.79)
