"""Exercise complete cash, millisecond inventory boundaries and causal risk guards."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from freqtrade.research.data import read
from research.crypto.hedged_hold import account, funding_path, load_factor, monthly_observations


@pytest.fixture
def inputs():
    plan = read(Path("research/crypto/hedged_hold_v002_plan.json"))
    dates = pd.date_range("2024-01-01", "2024-01-02", freq="h", inclusive="left", tz="UTC")
    panels = {
        symbol: pd.DataFrame(
            {
                "date": dates,
                "spot_open": 100.0,
                "spot_close": 100.0,
                "spot_quote_volume": 1e9,
                "perpetual_open": 100.0,
                "perpetual_low": 100.0,
                "perpetual_volume": 1e9,
                "mark_open": 100.0,
            }
        )
        for symbol in plan["symbols"]
    }
    events = pd.to_datetime(
        [
            "2024-01-01T01:00:00Z",
            "2024-01-01T01:00:00.001Z",
            "2024-01-01T08:00:00Z",
            "2024-01-01T23:00:00Z",
            "2024-01-01T23:00:00.001Z",
        ],
        format="mixed",
    )
    funding = {
        symbol: pd.DataFrame({"calc_time": events.as_unit("ms").asi8, "funding_rate": 0.001})
        for symbol in plan["symbols"]
    }
    return plan, panels, funding, load_factor(plan)


def test_millisecond_funding_boundary_and_no_early_cash_credit(inputs):
    plan, panels, funding, _ = inputs
    symbol = plan["symbols"][0]
    clock = pd.DatetimeIndex(panels[symbol].date)
    held, cumulative = funding_path(funding[symbol], panels[symbol], clock, clock[1], clock[-1], 2)
    assert len(held) == 3
    assert cumulative[1] == 0
    assert cumulative[2] == pytest.approx(0.2)
    assert cumulative[-1] == pytest.approx(0.6)


def test_terminal_cash_matches_independent_decimal_fill_ledger(tmp_path, inputs):
    plan, panels, funding, factor = inputs
    out = tmp_path / "account"
    result = account(plan, panels, funding, factor, "2024-01-01", "2024-01-02", 1000, 10, out)
    cash = Decimal(1000)
    for trade in read(out / "trades.json"):
        q = Decimal(str(trade["q"]))
        cash -= q / Decimal("0.999") * Decimal("100.1")
        cash -= q * Decimal("99.9") * Decimal("0.0005")
        cash += q * Decimal("99.9") * Decimal("0.999")
        cash += q * (Decimal("99.9") - Decimal("100.1"))
        cash -= q * Decimal("100.1") * Decimal("0.0005")
        cash += q * Decimal(100) * Decimal("0.001") * 3
    assert result["net_profit_usdt"] == pytest.approx(float(cash - 1000), abs=1e-10)
    assert result["funding_events"] == 6
    assert result["full_months"] == 0
    assert result["checks"]["full_calendar_month_coverage"] is False
    equity = pd.read_parquet(out / "hourly_equity.parquet")
    assert equity.equity.iloc[-1] == pytest.approx(float(cash), abs=1e-10)


def test_quantity_uses_prior_close_and_future_changes_cannot_change_earlier_nav(tmp_path, inputs):
    plan, panels, funding, factor = inputs
    altered = deepcopy(panels)
    for panel in altered.values():
        panel.loc[
            panel.date.ge(pd.Timestamp("2024-01-01T20:00:00Z")),
            ["spot_open", "perpetual_open", "mark_open"],
        ] *= 1.1
    for name, frames in (("base", panels), ("altered", altered)):
        account(plan, frames, funding, factor, "2024-01-01", "2024-01-02", 1000, 3, tmp_path / name)
    original = read(tmp_path / "base/trades.json")
    changed = read(tmp_path / "altered/trades.json")
    assert [r["q"] for r in original] == [r["q"] for r in changed]
    first = pd.read_parquet(tmp_path / "base/hourly_equity.parquet")
    second = pd.read_parquet(tmp_path / "altered/hourly_equity.parquet")
    pd.testing.assert_frame_equal(
        first.loc[first.date.lt("2024-01-01T20:00:00Z")],
        second.loc[second.date.lt("2024-01-01T20:00:00Z")],
    )


def test_guard_uses_known_hour_then_exits_next_hour(tmp_path, inputs):
    plan, panels, funding, factor = inputs
    for panel in panels.values():
        panel.loc[
            panel.date.ge(pd.Timestamp("2024-01-01T12:00:00Z")), ["perpetual_open", "mark_open"]
        ] = 300
    result = account(
        plan, panels, funding, factor, "2024-01-01", "2024-01-02", 1000, 3, tmp_path / "guard"
    )
    assert result["margin_or_drawdown_trigger_time"] == "2024-01-01T12:00:00+00:00"
    assert {t["exit_time"] for t in read(tmp_path / "guard/trades.json")} == {
        "2024-01-01T13:00:00+00:00"
    }


def test_unaffordable_market_gap_leaves_whole_account_in_cash(tmp_path, inputs):
    plan, panels, funding, factor = inputs
    for panel in panels.values():
        panel.loc[1, "spot_open"] = 300
    result = account(
        plan, panels, funding, factor, "2024-01-01", "2024-01-02", 1000, 3, tmp_path / "cash"
    )
    assert result["entry_status"] == "CASH_CAPITAL_INFEASIBLE"
    assert result["complete_hedged_round_trips"] == result["funding_events"] == 0
    assert read(tmp_path / "cash/trades.json") == []


def test_monthly_observations_exclude_both_partial_edge_months():
    equity = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-31T23:00Z", "2024-02-29T23:00Z", "2024-03-14T23:00Z"]),
            "equity": [1000, 1010, 1015],
        }
    )
    returns = monthly_observations(
        equity, pd.Timestamp("2024-01-15", tz="UTC"), pd.Timestamp("2024-03-15", tz="UTC"), 1000
    )
    assert len(returns) == 1
    assert returns.iloc[0] == pytest.approx(0.01)
