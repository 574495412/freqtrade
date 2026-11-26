"""Measure native account returns and drawdown in prior-day BTC volatility regimes."""

import json
from math import isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save


HERE = Path(__file__).resolve().parent
DAY = pd.Timedelta(days=1)
PROTOCOL = {
    "sample": "Complete UTC days within the January–November 2026 archived deployment-aligned native account",
    "volatility": "Square root of 365 times the mean of the previous seven completed BTC daily log returns squared",
    "comparison": "High means current observation is at least the 80th percentile of the prior 90 observed 7-day volatilities",
    "windows": ["entire calendar", "January–June", "July through the end"],
    "outcomes": "Cash account daily return, share of days below -2 percent, and within-day peak-to-trough on 5-minute marks including both day boundaries",
    "scope": "Retrospective regime attribution without account replay or weight changes. Contiguous volatility regimes are serially dependent.",
}


def completed_btc_closes(rows):
    start = pd.to_datetime([int(row[0]) for row in rows], unit="ms", utc=True)
    end = pd.to_datetime([int(row[6]) for row in rows], unit="ms", utc=True)
    assert np.all(np.diff(start) == DAY) and (end < start + DAY).all()
    close = pd.Series([float(row[4]) for row in rows], index=start + DAY)
    assert close.gt(0).all()
    return close


def bitcoin_regime(close):
    volatility = np.sqrt(np.log(close).diff().pow(2).rolling(7).mean() * 365)
    prior_80th = volatility.shift().rolling(90).quantile(.8)
    frame = pd.DataFrame({"volatility": volatility, "prior_80th": prior_80th}).dropna()
    frame["high"] = frame["volatility"].ge(frame["prior_80th"])
    return frame


def daily_metrics(equity, regime, plan):
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="5min")
    assert equity.index.as_unit("ns").equals(grid.as_unit("ns"))
    dates = pd.date_range(grid[0], grid[-1].floor("D"), freq="D")
    assert dates.isin(regime.index).all()
    start = equity.loc[dates]
    rows = regime.loc[dates[:-1]].copy()
    rows["net_return"] = start.shift(-1).div(start).sub(1).dropna()
    for date in rows.index:
        within_day = equity.loc[date:date + DAY]
        rows.loc[date, "intraday_drawdown"] = (1 - within_day / within_day.cummax()).max()
    assert isclose((1 + rows["net_return"]).prod(), start.iloc[-1] / start.iloc[0], abs_tol=1e-12)
    return rows


def summarize(rows):
    return {
        "dates": len(rows), "mean_return": float(rows["net_return"].mean()),
        "median_return": float(rows["net_return"].median()),
        "losing_days": int(rows["net_return"].lt(0).sum()),
        "days_at_or_below_minus_2pct": int(rows["net_return"].le(-.02).sum()),
        "worst_day": rows["net_return"].idxmin().isoformat(),
        "worst_day_return": float(rows["net_return"].min()),
        "maximum_within_day_drawdown": float(rows["intraday_drawdown"].max()),
        "median_within_day_drawdown": float(rows["intraday_drawdown"].median()),
        "median_prior_btc_realized_volatility": float(rows["volatility"].median()),
    }


def main():
    plan_path = HERE / "plan.json"
    plan = read(plan_path)
    schedule = read(checked(plan["historical_schedule"]))
    btc = schedule["daily_sources"]["BTC/USDT:USDT"]
    path = checked(btc)
    anchor = HERE / "volatility_regime_freeze.json"
    archived_reports = {cost: receipt(HERE / f"comparison_{cost}bp.json") for cost in ("15", "8")}
    freeze = {"protocol": PROTOCOL, "script": receipt(Path(__file__)), "plan": receipt(plan_path),
              "schedule": plan["historical_schedule"], "btc": btc, "reported_accounts": archived_reports}
    if anchor.exists():
        assert read(anchor) == freeze
    else:
        with anchor.open("x") as out:
            json.dump(freeze, out, indent=2)
            out.write("\n")
    closes = completed_btc_closes(read(path))
    regime = bitcoin_regime(closes)
    for cutoff in ("2026-01-01", "2026-07-01"):
        pd.testing.assert_frame_equal(bitcoin_regime(closes.loc[:cutoff]), regime.loc[:cutoff],
                                      check_exact=True)
    reports = {}
    for cost, account_receipt in archived_reports.items():
        account = read(checked(account_receipt))["control"]
        assert account["five_minute_nav_reconciliation"]["passed"]
        source, = [row for row in account["sources"] if row["path"].endswith(".parquet")]
        curve = pd.read_parquet(checked(source))
        curve = curve.set_index("date")["marked_equity"]
        rows = daily_metrics(curve, regime, plan)
        periods = {"full": rows, "first_half": rows.loc[rows.index.month <= 6],
                   "second_half": rows.loc[rows.index.month > 6]}
        reports[cost] = {
            "account": source, "last_mark_included": (rows.index[-1] + DAY).isoformat(),
            "periods": {period: {"high": summarize(sample[sample.high]),
                                 "other": summarize(sample[~sample.high])}
                        for period, sample in periods.items()},
        }
        rows.to_csv(HERE / f"volatility_regime_daily_{cost}bp.csv")
    save(HERE / "volatility_regime.json", {
        "protocol": PROTOCOL, "freeze": receipt(anchor), "by_cost": reports,
        "new_native_accounts": 0, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
