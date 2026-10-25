"""Proposal observations preserve the causal precision and read the actual open account."""

from types import SimpleNamespace

import pandas as pd

from freqtrade.exchange.exchange import TICK_SIZE
from freqtrade.persistence import Trade
from research.crypto.run_first_hit_account import RecordedPrecisionBacktesting


def test_recorded_precision_retains_causal_value_and_current_capacity(monkeypatch):
    engine = object.__new__(RecordedPrecisionBacktesting)
    clock = pd.Timestamp("2024-08-05T04:10:00Z")
    engine.closed_precision = {
        "ETH/USDT:USDT": pd.Series([0.01, 0.001], index=[clock, clock + pd.Timedelta(minutes=5)])
    }
    engine.precision_calls = []
    opened = [
        SimpleNamespace(enter_tag="small_first_hit"),
        SimpleNamespace(enter_tag="core_first_hit"),
        SimpleNamespace(enter_tag="core_first_hit"),
    ]
    monkeypatch.setattr(Trade, "get_trades_proxy", lambda **kwargs: opened)
    assert engine.get_pair_precision("ETH/USDT:USDT", clock) == (0.01, TICK_SIZE)
    record = engine.precision_calls[0]
    assert (record["open_small"], record["open_core"], record["open_total"]) == (1, 2, 3)
    assert len(opened) == 3
