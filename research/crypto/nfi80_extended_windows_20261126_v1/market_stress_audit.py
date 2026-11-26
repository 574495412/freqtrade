"""Attribute observed full-account return to matched two-day stress intervals."""

from math import fsum, isclose
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE


ATTRIBUTION = HERE / "recent_two_year_trade_attribution.json"
PROTOCOL = HERE / "market_stress_15_8_protocol.json"
ORIGINAL_EVENT = HERE / "annual_original_15bp_october_event.json"
STEP = pd.Timedelta(minutes=5)
TWO_DAYS = pd.Timedelta(days=2)
DAY = pd.Timedelta(days=1)
WINDOWS = ("2025", "2026_to_nov09")
BTC_MARK = "futures/BTC_USDT_USDT-5m-mark.feather"


def select_market_window(reference, start, stop):
    native = checked(reference[".zip"])
    plan_path = receipt(native.parents[1] / "registered_plan.json")
    plan = read(checked(plan_path))
    files = read(checked(plan["data_receipts"]))
    path = {"path": str(Path(plan["datadir"]) / BTC_MARK), "sha256": files[BTC_MARK]}
    prices = pd.read_feather(checked(path), columns=["date", "open"]).set_index("date").open
    assert prices.index.is_unique and prices.index.is_monotonic_increasing and prices.gt(0).all()
    if start is None:
        first = pd.Timestamp(plan["start"])
        windows = pd.date_range(first, stop - TWO_DAYS, freq="D")
        inspected = {}
        for day in windows:
            curve = prices.loc[day:day + DAY]
            assert len(curve) == 289, day
            inspected[day] = float((1 - curve / curve.cummax()).max())
        event = min(inspected, key=lambda t: (-inspected[t], t))
        rule = {"selection_days": len(inspected), "largest_intraday_mark_drawdown": inspected[event]}
    else:
        event = start
        rule = {"event_source": receipt(ORIGINAL_EVENT)}
    end = event + TWO_DAYS
    tape = prices.loc[event:end]
    assert len(tape) == 577 and tape.index[0] == event and tape.index[-1] == end
    trough = (1 - tape / tape.cummax()).idxmax()
    peak = tape.loc[:trough].idxmax()
    return event, end, {"native_btc_mark_tape": path, "registered_plan": plan_path, **rule,
                         "btc_end_to_end_return": float(tape.iloc[-1] / tape.iloc[0] - 1),
                         "btc_local_peak_to_trough_drawdown": float(1 - tape.at[trough] / tape.at[peak]),
                         "btc_peak": peak.isoformat(), "btc_trough": trough.isoformat()}


def campaign(trade):
    return {"pair": trade["pair"], "tag": trade["enter_tag"].strip(), "close": trade["close_date"],
            "entry_fills": sum(row["ft_is_entry"] for row in trade["orders"]), "profit_abs_usdt": trade["profit_abs"]}


def replay_event(refs, start, end):
    nav = pd.read_parquet(checked(refs[".parquet"])).set_index("date")
    assert nav.index.is_unique and nav.index.is_monotonic_increasing
    event = nav.loc[start:end]
    assert event.index.equals(pd.date_range(start, end, freq=STEP).as_unit(event.index.unit))
    marks = event.marked_equity
    initial, final = (float(marks.iloc[i]) for i in (0, -1))
    total_multiplier = float(nav.marked_equity.iloc[-1] / nav.marked_equity.iloc[0])
    event_multiplier = final / initial
    before = float(initial / nav.marked_equity.iloc[0])
    after = float(nav.marked_equity.iloc[-1] / final)
    assert isclose(total_multiplier / event_multiplier, before * after, rel_tol=1e-13)
    start_ms, end_ms = (stamp.value // 1_000_000 for stamp in (start, end))
    trades = stats_from(checked(refs[".zip"]))["trades"]
    natural = [trade for trade in trades if trade["exit_reason"] != "force_exit"
               and start_ms <= trade["close_timestamp"] < end_ms]
    realized = fsum(trade["profit_abs"] for trade in natural)
    assert isclose(realized, float(event.closed_realized_pnl.iloc[-1] - event.closed_realized_pnl.iloc[0]),
                   abs_tol=1e-6, rel_tol=0)
    open_pnl = float(event.open_pnl.iloc[-1] - event.open_pnl.iloc[0])
    adjustment = event.marked_equity - event.equity
    correction = float(adjustment.iloc[-1] - adjustment.iloc[0])
    assert isclose(final - initial, realized + open_pnl + correction, abs_tol=1e-6, rel_tol=0)
    drawdown = 1 - marks / marks.cummax()
    weakest = drawdown.idxmax()
    return {
        "source": refs, "marks_including_boundary": len(event), "event_start_nav_usdt": initial,
        "event_end_nav_usdt": final, "event_net_change_usdt": final - initial, "event_return": event_multiplier - 1,
        "event_max_five_minute_drawdown": float(drawdown.max()), "event_drawdown_trough": weakest.isoformat(),
        "positions_at_event_drawdown_trough": int(event.at[weakest, "open_trades"]),
        "peak_trade_price_gross_to_marked_nav": float(event.gross.div(marks).max()),
        "natural_realized_pnl_usdt": realized, "change_in_open_trade_price_pnl_usdt": open_pnl,
        "change_in_trade_to_mark_adjustment_usdt": correction, "natural_campaign_count": len(natural),
        "largest_realized": [campaign(row) for row in sorted(natural, key=lambda row: row["profit_abs"], reverse=True)[:3]],
        "smallest_realized": [campaign(row) for row in sorted(natural, key=lambda row: row["profit_abs"])[:3]],
        "full_window_multiplier": total_multiplier, "non_event_observed_multiplier_product": before * after,
    }


def main():
    receipt_ref = receipt(ATTRIBUTION)
    groups = read(checked(receipt_ref))["comparisons"]
    selections = {}
    for period in WINDOWS:
        source = groups["15"][period]
        start = pd.Timestamp("2025-10-10", tz="UTC") if period == "2025" else None
        selections[period] = select_market_window(source["native_receipts"]["12"], start,
                                                  pd.Timestamp(source["comparison_end"]))
    anchor = write_once(HERE / "market_stress_window_selection.json", {
        "protocol": receipt(PROTOCOL), "source": receipt_ref, "implementation": receipt(Path(__file__)),
        "windows": {period: {"start": begin.isoformat(), "stop": end.isoformat(), **info}
                    for period, (begin, end, info) in selections.items()},
    })
    result = {}
    for cost, periods in groups.items():
        result[cost] = {}
        for period, (start, end, info) in selections.items():
            archived = periods[period]["native_receipts"]
            accounts = {name: replay_event(refs, start, end) for name, refs in archived.items()}
            a, b = (accounts[name] for name in ("12", "14"))
            increment = b["event_net_change_usdt"] - a["event_net_change_usdt"]
            total = (b["full_window_multiplier"] - a["full_window_multiplier"]) * 1000
            assert isclose(total, periods[period]["ending_equity_gain_usdt"], abs_tol=1e-8)
            result[cost][period] = {"event": info, "accounts": accounts,
                                    "event_14_minus_12_dollar_pnl_usdt": increment,
                                    "event_fraction_of_observed_final_nav_difference": increment / total,
                                    "non_event_multiplier_ratio_14_to_12": b["non_event_observed_multiplier_product"] /
                                                                           a["non_event_observed_multiplier_product"]}
            print(cost, period, start.isoformat(), *(round(accounts[n]["event_return"]*100, 4) for n in ("12", "14")),
                  round(increment, 4), flush=True)
    write_once(HERE / "market_stress_15_8_audit.json", {"window_selection": anchor, "groups": result, "orders_enabled": False})


if __name__ == "__main__":
    main()
