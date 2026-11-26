"""Diagnose where a lagged BTC trend filter could alter the 14-slot native path."""

from collections import Counter
from math import fsum
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import COSTS, write_once
from .annual_trade_scale import FIVE_MINUTES
from .capacity_2024_drawdown_attribution import worst_drawdown
from .continuous_native_inputs import IndependentShardRead
from .inputs import HERE
from .recent_two_year_attribution import ledger, reports


BTC = "BTC/USDT:USDT"
LOOKBACK = 200


def trend_receipt(source):
    frame = IndependentShardRead(source)(BTC, "1d-futures").set_index("date")
    assert frame.index.is_monotonic_increasing and frame.index.is_unique
    assert frame.index.normalize().equals(frame.index)
    calendar = pd.date_range(frame.index[0], frame.index[-1], freq="D")
    frame = frame.reindex(calendar)
    close = frame.close.where(frame.close.gt(0) & np.isfinite(frame.close)
                              & frame.volume.gt(0) & np.isfinite(frame.volume))
    average = close.rolling(LOOKBACK).mean()
    for i in range(LOOKBACK - 1, len(close)):
        if pd.notna(average.iloc[i]):
            np.testing.assert_allclose(average.iloc[i], fsum(close.iloc[i - LOOKBACK + 1:i + 1]) / LOOKBACK,
                                       rtol=0, atol=5e-11)
    update = close.index + pd.Timedelta(days=1) + FIVE_MINUTES
    return pd.DataFrame({"candle_open": close.index, "close": close.to_numpy(), "sma": average.to_numpy()}, index=update)


def trend_at(clock, trend):
    row = trend.iloc[trend.index.searchsorted(clock, side="right") - 1]
    assert row.candle_open + pd.Timedelta(days=1) < clock
    if pd.isna(row.sma) or pd.isna(row.close):
        return {"state": "unknown"}
    return {"state": "defensive" if row.close <= row.sma else "open",
            "btc_daily_candle_open": row.candle_open.isoformat(), "btc_close": float(row.close), "sma_200": float(row.sma)}


def native_evidence(wallet):
    archive = checked(wallet["refs"][".zip"])
    refs = wallet["refs"] | {"initial_entry_checks": receipt(archive.parents[1] / "initial_entry_checks.json"),
                            "plan": receipt(archive.parents[1] / "registered_plan.json")}
    plan = read(checked(refs["plan"]))
    native = stats_from(archive)
    assert native["max_open_trades"] == plan["candidates"][0]["max_open_trades"] == 14
    assert not any(native[key] for key in ("timedout_entry_orders", "canceled_trade_entries", "replaced_entry_orders"))
    ordered = {}
    exits = Counter()
    for trade in native["trades"]:
        order = next(order for order in trade["orders"] if order["ft_is_entry"])
        key = (pd.Timestamp(order["order_filled_timestamp"], unit="ms", tz="UTC"), trade["pair"])
        assert key not in ordered and order["order_filled_timestamp"] == trade["open_timestamp"]
        ordered[key] = trade
        exits[pd.Timestamp(trade["close_timestamp"], unit="ms", tz="UTC")] += 1
    paired_attempts = [row for row in read(checked(refs["initial_entry_checks"])) if row["accepted"]]
    keys = [(pd.Timestamp(row["time"]), row["pair"]) for row in paired_attempts]
    assert len(keys) == len(ordered) and Counter(keys) == Counter(ordered.keys())
    return ordered, exits, keys, refs


def audit_entries(wallet, trend):
    orders, exits, keys, refs = native_evidence(wallet)
    matches = Counter()
    minutes = wallet["curve"].open_trades
    proven, uncertain = [], []
    for clock, pair in keys:
        earlier = matches[clock]
        matches[clock] += 1
        before = int(minutes.at[clock])
        lower = max(0, before + earlier - exits[clock])
        upper = min(13, before + earlier)
        assert lower <= upper
        if upper < 12 or orders[clock, pair]["is_short"]:
            continue
        state = trend_at(clock, trend)
        if state["state"] == "open":
            continue
        trade = orders[clock, pair]
        row = {"pair": pair, "clock": clock.isoformat(), **state,
               "open_trade_count_lower": lower, "open_trade_count_upper": upper,
               "same_bar_all_exits": exits[clock], "recorded_net_pnl_usdt": trade["profit_abs"],
               "naturally_closed": trade["exit_reason"] != "force_exit"}
        (proven if lower >= 12 else uncertain).append(row)
    for clock in set(matches) | set(exits):
        after = clock + FIVE_MINUTES
        assert clock in minutes.index
        if after in minutes.index:
            assert int(minutes.at[clock]) + matches[clock] - exits[clock] == int(minutes.at[after])
    return {"refs": refs, "entries_audited": len(keys), "definite": proven, "unresolved": uncertain}


def audit_drawdown(left, right, trend):
    a = left["curve"]
    b = right["curve"]
    assert a.index.equals(b.index)
    peak, trough, loss = worst_drawdown(b.marked_equity)
    state = trend_at(trough, trend)
    compare = {}
    for slots, curve in (("12", a), ("14", b)):
        nav = curve.loc[trough]
        compare[slots] = {"marked_nav_return_same_interval": float(nav.marked_equity / curve.at[peak, "marked_equity"] - 1),
                          "open_campaigns": int(nav.open_trades),
                          "trade_price_gross_notional_to_marked_nav": float(nav.gross / nav.marked_equity)}
    return {"peak": peak.isoformat(), "trough": trough.isoformat(), "fourteen_slot_drawdown": loss,
            "btc_state_at_trough": state, "same_interval": compare}


def main():
    preflight = receipt(HERE / "recent_two_year_native_preflight.json")
    tape = read(checked(preflight))["manifest"]
    protocol = receipt(HERE / "sma_capacity_protocol.json")
    trend = trend_receipt(tape)
    start = pd.Timestamp("2025-01-01T00:00:00Z")
    stop = pd.Timestamp("2026-11-26T00:00:00Z")
    eligible = trend.loc[start:stop - FIVE_MINUTES]
    assert len(eligible) == 694 and eligible.sma.notna().all() and eligible.close.notna().all()
    assert trend_at(start, trend)["state"] != "unknown"
    sections = {}
    for cost in COSTS:
        cases = {}
        for period, info, base, candidate in reports(cost):
            left, right = ledger(base), ledger(candidate)
            entries = audit_entries(right, trend)
            rows = audit_drawdown(left, right, trend)
            cases[period] = {"control_12_refs": left["refs"], "reconciliation": info, "entry_filter_audit": entries,
                             "fourteen_slot_max_drawdown": rows}
            print(cost, period, len(entries["definite"]), len(entries["unresolved"]), rows["fourteen_slot_drawdown"],
                  flush=True)
        sections[str(cost)] = cases
    write_once(HERE / "sma_capacity_audit.json", {"protocol": protocol, "manifest": tape, "preflight": preflight,
                                                 "source": receipt(Path(__file__)), "sections": sections,
                                                 "new_native_wallet_results": 0, "orders_enabled": False})


if __name__ == "__main__":
    main()
