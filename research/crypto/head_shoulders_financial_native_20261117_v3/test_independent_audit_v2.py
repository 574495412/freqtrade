"""Synthetic monetary and source-clock fixtures, never market account outputs."""

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
from independent_account_audit_v2 import (
    HOUR,
    boundary_evidence,
    cash_from_orders,
    fixed_combo,
    funding_history,
    missing_exposure,
    ms,
    sha,
    source_terminal,
    source_trade_check,
    standalone_gate,
    trade_path,
    two_fill_funding,
)


HERE = Path(__file__).absolute().parent


def rejected(call):
    try:
        call()
    except (AssertionError, ValueError, KeyError):
        return
    raise AssertionError("Invalid input was accepted")


def fill(hour, amount, entry, price=100):
    return {
        "order_filled_timestamp": int(hour * HOUR),
        "amount": amount,
        "ft_is_entry": entry,
        "safe_price": price,
    }


def run():
    frozen = json.loads((HERE / "independent_audit_freeze_v2.json").read_text())
    for path, digest in frozen["files"].items():
        assert sha(path) == digest
    passed = []

    def record(name):
        passed.append(name)

    clocks = np.arange(5) * HOUR
    events = np.arange(1, 4) * HOUR
    cases = [
        (
            [fill(0.5, 2, True), fill(3.5, 2, False)],
            [1.0, 2.0, 3.0],
            [0.0, 0.0, -2.0, -6.0, -12.0],
            -12.0,
        ),
        (
            [fill(0.5, 2, True), fill(2, 1, True), fill(3.5, 3, False)],
            [1.0, 1.0, 1.0],
            [0.0, 0.0, -2.0, -4.0, -10.0],
            -10.0,
        ),
        (
            [fill(0.5, 2, True), fill(2, 1, False), fill(3.5, 1, False)],
            [1.0, 1.0, 1.0],
            [0.0, 0.0, -2.0, -4.0, -6.0],
            -6.0,
        ),
        (
            [fill(1, 2, True), fill(3, 2, False)],
            [1.0, 1.0, 1.0],
            [0.0, 0.0, -2.0, -4.0, -6.0],
            -6.0,
        ),
    ]
    for number, (orders, units, expected, final) in enumerate(cases):
        for short in (False, True):
            values, terminal, _ = funding_history(
                {"orders": orders, "is_short": short}, events, np.array(units), clocks
            )
            sign = -1 if short else 1
            np.testing.assert_array_equal(values, np.array(expected) * sign)
            assert terminal == final * sign
            if len(orders) == 2:
                strict_trade = {
                    "orders": orders,
                    "is_short": short,
                    "open_timestamp": orders[0]["order_filled_timestamp"],
                    "close_timestamp": orders[-1]["order_filled_timestamp"],
                }
                closed, closed_final = two_fill_funding(
                    strict_trade, events, np.array(units), clocks
                )
                np.testing.assert_array_equal(closed, values)
                assert closed_final == terminal
            record(f"funding_{number}_{short}")
    trade = {
        "orders": [fill(1, 2, True), fill(3, 2, False, 110)],
        "is_short": False,
        "fee_open": 0.001,
        "fee_close": 0.001,
        "funding_fees": -6.0,
        "profit_abs": 13.58,
        "open_timestamp": HOUR,
        "close_timestamp": 3 * HOUR,
        "pair": "AXS/USDT:USDT",
    }
    assert cash_from_orders(trade)["net_pnl"] == "13.580"
    record("decimal_cash_both_fees_and_funding")
    path, _, _, final, _ = trade_path(trade, clocks, np.full(5, 110.0), events, np.ones(3))
    np.testing.assert_allclose(path, [0.0, 0.0, 17.58, 15.58, 13.58], rtol=0, atol=1e-12)
    assert final == -6
    record("nav_before_clock_fills_and_exit_reserve")
    assert boundary_evidence(trade, set(events))["status"] == "UNKNOWN"
    record("minified_boundary_unknown")
    full = copy.deepcopy(trade)
    for order in full["orders"]:
        order["order_timestamp"] = order["order_filled_timestamp"]
    assert boundary_evidence(full, set(events))["status"] == "VERIFIED"
    record("full_immediate_boundary_timestamps")
    full["orders"][0]["order_timestamp"] -= 300000
    assert boundary_evidence(full, set(events))["status"] == "UNKNOWN"
    record("deferred_boundary_unknown")
    assert missing_exposure([trade], trade["pair"], 2 * HOUR)["status"] == "UNKNOWN"
    assert missing_exposure([trade], trade["pair"], HOUR)["status"] == "UNKNOWN"
    assert missing_exposure([trade], trade["pair"], 3 * HOUR)["status"] == "UNKNOWN"
    assert missing_exposure([trade], trade["pair"], 4 * HOUR)["status"] == "NO_GROSS_EXPOSURE"
    record("missing_rate_held_entry_exit_flat")
    opposite = copy.deepcopy(trade)
    opposite["is_short"] = True
    missing = missing_exposure([trade, opposite], trade["pair"], 2 * HOUR)
    assert missing["status"] == "UNKNOWN" and len(missing["campaigns"]) == 2
    record("opposite_campaigns_never_net_missing_funding")
    origin = pd.Timestamp("2023-08-01T00:00:00Z")
    data = pd.DataFrame(
        {
            "date": pd.date_range(origin, periods=6, freq="h"),
            "close": [100.0, 100.0, 100.0, 120.0, 80.0, 100.0],
        }
    )
    event = {
        "pair": "TEST/USDT:USDT",
        "event_id": "fixture",
        "inverted": True,
        "break_i": 1,
        "head_width": 4,
        "r_shoulder_p": float(np.log(90.0)),
        "neck_end": float(np.log(100.0)),
        "head_height": float(np.log(110.0)) - float(np.log(100.0)),
        "confirmation_bar_open": origin + pd.Timedelta(hours=1),
        "expiry_bar_open": origin + pd.Timedelta(hours=4),
        "entry_available": origin + pd.Timedelta(hours=2),
        "self_terminal_at_confirmation": False,
    }
    event["source_stop_log_price"] = event["r_shoulder_p"]
    event["source_target_log_price"] = event["neck_end"] + event["head_height"]
    assert (
        source_terminal(event, data, ms(origin) + 4 * HOUR)["available_ms"] == ms(origin) + 4 * HOUR
    )
    record("long_strict_target_close_clock")
    assert source_terminal(event, data, ms(origin) + 4 * HOUR - 1) is None
    record("unclosed_hour_not_visible")
    shock = data.copy()
    shock.loc[4:, "close"] = [1e9, 1e-9]
    assert source_terminal(event, data, ms(origin) + 4 * HOUR) == source_terminal(
        event, shock, ms(origin) + 4 * HOUR
    )
    record("future_shock_does_not_change_past_exit")
    touched = data.copy()
    touched["close"] = 100.0
    touched.loc[2, "close"] = 110.0
    touched.loc[3, "close"] = 90.0
    terminal = source_terminal(event, touched, ms(origin) + 5 * HOUR)
    assert (
        terminal["reason"] == "hs_source_expiry"
        and terminal["available_ms"] == ms(origin) + 5 * HOUR
    )
    record("equality_not_cross_expiry_width_minus_one")
    short_event = copy.deepcopy(event)
    short_event["inverted"] = False
    short_event["r_shoulder_p"] = short_event["source_stop_log_price"] = float(np.log(110.0))
    short_event["head_height"] = float(np.log(100.0)) - float(np.log(90.0))
    short_event["source_target_log_price"] = float(np.log(90.0))
    bearish = data.copy()
    bearish.loc[3, "close"] = 80.0
    assert (
        source_terminal(short_event, bearish, ms(origin) + 4 * HOUR)["reason"] == "hs_source_target"
    )
    assert source_terminal(short_event, data, ms(origin) + 4 * HOUR)["reason"] == "hs_source_stop"
    record("short_target_and_stop_directions")
    long_stop = data.copy()
    long_stop.loc[3, "close"] = 80.0
    assert source_terminal(event, long_stop, ms(origin) + 4 * HOUR)["reason"] == "hs_source_stop"
    record("long_strict_stop")
    actual = {
        "pair": event["pair"],
        "enter_tag": "fixture",
        "is_short": False,
        "open_timestamp": ms(origin) + 2 * HOUR,
        "close_timestamp": ms(origin) + 4 * HOUR,
        "exit_reason": "hs_source_target",
        "is_open": False,
        "has_open_orders": False,
        "nr_of_successful_entries": 1,
        "nr_of_successful_exits": 1,
        "open_fill_timestamp": ms(origin) + 2 * HOUR,
        "orders": [fill(0, 1, True), fill(0, 1, False)],
    }
    actual["orders"][0]["order_filled_timestamp"] = actual["open_timestamp"]
    actual["orders"][1]["order_filled_timestamp"] = actual["close_timestamp"]
    assert source_trade_check(actual, event, data)["category"] == "SOURCE_CLOSE_RULE"
    record("actual_tag_first_source_exit_exact_clock")
    minified = copy.deepcopy(actual)
    for key in (
        "has_open_orders",
        "nr_of_successful_entries",
        "nr_of_successful_exits",
        "open_fill_timestamp",
    ):
        del minified[key]
    source_trade_check(minified, event, data)
    assert (
        boundary_evidence(minified, {actual["open_timestamp"]}, strict_hs_proven=True)["status"]
        == "VERIFIED"
    )
    record("strict_hs_minified_schema_static_two_fill_proof")
    pending = copy.deepcopy(actual)
    pending["has_open_orders"] = True
    rejected(lambda: source_trade_check(pending, event, data))
    record("explicit_pending_order_rejected")
    unequal = copy.deepcopy(actual)
    unequal["orders"][1]["amount"] = 0.9
    rejected(lambda: source_trade_check(unequal, event, data))
    record("partial_exit_not_strict_hs_two_fill_path")
    late = copy.deepcopy(actual)
    late["close_timestamp"] += 300000
    late["orders"][1]["order_filled_timestamp"] += 300000
    rejected(lambda: source_trade_check(late, event, data))
    record("late_exit_rejected")
    wrong = copy.deepcopy(actual)
    wrong["enter_tag"] = "wrong"
    rejected(lambda: source_trade_check(wrong, event, data))
    record("wrong_tag_rejected")
    risk = copy.deepcopy(actual)
    risk["exit_reason"] = "stop_loss"
    rejected(lambda: source_trade_check(risk, event, data))
    record("same_clock_source_precedes_native_risk")
    risk["close_timestamp"] -= HOUR
    risk["orders"][1]["order_filled_timestamp"] -= HOUR
    assert source_trade_check(risk, event, data)["category"] == "NATIVE_RISK"
    record("earlier_native_risk_separate")
    force = copy.deepcopy(risk)
    force["exit_reason"] = "force_exit"
    assert (
        source_trade_check(force, event, data, force["close_timestamp"])["category"]
        == "NATIVE_TERMINAL_FORCE"
    )
    record("terminal_force_separate_from_source")
    zero = copy.deepcopy(event)
    zero["head_width"] = 1
    zero["expiry_bar_open"] = zero["confirmation_bar_open"]
    assert source_terminal(zero, data, actual["open_timestamp"])["self_terminal"]
    rejected(lambda: source_trade_check(actual, zero, data))
    record("zero_duration_no_executable_entry")
    gap = data.drop(index=2)
    rejected(lambda: source_terminal(event, gap, actual["close_timestamp"]))
    record("closed_source_gap_rejected")
    plan = json.loads((HERE / "plan.json").read_text())
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    hs = pd.Series(np.linspace(500, 600, len(grid)), index=grid)
    nfi = pd.Series(np.linspace(500, 550, len(grid)), index=grid)
    baseline = pd.Series(np.linspace(1000, 1100, len(grid)), index=grid)
    nav, combo = fixed_combo(hs, nfi, baseline, plan["periods"], True)
    assert nav.iloc[0] == 1000 and nav.iloc[-1] == 1150 and combo["passed"]
    assert [p["calendar_hours"] for p in combo["periods"]] == [14592, 8761, 5832]
    record("actual500_sum_full_calendar_slices")
    assert not fixed_combo(hs, nfi, baseline, plan["periods"], False)[1]["passed"]
    record("unknown_sleeve_blocks_combo")
    rejected(lambda: fixed_combo(hs * 2, nfi, baseline, plan["periods"], True))
    record("two1000_or_scaled_sleeve_rejected")
    accounts = [
        {
            "capital": capital,
            "cost_bps": cost,
            "status": "VERIFIED",
            "periods": [
                {
                    "net_return": 0.1,
                    "hourly_mark_drawdown": 0.1,
                    "complete_24h_daily_nav_sharpe": 0.6,
                }
            ],
        }
        for capital in (500, 1000)
        for cost in (8, 15)
    ]
    assert all(r["both_costs_passed"] for r in standalone_gate(accounts).values())
    accounts[0]["status"] = "UNKNOWN"
    assert not standalone_gate(accounts)["500"]["both_costs_passed"]
    record("both_costs_and_unknown_gate")
    result = {
        "passed": True,
        "synthetic_cases": len(passed),
        "cases": passed,
        "market_accounts_run": 0,
        "native_backtesting_start_calls": 0,
        "financial_outputs_read": False,
        "freeze_sha256": sha(HERE / "independent_audit_freeze_v2.json"),
    }
    (HERE / "independent_audit_validation_v2.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    run()
