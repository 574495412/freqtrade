"""Reconcile the worst complete UTC day in the verified 2024 fixed-boost wallet."""

from math import fsum, isclose
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .volatility_across_years import annual_plan, annual_prices, evaluate


HERE = Path(__file__).resolve().parent


def main():
    account_report = receipt(HERE / "annual_2024_fixed12_15bp_verified.json")
    account = read(checked(account_report))["accounts"]["fixed_boost_12"]
    plan, btc, sources = annual_plan(account, 2024)
    rows = evaluate(plan, annual_prices(btc), sources["nav"])
    start = rows["net_return"].idxmin()
    end = start + pd.Timedelta(days=1)
    wallet = pd.read_parquet(checked(sources["nav"])).set_index("date").loc[start:end]
    assert wallet.index.as_unit("ns").equals(pd.date_range(start, end, freq="5min").as_unit("ns"))
    previous, current = wallet.iloc[0], wallet.iloc[-1]
    native, = (source for source in account["receipts"] if source["path"].endswith(".zip"))
    realized, open_positions = [], {}
    for trade in stats_from(checked(native))["trades"]:
        opened, closed = (pd.Timestamp(trade[f"{flag}_timestamp"], unit="ms", tz="UTC") for flag in ("open", "close"))
        if start < closed <= end:
            realized.append({"pair": trade["pair"], "entry_tag": trade["enter_tag"],
                             "exit": closed.isoformat(), "exit_reason": trade["exit_reason"],
                             "net_realized_usdt": trade["profit_abs"]})
        for boundary in (start, end):
            if opened <= boundary < closed:
                open_positions.setdefault(boundary, []).append((trade["pair"], trade["is_short"]))
    assert len(open_positions[start]) == previous["open_trades"]
    assert len(open_positions[end]) == current["open_trades"]
    realized_delta = current["closed_realized_pnl"] - previous["closed_realized_pnl"]
    open_delta = current["open_pnl"] - previous["open_pnl"]
    mark_delta = (current["marked_equity"] - current["equity"]) - (previous["marked_equity"] - previous["equity"])
    total = current["marked_equity"] - previous["marked_equity"]
    assert isclose(realized_delta, fsum(row["net_realized_usdt"] for row in realized), abs_tol=1e-8)
    assert isclose(total, realized_delta + open_delta + mark_delta, abs_tol=1e-8)
    prior_state = rows.loc[start]
    write_once(HERE / "annual_2024_fixed12_worst_day.json", {
        "scope": "Fixed 12-slot boost, year 2024, 15 bp per side, worst complete midnight-to-midnight observation",
        "account": account_report, "native_trades": native, "verified_nav": sources["nav"], "btc_daily_input": btc,
        "start": start.isoformat(), "end": end.isoformat(), "five_minute_marks": len(wallet),
        "start_equity_usdt": float(previous["marked_equity"]), "end_equity_usdt": float(current["marked_equity"]),
        "daily_net_return": float(prior_state["net_return"]),
        "prior_btc_volatility": float(prior_state["volatility"]),
        "prior_btc_volatility_threshold": float(prior_state["prior_80th"]),
        "prior_high_volatility": bool(prior_state["high"]),
        "closed_realized_change_usdt": float(realized_delta), "open_pnl_change_usdt": float(open_delta),
        "additional_mark_change_usdt": float(mark_delta), "total_change_usdt": float(total),
        "open_position_side_count": {
            boundary.isoformat(): {"short": sum(side for _, side in positions),
                                   "long": sum(not side for _, side in positions)}
            for boundary, positions in open_positions.items()
        },
        "native_realized_trades": realized, "new_native_accounts": 0, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
