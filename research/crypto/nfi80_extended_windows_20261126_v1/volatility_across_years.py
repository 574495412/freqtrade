"""Apply the frozen BTC volatility regime to finished NFI native wallets."""

from math import isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt
from research.crypto.nfi80_online_baseline_20261125_v1 import volatility_regime as owner

from .accounts import write_once


HERE = Path(__file__).resolve().parent
ONLINE = owner.HERE
BTC_DAILY = "futures/BTC_USDT_USDT-1d-futures.feather"


def selected_receipt(records, suffix):
    selected, = (row for row in records if row["path"].endswith(suffix))
    return selected


def annual_plan(account, year):
    records = account["receipts"]
    completion = selected_receipt(records, "/completion.json")
    audit = selected_receipt(records, "/account_reconciled.json")
    plan = selected_receipt(records, "/registered_plan.json")
    assert read(checked(completion))["returncode"] == 0
    assert read(checked(audit))["five_minute_nav_reconciliation"]["passed"]
    config = read(checked(plan))
    assert pd.Timestamp(config["start"]).year == year
    path = Path(config["datadir"]) / BTC_DAILY
    btc_hash = read(checked(config["data_receipts"]))[BTC_DAILY]
    return config, {"path": str(path), "sha256": btc_hash}, {
        "account_proof": audit, "native_exit": completion, "native_plan": plan,
        "nav": selected_receipt(records, "/five_minute_equity.parquet"),
    }


def annual_prices(input_file):
    day = pd.read_feather(checked(input_file), columns=["date", "close"]).set_index("date")["close"]
    assert day.index.as_unit("ns").equals(pd.date_range(day.index[0], day.index[-1], freq="D").as_unit("ns"))
    assert day.gt(0).all() and np.isfinite(day).all()
    return day.shift(freq=owner.DAY)


def evaluate(plan, prices, nav):
    regime = owner.bitcoin_regime(prices)
    for split in (plan["start"], f'{pd.Timestamp(plan["start"]).year}-07-01'):
        path = prices.loc[:split]
        pd.testing.assert_frame_equal(owner.bitcoin_regime(path), regime.loc[:path.index[-1]],
                                      check_exact=True)
    data = pd.read_parquet(checked(nav)).set_index("date")["marked_equity"]
    assert data.gt(0).all() and np.isfinite(data).all()
    return owner.daily_metrics(data, regime, plan)


def summaries(rows):
    return {"high": owner.summarize(rows[rows.high]), "other": owner.summarize(rows[~rows.high])}


def paired_change(control, candidate):
    pd.testing.assert_series_equal(control["high"], candidate["high"])
    delta = np.log1p(candidate["net_return"]) - np.log1p(control["net_return"])
    grouped = {key: {"days": len(rows), "relative_growth": float(np.expm1(rows.sum()))}
               for key, rows in (("high", delta[control.high]), ("other", delta[~control.high]))}
    full = float(np.expm1(delta.sum()))
    assert isclose(np.prod([1 + row["relative_growth"] for row in grouped.values()]) - 1, full,
                   rel_tol=1e-12, abs_tol=1e-14)
    return {"relative_growth_on_full_utc_days": full, "contributions": grouped}


def main():
    annual = {2024: receipt(HERE / "annual_2024_fixed12_15bp_verified.json"),
              2025: receipt(HERE / "boost14_2025_15bp_frozen_basis.json")}
    archived = receipt(ONLINE / "comparison_15bp.json")
    archived_regime = receipt(ONLINE / "volatility_regime.json")
    frozen = write_once(HERE / "volatility_across_years_freeze.json", {
        "scope": "Fixed 15bp annual complete UTC days and completed January–November 2026 dates",
        "calendar": "Complete midnight to next-midnight cash equity intervals with both marks; annual terminal partial day is excluded",
        "as_of": "Same causal 7-day BTC variation and trailing 90-observation 80th percentile that exclude the current observation from the cutoff",
        "script": receipt(Path(__file__)), "owner": receipt(Path(owner.__file__)), "annual": annual,
        "previous_regime": archived_regime, "online_comparison": archived,
        "parameter_changes": False, "new_native_accounts": 0, "orders_enabled": False,
    })
    cohorts = {}
    for year, proof in annual.items():
        accounts = read(checked(proof))["accounts"]
        rows, results = {}, {}
        for name in ("fixed_boost_12", "boost_14"):
            if name not in accounts:
                continue
            plan, btc, sources = annual_plan(accounts[name], year)
            rows[name] = evaluate(plan, annual_prices(btc), sources["nav"])
            results[name] = {"source": sources, "btc": btc, **summaries(rows[name])}
        cohorts[str(year)] = {"accounts": results, "full_utc_days": len(rows["fixed_boost_12"]),
                              "dates": [str(d) for d in rows["fixed_boost_12"].index[[0, -1]]]}
        if "boost_14" in rows:
            cohorts[str(year)]["paired_growth"] = paired_change(rows["fixed_boost_12"], rows["boost_14"])

    plan = read(ONLINE / "plan.json")
    btc = read(checked(plan["historical_schedule"]))["daily_sources"]["BTC/USDT:USDT"]
    prices = owner.completed_btc_closes(read(checked(btc)))
    comparison = read(checked(archived))
    results = {}
    for role, key in (("fixed_boost_12", "control"), ("boost_14", "candidate")):
        case = comparison[key]
        assert case["five_minute_nav_reconciliation"]["passed"]
        nav = selected_receipt(case["sources"], "/five_minute_equity.parquet")
        results[role] = evaluate(plan, prices, nav)
    assert summaries(results["fixed_boost_12"]) == read(checked(archived_regime))["by_cost"]["15"]["periods"]["full"]
    rows_2026 = results["fixed_boost_12"]
    cohorts["2026"] = {"accounts": {role: summaries(rows) for role, rows in results.items()},
                       "full_utc_days": len(rows_2026), "dates": [str(d) for d in rows_2026.index[[0, -1]]],
                       "paired_growth": paired_change(rows_2026, results["boost_14"])}
    write_once(HERE / "volatility_across_years_15bp.json", {
        "freeze": frozen, "years": cohorts, "cost_per_side_bp": 15,
        "new_native_accounts": 0, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
