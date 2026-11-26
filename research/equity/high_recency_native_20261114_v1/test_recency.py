import numpy as np
import pandas as pd
import pytest

from bootstrap import boot

boot()
from freqtrade.data.history.equity import EquityAdmissionError
from RecencySignal import RecencySignal, recency


def test_calendar_recency_latest_tie_and_expiry():
    dates = pd.date_range("2024-01-01", "2025-02-01", tz="UTC", freq="B")
    frame = pd.DataFrame(dict(date=dates, close=10.0))
    frame.loc[0, "close"] = 20
    frame.loc[5, "close"] = 15
    frame.loc[10, "close"] = 15
    result = recency(frame)
    assert result.loc[frame.date.lt(dates[0] + pd.Timedelta(days=364))].isna().all()
    eligible = frame.index[frame.date.ge(dates[0] + pd.Timedelta(days=364))]
    for i in eligible:
        day = frame.date.iloc[i]
        window = frame.loc[(frame.date > day - pd.Timedelta(days=364)) & (frame.date <= day)]
        high = max(window.close)
        last = max(window.loc[window.close.eq(high), "date"])
        assert result.iloc[i] == 1 - (day - last).days / 364
    # Repeated exact highs select the most recent observation, not first argmax.
    assert result.iloc[-1] == 1


def test_future_changes_do_not_change_prefix():
    dates = pd.date_range("2024-01-01", "2026-08-31", tz="UTC", freq="B")
    frame = pd.DataFrame(dict(date=dates, close=100 + np.sin(np.arange(len(dates)))))
    original = recency(frame)
    for cutoff in [300, 450, 600]:
        changed = frame.copy()
        changed.loc[cutoff:, "close"] *= 1e5
        np.testing.assert_array_equal(recency(changed).iloc[:cutoff], original.iloc[:cutoff])
        np.testing.assert_array_equal(recency(frame.iloc[:cutoff]), original.iloc[:cutoff])


def test_financial_lifecycle_rejected():
    strategy = RecencySignal(dict(equity_diagnostic_only=True))
    strategy.bot_start()
    with pytest.raises(EquityAdmissionError, match="funded loop"):
        strategy.bot_loop_start()
    with pytest.raises(EquityAdmissionError, match="unfinished"):
        RecencySignal({}).bot_start()
    assert not strategy.confirm_trade_entry()
