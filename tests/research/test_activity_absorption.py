import io
from decimal import Decimal
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest

from research.crypto.activity_absorption_features import (
    GROUPS,
    activity_features,
    dense_price_features,
    opportunities,
    read_activity_archive,
)
from research.crypto.interaction_features import hourly_features


def fixture():
    rng = np.random.default_rng(74)
    clock = pd.date_range("2024-01-01", periods=650, freq="5min", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.0004, len(clock))))
    frame = pd.DataFrame(
        {
            "open": close * 0.9999,
            "close": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "volume": 100 + rng.random(len(clock)),
        },
        index=clock,
    )
    frame["quote_volume"], frame["trade_count"] = 10000.0, 100.0
    frame["taker_buy_quote"] = 5000.0
    for index, side in [(400, 1), (401, 1), (420, -1)]:
        at, value = clock[index], close[index]
        frame.loc[at, "open"] = value * (1 - side * 0.002)
        frame.loc[at, "high"] = max(value, frame.loc[at, "open"]) + 0.01
        frame.loc[at, "low"] = min(value, frame.loc[at, "open"]) - 0.01
        frame.loc[at, ["quote_volume", "trade_count", "taker_buy_quote"]] = [
            40000,
            200,
            4000 if side == 1 else 36000,
        ]
    events = pd.DataFrame(
        {
            "date": pd.date_range("2023-12-30", "2024-01-06", freq="8h", tz="UTC"),
            "funding_rate": 0.0001,
        }
    )
    return frame, events


def build(frame, events):
    dense = dense_price_features(frame)
    return opportunities(
        frame,
        dense,
        dense.r12,
        events,
        pd.Timestamp("2024-01-01", tz="UTC"),
        pd.Timestamp("2024-01-08", tz="UTC"),
    )


def test_real_events_and_dense_baseline_parity():
    frame, events = fixture()
    pd.testing.assert_frame_equal(
        dense_price_features(frame).loc[lambda f: f.index.minute == 0], hourly_features(frame)
    )
    result = build(frame, events)
    assert result.feature_open.tolist() == [frame.index[400], frame.index[420]]
    assert result.side.tolist() == [1, -1]
    assert result.feature_open.dt.minute.ne(0).any()
    assert len(GROUPS["Price"]) == 25 and len(GROUPS["Flow"]) == 29
    assert len(GROUPS["FlowActivity"]) == 33


def test_future_price_flow_count_and_funding_cannot_change_past():
    frame, events = fixture()
    cut = frame.index[430]
    expected = build(frame, events).loc[lambda f: f.feature_open < cut].reset_index(drop=True)
    changed = frame.copy()
    changed.loc[cut:, ["open", "high", "low", "close"]] *= 1.3
    changed.loc[cut:, "taker_buy_quote"] = 1
    changed.loc[cut:, "trade_count"] = 99999
    new_events = events.copy()
    new_events.loc[new_events.date >= cut, "funding_rate"] = 2
    observed = build(changed, new_events).loc[lambda f: f.feature_open < cut].reset_index(drop=True)
    pd.testing.assert_frame_equal(observed, expected)
    pd.testing.assert_frame_equal(
        build(frame.loc[: cut - pd.Timedelta(minutes=5)], events), expected
    )


def test_zero_activity_is_unknown_and_decimal_activity_arithmetic():
    frame, _ = fixture()
    result = activity_features(frame)
    at = frame.index[400]
    assert result.loc[at, "flow5"] == float(2 * Decimal(4000) / Decimal(40000) - 1)
    assert result.loc[at, "count_ratio"] == pytest.approx(float(Decimal(2).ln()))
    assert result.loc[at, "trade_size_ratio"] == pytest.approx(float(Decimal(2).ln()))
    frame.loc[at, ["quote_volume", "trade_count", "taker_buy_quote"]] = 0
    row = activity_features(frame).loc[at]
    assert pd.isna(row.flow5) and pd.isna(row.count_ratio) and pd.isna(row.trade_size_ratio)


@pytest.mark.parametrize("count,taker", [(2.5, 100), (2, 300), (-1, 100), (0, 100)])
def test_invalid_raw_counts_and_taker_values_rejected(tmp_path, count, taker):
    data = f"1704067200000,100,101,99,100,2,1704067499999,200,{count},1,{taker},0\n"
    path = tmp_path / "source.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("data.csv", data)
    with pytest.raises(ValueError):
        read_activity_archive(path)


def test_official_header_and_field_positions(tmp_path):
    path = tmp_path / "source.zip"
    buffer = io.StringIO()
    buffer.write("open_time,open,high,low,close,volume,close_time,quote_volume,count,")
    buffer.write("taker_buy_volume,taker_buy_quote_volume,ignore\n")
    buffer.write("1704067200000,100,101,99,100,2,1704067499999,200,2,1,100,0\n")
    with ZipFile(path, "w") as archive:
        archive.writestr("data.csv", buffer.getvalue())
    row = read_activity_archive(path).iloc[0]
    assert row.trade_count == 2 and row.quote_volume == 200 and row.taker_buy_quote == 100
