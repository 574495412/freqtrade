"""Cached admission must not reopen after a later detail-minute close."""

from types import SimpleNamespace

import pandas as pd
import pytest

from freqtrade.persistence import Trade
from research.crypto.strategies.fine_lease import FineLeaseBase
from research.crypto.strategies.fine_lease_v2 import (
    FineLeaseOneRV2,
    FineLeaseQuarterV2,
    FineLeaseRetainV2,
)


@pytest.mark.parametrize("strategy_type", [FineLeaseQuarterV2, FineLeaseOneRV2, FineLeaseRetainV2])
@pytest.mark.parametrize(
    "profit,count,elapsed,is_open,expected",
    [
        (0.24, 1, 1, False, False),
        (-0.93, 1, 20, False, False),
        (0.0, 1, 20, False, False),
        (0.24, 4, 20, False, False),
        (0.24, 1, 6, False, False),
        (0.24, 1, 10, False, True),
        (0.24, 1, 20, True, False),
    ],
)
def test_current_history_overrides_cached_shortlist(
    monkeypatch, strategy_type, profit, count, elapsed, is_open, expected
):
    # A prior5m shortlist still says yes; only actual state may veto it.
    monkeypatch.setattr(FineLeaseBase, "confirm_trade_entry", lambda *args, **kwargs: True)
    clock = pd.Timestamp("2024-08-05T05:05Z")
    trade = SimpleNamespace(
        enter_tag="small_first_hit",
        get_custom_data=lambda key: "DOGEUSDT:97280",
        is_open=is_open,
        close_profit_abs=profit,
        close_date_utc=clock,
    )
    monkeypatch.setattr(Trade, "get_trades_proxy", lambda *args, **kwargs: [trade] * count)
    strategy = strategy_type({})
    strategy.closed_row = lambda *args: SimpleNamespace(lease_id="DOGEUSDT:97280")
    observed = strategy.confirm_trade_entry(
        "DOGE/USDT:USDT",
        "limit",
        1,
        100,
        "GTC",
        clock + pd.Timedelta(minutes=elapsed),
        "small_first_hit",
        "long",
    )
    assert observed is expected
