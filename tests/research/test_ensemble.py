"""Portfolio accounting uses complete synchronous native accounts and their actual capital."""

import copy

import pandas as pd
import pytest

from research.crypto.ensemble import combine_accounts


def accounts_and_plan():
    times = pd.date_range("2024-01-01T01:00Z", periods=3, freq="h")
    accounts = []
    for name, values, profit, funding in [
        ("A", [1100.0, 900.0, 1100.0], 100.0, 10.0),
        ("B", [900.0, 1100.0, 1050.0], 50.0, 0.0),
    ]:
        trade = {
            "profit_abs": profit,
            "funding_fees": funding,
            "is_open": False,
            "close_timestamp": int(times[-1].timestamp() * 1000),
        }
        row = {
            "phase": "train",
            "start": "2024-01-01",
            "end": "2024-01-01T03:00",
            "extra_cost_bps_per_side": 3,
            "native_final_account_reconciled": True,
            "net_profit_usdt": profit,
        }
        accounts.append(
            {
                "name": name,
                "initial": 1000,
                "result": row,
                "equity": pd.DataFrame({"date": times, "equity": values, "open_trades": [1, 1, 0]}),
                "stats": {"profit_total_abs": profit, "trades": [trade]},
            }
        )
    plan = {
        "candidate": "fixed_portfolio",
        "initial_equity_usdt": 2000,
        "maximum_open_positions": 12,
        "screening_targets": {
            "minimum_closed_trades_per_evaluation": 2,
            "minimum_profit_factor": 1.1,
            "maximum_observed_drawdown": 0.15,
        },
    }
    return accounts, plan


def test_sum_nav_before_drawdown_and_do_not_add_funding_twice():
    accounts, plan = accounts_and_plan()
    result, equity, trades = combine_accounts(accounts, plan)
    assert equity.equity.tolist() == [2000.0, 2000.0, 2000.0, 2150.0]
    assert result["hourly_observed_drawdown"] == 0.0
    assert result["net_profit_usdt"] == 150.0
    assert result["funding_net_usdt"] == 10.0
    assert len(trades) == 2
    assert result["economic_audit"]["passed"]


def test_refuse_missing_component_hour_instead_of_filling_it():
    accounts, plan = accounts_and_plan()
    accounts[1]["equity"] = accounts[1]["equity"].drop(index=1)
    with pytest.raises(ValueError, match="misaligned"):
        combine_accounts(accounts, plan)


def test_refuse_capital_rescaling_and_duplicate_account():
    accounts, plan = accounts_and_plan()
    with pytest.raises(ValueError, match="capital"):
        combine_accounts(accounts, dict(plan, initial_equity_usdt=1000))
    accounts[1] = copy.deepcopy(accounts[0])
    with pytest.raises(ValueError, match="counted twice"):
        combine_accounts(accounts, plan)


def test_refuse_terminal_inventory_or_cash_mismatch():
    accounts, plan = accounts_and_plan()
    accounts[0]["stats"]["trades"][0]["is_open"] = True
    with pytest.raises(ValueError, match="closed and reconciled"):
        combine_accounts(accounts, plan)
    accounts[0]["stats"]["trades"][0]["is_open"] = False
    accounts[0]["equity"].loc[2, "equity"] = 1101.0
    with pytest.raises(ValueError, match="terminal NAV"):
        combine_accounts(accounts, plan)
