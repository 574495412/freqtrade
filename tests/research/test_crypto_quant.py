"""核验时序泄漏、浮亏与资金费、完整成本筛查等关键经济边界。"""

from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from freqtrade.research.data import complete_hours, read
from freqtrade.research.engine import configuration, equity_observations
from freqtrade.research.strategies import CryptoBreakout, CryptoReversion, CryptoTrend
from freqtrade.research.workflow import screening, validate_plan


@pytest.fixture
def plan():
    return read(Path(__file__).resolve().parents[2] / "research/crypto/plan.json")


def test_missing_hours_and_duplicate_candles_cannot_be_filled_silently():
    frame = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=24, freq="h", tz="UTC")})
    assert len(complete_hours(frame, "2024-01-01", "2024-01-02")) == 24
    for broken in (frame.drop(index=5), pd.concat([frame, frame.iloc[[0]]])):
        with pytest.raises(ValueError, match="缺失、重复"):
            complete_hours(broken, "2024-01-01", "2024-01-02")


def test_native_inclusive_timerange_does_not_read_the_next_validation_bar(plan, tmp_path):
    from freqtrade.configuration import TimeRange

    config = configuration(plan, tmp_path, tmp_path, "2024-01-01", "2025-01-01", 3)
    boundary = TimeRange.parse_timerange(config["timerange"])
    assert boundary.stopts == int(pd.Timestamp("2025-01-01", tz="UTC").timestamp()) - 1
    assert boundary.stopts < int(pd.Timestamp(plan["validation_start"], tz="UTC").timestamp())


def test_native_configuration_setup_populates_wallet_defaults(plan, tmp_path):
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.research.data import save

    settings = configuration(plan, tmp_path, tmp_path, "2024-01-01", "2025-01-01", 3)
    save(tmp_path / "settings.json", settings)
    loaded = setup_optimize_configuration(
        {"config": [str(tmp_path / "settings.json")]}, RunMode.BACKTEST
    )
    assert loaded["amend_last_stake_amount"] is False
    assert loaded["dry_run"] is True


def test_external_virtual_environment_python_symlink_is_preserved(tmp_path, monkeypatch):
    import sys

    import freqtrade.research.__main__ as cli

    executable = tmp_path / "venv_python"
    executable.symlink_to(sys.executable)
    received = []
    monkeypatch.setattr(cli, "review", lambda *args: received.append(args) or {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "research",
            "review",
            "--dataset",
            str(tmp_path),
            "--run",
            str(tmp_path),
            "--tradingagents-root",
            str(tmp_path),
            "--tradingagents-python",
            str(executable),
        ],
    )
    cli.main()
    assert received[0][-1] == executable.absolute()
    assert received[0][-1] != executable.resolve()


@pytest.mark.parametrize("cls", [CryptoTrend, CryptoBreakout, CryptoReversion])
def test_strategy_signals_are_unchanged_when_future_prices_change(cls):
    rng = np.random.default_rng(20261022)
    price = np.exp(np.cumsum(rng.normal(0, 0.02, 800))) * 100
    frame = pd.DataFrame(
        {
            "open": price,
            "high": price * 1.02,
            "low": price * 0.98,
            "close": price,
            "volume": np.ones(800) * 100,
        }
    )
    strategy = cls({})

    def calculate(data):
        result = strategy.populate_indicators(data.copy(), {})
        result = strategy.populate_entry_trend(result, {})
        return strategy.populate_exit_trend(result, {})

    full = calculate(frame)
    for boundary in (300, 650):
        changed = frame.copy()
        changed.loc[boundary:, ["open", "high", "low", "close"]] *= 100
        pd.testing.assert_frame_equal(full.iloc[:boundary], calculate(changed).iloc[:boundary])


def test_marked_equity_includes_open_short_funding_and_entry_cost():
    clocks = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    stats = {
        "trades": [
            {
                "open_date": clocks[0],
                "close_date": clocks[2],
                "profit_abs": 10.005,
                "pair": "BTC/USDT:USDT",
                "amount": 1,
                "open_rate": 100,
                "fee_open": 0.0005,
                "is_short": True,
            }
        ]
    }
    market = {"BTC/USDT:USDT": pd.DataFrame({"date": clocks, "open": [100, 90, 90]})}
    funding = {
        "BTC/USDT:USDT": pd.DataFrame({"_ff_date_ns": [clocks[1].value], "_ff_fee_per_unit": [0.1]})
    }
    result = equity_observations(stats, market, funding, "2024-01-01", "2024-01-01T03:00", 1000)
    assert result["equity"].tolist() == pytest.approx([999.95, 1010.05, 1010.005])


def test_open_loss_is_not_hidden_until_the_trade_closes():
    clocks = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    stats = {
        "trades": [
            {
                "open_date": clocks[0],
                "close_date": clocks[2],
                "profit_abs": 0,
                "pair": "BTC/USDT:USDT",
                "amount": 1,
                "open_rate": 100,
                "fee_open": 0,
                "is_short": False,
            }
        ]
    }
    market = {"BTC/USDT:USDT": pd.DataFrame({"date": clocks, "open": [100, 70, 100]})}
    funding = {
        "BTC/USDT:USDT": pd.DataFrame(
            {
                "_ff_date_ns": pd.Series(dtype="int64"),
                "_ff_fee_per_unit": pd.Series(dtype="float64"),
            }
        )
    }
    result = equity_observations(stats, market, funding, "2024-01-01", "2024-01-01T03:00", 1000)
    assert result["equity"].iloc[-1] == 1000
    assert result["drawdown"].max() == pytest.approx(0.03)


def test_missing_stress_scenario_prevents_promotion(plan):
    row = {
        "strategy": "CryptoTrend",
        "closed_trades": 100,
        "net_return": 0.1,
        "profit_factor": 1.3,
        "hourly_observed_drawdown": 0.03,
        "capacity_review_required": False,
        "extra_cost_bps_per_side": 0,
    }
    assert not screening([row], "CryptoTrend", plan)["passed"]
    all_costs = [dict(row, extra_cost_bps_per_side=cost) for cost in (0, 3, 10)]
    assert screening(all_costs, "CryptoTrend", plan)["passed"]
    all_costs[-1]["net_return"] = -0.01
    assert not screening(all_costs, "CryptoTrend", plan)["passed"]
    all_costs[-1]["net_return"] = float("nan")
    assert not screening(all_costs, "CryptoTrend", plan)["passed"]


@pytest.mark.parametrize("name", ["CryptoTrend", "CryptoBreakout", "CryptoReversion"])
def test_native_strategy_resolver_loads_frozen_candidates(name, tmp_path):
    from freqtrade.resolvers import StrategyResolver

    path = Path(__file__).resolve().parents[2] / "freqtrade/research"
    strategy = StrategyResolver.load_strategy(
        {
            "strategy": name,
            "strategy_path": str(path),
            "user_data_dir": tmp_path,
            "trading_mode": "futures",
            "stake_currency": "USDT",
            "stake_amount": 150,
        }
    )
    assert strategy.get_strategy_name() == name
    assert strategy.can_short
    assert not strategy.position_adjustment_enable


@pytest.mark.parametrize(
    "field,value",
    [("leverage", 2), ("holdout_start", "2025-06-01"), ("extra_cost_bps_per_side", [0])],
)
def test_research_contract_rejects_leverage_overlap_and_missing_costs(plan, field, value):
    changed = deepcopy(plan)
    changed[field] = value
    with pytest.raises(ValueError):
        validate_plan(changed)
