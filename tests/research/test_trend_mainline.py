"""Economic invariants for causal liquidity, partial fills and continuous inventory."""

import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.crypto.trend_mainline.__main__ import gate
from research.crypto.trend_mainline.account import metrics, replay
from research.crypto.trend_mainline.data import features, validate


@pytest.fixture
def plan():
    p = json.loads(Path("research/crypto/trend_mainline_v001.json").read_text())
    p["symbols"] = ["BTCUSDT"]
    return p


def sample(plan):
    index = pd.date_range("2024-01-01", "2024-01-02", freq="5min", inclusive="left", tz="UTC")
    bars = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
            "quote_volume": 10000.0,
        },
        index=index,
    )
    h = pd.date_range("2024-01-01", "2024-01-02", freq="h", inclusive="left", tz="UTC")
    f = pd.DataFrame(
        {
            "atr": 1.0,
            "previous_close": 100.0,
            "before_previous_close": 100.0,
            "prior_hour_quote": 10000000.0,
            "eligible": True,
            "momentum": 0.1,
            "high55": 110.0,
            "low55": 90.0,
            "high20": 110.0,
            "low20": 90.0,
        },
        index=h,
    )
    funding = pd.DataFrame(
        {"funding_rate": [0.001], "mark": [100.0]}, index=pd.DatetimeIndex(["2024-01-01T08:00:00Z"])
    )
    return {"BTCUSDT": {"bars": bars, "features": f, "funding": funding}}


def test_open_inventory_is_not_a_closed_win_and_nav_pays_exit_reserve(plan):
    a = replay(sample(plan), plan, "Momentum63", 10000, 3, "2024-01-01T01:00", "2024-01-01T02:00")
    m = metrics(a, 10000)
    assert m["closed_trades"] == 0 and m["win_rate"] is None
    assert m["open_positions"] == 1 and m["net_return"] < 0
    assert a["state"]["positions"]["BTCUSDT"]["quantity"] > 0


def test_future_turnover_changes_fill_not_submitted_order(plan):
    data = sample(plan)
    other = deepcopy(data)
    other["BTCUSDT"]["bars"].loc["2024-01-01T01:00:00Z", "quote_volume"] = 100.0
    a = replay(data, plan, "Momentum63", 10000, 3, "2024-01-01T01:00", "2024-01-01T02:00")
    b = replay(other, plan, "Momentum63", 10000, 3, "2024-01-01T01:00", "2024-01-01T02:00")
    assert a["decisions"] == b["decisions"]
    assert a["events"][0]["quantity"] > b["events"][0]["quantity"]
    for e in a["events"] + b["events"]:
        assert e["quantity"] * e["price"] <= e["interval_quote_volume"] * 0.01 + 1e-8
        assert pd.Timestamp(e["time"]) > pd.Timestamp(e["submitted_at"])


def test_zero_volume_exit_persists_and_restart_is_exact(plan):
    data = sample(plan)
    bars = data["BTCUSDT"]["bars"]
    bars.loc["2024-01-01T07:55:00Z", "low"] = 90.0
    bars.loc["2024-01-01T08:00:00Z":"2024-01-01T08:10:00Z", "quote_volume"] = 0.0
    whole = replay(data, plan, "Momentum63", 10000, 3, "2024-01-01T01:00", "2024-01-01T10:00")
    a = replay(data, plan, "Momentum63", 10000, 3, "2024-01-01T01:00", "2024-01-01T08:10")
    assert a["state"]["pending_exits"] and a["state"]["positions"]
    restored = json.loads(json.dumps(a["state"]))
    b = replay(
        data, plan, "Momentum63", 10000, 3, "2024-01-01T08:10", "2024-01-01T10:00", state=restored
    )
    assert b["state"] == whole["state"]
    assert a["events"] + b["events"] == whole["events"]
    assert a["trades"] + b["trades"] == whole["trades"]
    assert any(e["type"] == "funding" and e["cash_delta"] < 0 for e in whole["events"])
    assert whole["state"]["max_exit_delay_minutes"] >= 20


def test_feature_prefix_cannot_observe_future_days(plan):
    index = pd.date_range("2023-08-01", "2023-12-01", inclusive="left", freq="5min", tz="UTC")
    x = np.arange(len(index)) / len(index)
    bars = pd.DataFrame(
        {
            "open": 100 + x,
            "high": 101 + x,
            "low": 99 + x,
            "close": 100 + x,
            "volume": 100.0,
            "quote_volume": 100000.0,
        },
        index=index,
    )
    full = features({"BTCUSDT": bars}, plan)["BTCUSDT"]
    cut = pd.Timestamp("2023-11-15T00:00Z")
    prefix = features({"BTCUSDT": bars.loc[bars.index < cut]}, plan)["BTCUSDT"]
    pd.testing.assert_frame_equal(full.loc[prefix.index], prefix)
    # The midnight feature uses only the preceding completed day's data.
    changed = bars.copy()
    changed.loc[changed.index >= cut, "quote_volume"] *= 10000
    changed.loc[changed.index >= cut, ["open", "high", "low", "close"]] *= 2
    after = features({"BTCUSDT": changed}, plan)["BTCUSDT"]
    pd.testing.assert_frame_equal(full.loc[full.index <= cut], after.loc[after.index <= cut])


def test_data_gaps_and_negative_turnover_fail(plan):
    bars = sample(plan)["BTCUSDT"]["bars"]
    with pytest.raises(ValueError, match="Missing"):
        validate(bars.iloc[:-1], "2024-01-01", "2024-01-02", "5min")
    bad = bars.copy()
    bad.iloc[0, bad.columns.get_loc("quote_volume")] = -1
    with pytest.raises(ValueError, match="Negative"):
        validate(bad, "2024-01-01", "2024-01-02", "5min")


def test_no_trade_or_undefined_sharpe_cannot_pass(plan):
    result = {
        "net_return": 0.1,
        "closed_trades": 0,
        "profit_factor": None,
        "daily_sharpe": None,
        "max_drawdown": 0.0,
        "account_halted": False,
    }
    assert not gate(result, plan["screening_targets"])["passed"]
