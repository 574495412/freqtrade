from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location("matched_runner", Path(__file__).with_name("run_matched_comparison.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
analysis_spec = importlib.util.spec_from_file_location("matched_analysis", Path(__file__).with_name("analyze_matched_comparison.py"))
analysis = importlib.util.module_from_spec(analysis_spec)
analysis_spec.loader.exec_module(analysis)


@pytest.mark.parametrize("short,profit", [(False, 17.59), (True, -22.41)])
def test_decimal_cash_reconstruction_includes_dca_partial_exits_fees_and_funding(short, profit):
    trade = {"pair": "BTC/USDT:USDT", "is_short": short, "fee_open": .001,
             "fee_close": .001, "funding_fees": -2, "profit_abs": profit,
             "open_timestamp": 0, "close_timestamp": 10,
             "orders": [{"amount": 1, "safe_price": 100, "ft_is_entry": True},
                        {"amount": 1, "safe_price": 95, "ft_is_entry": True},
                        {"amount": 1, "safe_price": 105, "ft_is_entry": False},
                        {"amount": 1, "safe_price": 110, "ft_is_entry": False}]}
    assert runner.audit_trades({"trades": [trade], "profit_total_abs": profit})["passed"]
    bad = deepcopy(trade)
    bad["orders"][-1]["amount"] = .9
    with pytest.raises(ValueError, match="fill/cash"):
        runner.audit_trades({"trades": [bad], "profit_total_abs": profit})


def test_period_uses_marked_nav_and_preserves_open_boundary_inventory():
    clocks = pd.date_range("2025-01-01", periods=4, freq="h", tz="UTC")
    frame = pd.DataFrame({"date": clocks, "hourly_mark_equity": [1000, 1100, 900, 1000],
                          "open_trades": [0, 1, 1, 1]})
    stats = {"trades": [
        {"close_timestamp": clocks[1].timestamp()*1000, "exit_reason": "exit", "profit_abs": 20},
        {"close_timestamp": clocks[3].timestamp()*1000, "exit_reason": "exit", "profit_abs": 50},
        {"close_timestamp": clocks[2].timestamp()*1000, "exit_reason": "force_exit", "profit_abs": 500}]}
    period = {"name": "fixture", "start": str(clocks[0]), "end": str(clocks[-1])}
    result = runner.period_metrics(frame, stats, period)
    assert result["net_return"] == 0
    assert result["hourly_mark_drawdown"] == pytest.approx(1 - 900/1100)
    assert result["completed_campaigns"] == 1
    assert result["campaign_gross_profit"] == 20
    assert result["open_campaigns_at_end"] == 1
    with pytest.raises(ValueError, match="Incomplete"):
        runner.period_metrics(frame.drop(index=1), stats, period)


def test_period_cannot_silently_change_common_endpoint():
    frame = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC"),
                          "hourly_mark_equity": [1000, 1100], "open_trades": [0, 0]})
    with pytest.raises(ValueError, match="boundary"):
        runner.period_metrics(frame, {"trades": []}, {"name": "bad", "start": "2025-01-01T00:00Z", "end": "2025-01-01T02:00Z"})


def test_pareto_cannot_hide_a_worse_cost_or_calendar_scenario():
    assert analysis.dominates([(.1, .1), (.2, .2)], [(.09, .11), (.19, .21)])
    assert not analysis.dominates([(.1, .1), (.2, .2)], [(.09, .11), (.19, .19)])
    assert not analysis.dominates([(.1, .1)], [(.1, .1)])


def test_partial_results_do_not_publish_a_winner(tmp_path):
    root = tmp_path / "matrix"
    root.mkdir()
    (root / "registered_plan.json").write_text(json.dumps({"candidates": [{"name": "a"}],
                                                           "total_cost_bps_per_side": [5, 8, 15]}))
    with pytest.raises(ValueError, match="Incomplete matrix"):
        analysis.analyze(root, tmp_path / "premature-report")
    assert not (tmp_path / "premature-report").exists()
