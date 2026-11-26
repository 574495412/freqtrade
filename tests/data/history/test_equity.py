"""Equity admission failures must survive native cleaning, without crypto regressions."""

import pandas as pd
import pytest

from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
from freqtrade.data.history.equity import (
    EquityAdmissionError,
    EquitySessionContract,
    load_equity_pair_history,
    require_complete_equity_account_support,
)
from freqtrade.data.history.history_utils import load_pair_history
from freqtrade.enums import CandleType


def fixture():
    dates = ("2025-01-03", "2025-01-06", "2025-01-07")
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(dates, utc=True),
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "volume": [100.0, 100.0, 100.0],
        }
    )
    return EquitySessionContract("TEST/USD", "XNYS", dates), frame


def store(path, frame):
    FeatherDataHandler(path).ohlcv_store("TEST/USD", "1d", frame, CandleType.SPOT)


def test_native_no_fill_and_default_unchanged(tmp_path):
    contract, frame = fixture()
    store(tmp_path, frame)
    admitted = load_equity_pair_history(tmp_path, contract)
    pd.testing.assert_frame_equal(frame, admitted, check_dtype=False, check_exact=True)
    default = load_pair_history("TEST/USD", "1d", tmp_path)
    assert len(default) == 5  # Jan 4/5 fabricated by the unchanged default crypto loader.
    assert (default.volume == 0).sum() == 2


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "extra",
        "duplicate",
        "unordered",
        "nan",
        "zero_price",
        "range",
        "zero_volume",
        "negative_volume",
    ],
)
def test_reject_before_cleaning(tmp_path, kind):
    contract, frame = fixture()
    if kind == "missing":
        frame = frame.drop(1)
    elif kind == "extra":
        row = frame.iloc[[0]].copy()
        row["date"] = pd.to_datetime(["2025-01-04"], utc=True)
        frame = pd.concat([frame, row]).sort_values("date")
    elif kind == "duplicate":
        frame = pd.concat([frame, frame.iloc[[1]]]).sort_values("date")
    elif kind == "unordered":
        frame = frame.iloc[::-1]
    elif kind == "nan":
        frame.loc[1, "close"] = float("nan")
    elif kind == "zero_price":
        frame.loc[1, "open"] = 0
    elif kind == "range":
        frame.loc[1, "high"] = 1
    elif kind == "zero_volume":
        frame.loc[1, "volume"] = 0
    else:
        frame.loc[1, "volume"] = -1
    store(tmp_path, frame)
    with pytest.raises(EquityAdmissionError):
        load_equity_pair_history(tmp_path, contract)


@pytest.mark.parametrize(
    "calendar,sessions",
    [
        ("CRYPTO", ("2025-01-03",)),
        ("XNYS", ()),
        ("XNYS", ("2025-01-03", "2025-01-03")),
        ("XNYS", ("2025-01-06", "2025-01-03")),
        ("XNYS", ("2025-01-03T12:00:00Z",)),
    ],
)
def test_invalid_contract(calendar, sessions):
    with pytest.raises(EquityAdmissionError):
        EquitySessionContract("TEST/USD", calendar, sessions).index()


def test_history_is_not_complete_account():
    with pytest.raises(EquityAdmissionError, match="not integrated"):
        require_complete_equity_account_support()
