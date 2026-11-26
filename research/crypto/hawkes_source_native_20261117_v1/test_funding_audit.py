"""Hand-calculated funding boundaries; no candidate or native imports."""

import numpy as np
import pytest

from research.crypto.hawkes_source_native_20261117_v1.audit_accounts import (
    funding_path,
    require_single_ownership,
)


def trade(opened, closed, *, short=False, quantity=2):
    return {
        "open_timestamp": opened,
        "close_timestamp": closed,
        "amount": quantity,
        "is_short": short,
        "orders": [
            {"ft_is_entry": True, "amount": quantity, "order_filled_timestamp": opened},
            {"ft_is_entry": False, "amount": quantity, "order_filled_timestamp": closed},
        ],
    }


@pytest.mark.parametrize(
    "policy,fees,accrued,owners",
    [
        ("before_orders", [0, 0, -0.6, 0], [0, 0, 0, -0.6], [False, False, True, False]),
        ("after_orders", [0, -0.4, 0, 0], [0, -0.4, -0.4, -0.4], [False, True, False, False]),
    ],
)
def test_open_and_close_boundaries(policy, fees, accrued, owners):
    # Two units open at event 8 and close at event 16. Positive rates charge longs.
    result = funding_path(
        trade(8, 16), [0, 8, 16, 24], [0.1, 0.2, 0.3, 0.4], [8, 9, 16, 17], policy
    )
    np.testing.assert_allclose(result["fees"], fees, rtol=0, atol=1e-15)
    np.testing.assert_allclose(result["accrued"], accrued, rtol=0, atol=1e-15)
    np.testing.assert_array_equal(result["ownership"], owners)
    assert [row["timestamp_ms"] for row in result["boundary"]] == [8, 16]
    assert result["boundary"][0]["entry_boundary"]
    assert result["boundary"][1]["exit_boundary"]


@pytest.mark.parametrize("policy", ["before_orders", "after_orders"])
@pytest.mark.parametrize("short,expected", [(False, -0.2), (True, 0.2)])
def test_ordinary_hold_and_signed_rate(policy, short, expected):
    # Net unit payment is +0.3-0.2=+0.1; quantity two gives +/-0.2.
    result = funding_path(
        trade(1, 17, short=short), [8, 16], [0.3, -0.2], [1, 8, 9, 16, 17, 18], policy
    )
    assert result["total_funding"] == pytest.approx(expected)
    np.testing.assert_allclose(
        result["accrued"],
        [0, 0, 0.6 if short else -0.6, 0.6 if short else -0.6, expected, expected],
        rtol=0,
        atol=1e-15,
    )
    assert result["boundary"] == []


@pytest.mark.parametrize("policy,expected", [("before_orders", -0.2), ("after_orders", 0.2)])
def test_same_timestamp_direction_change_has_one_owner(policy, expected):
    # At time 8 the old long closes and the new short opens at the same price.
    # With no other PnL or commission, this is also the total NAV difference
    # after the event; the pre-processing time-8 snapshot has zero funding.
    old = funding_path(trade(1, 8), [8], [0.1], [8, 9], policy)
    new = funding_path(trade(8, 17, short=True), [8], [0.1], [8, 9], policy)
    counts = old["ownership"].astype(int) + new["ownership"].astype(int)
    np.testing.assert_array_equal(counts, [1])
    require_single_ownership({"BTC/USDT:USDT": counts})
    np.testing.assert_allclose(1000 + old["accrued"] + new["accrued"], [1000, 1000 + expected])


@pytest.mark.parametrize("policy", ["before_orders", "after_orders"])
def test_same_timestamp_flat_roundtrip_owns_no_event(policy):
    result = funding_path(trade(8, 8), [8], [0.1], [8, 9], policy)
    assert result["total_funding"] == 0
    np.testing.assert_array_equal(result["ownership"], [False])
    assert result["boundary"][0]["entry_boundary"] and result["boundary"][0]["exit_boundary"]


def test_rejects_duplicate_lifecycle_ownership():
    with pytest.raises(AssertionError, match="multiple lifecycles"):
        require_single_ownership({"ETH/USDT:USDT": np.array([0, 2, 1])})


def test_rejects_partial_exit_or_dca():
    candidate = trade(1, 17)
    candidate["orders"].insert(1, {"ft_is_entry": False, "amount": 1, "order_filled_timestamp": 9})
    with pytest.raises(AssertionError, match="DCA/partial exits"):
        funding_path(candidate, [8, 16], [0.1, 0.1], [1, 8, 16, 17], "before_orders")


@pytest.mark.parametrize("policy", ["inclusive", "unknown"])
def test_rejects_unregistered_policy(policy):
    with pytest.raises(AssertionError):
        funding_path(trade(1, 17), [8, 16], [0.1, 0.1], [1, 8, 16, 17], policy)


def test_rejects_duplicate_events():
    with pytest.raises(AssertionError):
        funding_path(trade(1, 17), [8, 8], [0.1, 0.1], [1, 8, 16, 17], "before_orders")


def test_rejects_mismatched_fill_clock():
    candidate = trade(1, 17)
    candidate["open_timestamp"] = 0
    with pytest.raises(AssertionError):
        funding_path(candidate, [8, 16], [0.1, 0.1], [1, 8, 16, 17], "before_orders")
