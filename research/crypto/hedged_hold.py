"""Continuous, funded two-leg economic accounts; no forecasts and no real orders."""

import argparse
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha


def load_factor(plan):
    source = plan["cashflow_source"]
    path = Path(source["path"])
    if sha(path) != source["sha256"]:
        raise ValueError("Pinned TradingAgents factor changed")
    spec = importlib.util.spec_from_file_location("pinned_hedged_factor", path)
    factor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(factor)
    if (
        factor.SPOT_FEE != plan["spot_fee_per_side"]
        or factor.PERP_FEE != plan["perpetual_fee_per_side"]
    ):
        raise ValueError("Factor fee convention differs from protocol")
    return factor


def funding_path(funding, prices, clock, entry, final, q):
    selected = funding.loc[
        funding.calc_time.gt(entry.value // 1_000_000)
        & funding.calc_time.le(final.value // 1_000_000)
    ].copy()
    selected["event_time"] = pd.to_datetime(selected.calc_time, unit="ms", utc=True)
    source = prices.set_index("date").mark_open
    selected["mark_proxy"] = source.reindex(
        pd.DatetimeIndex(selected.event_time.dt.floor("h"))
    ).to_numpy()
    if not np.isfinite(selected[["funding_rate", "mark_proxy"]].to_numpy()).all():
        raise ValueError("Funding event lacks a finite source price")
    selected["cash"] = q * selected.mark_proxy * selected.funding_rate
    flow = np.zeros(len(clock))
    indices = np.searchsorted(clock.asi8, pd.DatetimeIndex(selected.event_time).asi8, side="left")
    np.add.at(flow, indices, selected.cash.to_numpy())
    return selected, np.cumsum(flow)


def monthly_observations(equity, start, end, capital):
    months = equity.loc[equity.date.lt(end)].set_index("date").equity.resample("ME").last()
    returns = months / np.r_[capital, months.to_numpy()[:-1]] - 1
    full = []
    for date in months.index:
        first = pd.Timestamp(year=date.year, month=date.month, day=1, tz="UTC")
        full.append(first >= start and first + pd.offsets.MonthBegin(1) <= end)
    return returns.loc[full]


def account(plan, panels, funding, factor, start, end, capital, slip_bps, output):
    output.mkdir(parents=True, exist_ok=False)
    start, end = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    clock = pd.date_range(start, end, freq="h", inclusive="left")
    entry, scheduled_exit = clock[1], clock[-1]
    slip = slip_bps / 10000
    details, paths, all_events = [], [], []
    feasible = True
    for symbol, weight in zip(plan["symbols"], plan["asset_weights"], strict=True):
        price = panels[symbol].set_index("date").reindex(clock)
        if price.isna().any().any():
            raise ValueError("Incomplete price calendar")
        budget = capital * weight
        prior = price.iloc[0]
        fill = price.iloc[1]
        q = (
            budget
            * plan["asset_spot_budget_fraction"]
            * (1 - factor.SPOT_FEE)
            / (prior.spot_close * (1 + slip))
        )
        gross = q / (1 - factor.SPOT_FEE)
        spot_entry = fill.spot_open * (1 + slip)
        perp_entry = fill.perpetual_open * (1 - slip)
        spot_cost, entry_fee = gross * spot_entry, q * perp_entry * factor.PERP_FEE
        collateral = budget * plan["asset_isolated_margin_fraction"]
        feasible &= spot_cost + collateral + entry_fee <= budget
        events, cumulative = funding_path(
            funding[symbol], panels[symbol], clock, entry, scheduled_exit, q
        )
        events["symbol"] = symbol
        pnl = q * (perp_entry - price.mark_open.to_numpy())
        nav = budget - spot_cost - entry_fee + cumulative + q * price.spot_open.to_numpy() + pnl
        nav[0] = budget
        margin = (collateral - entry_fee + cumulative + pnl) / (q * price.mark_open.to_numpy())
        margin[0] = np.inf
        participation = max(
            q * fill.spot_open / prior.spot_quote_volume,
            q * fill.perpetual_open / (prior.perpetual_low * prior.perpetual_volume),
        )
        details.append(
            {
                "symbol": symbol,
                "q": q,
                "gross_spot_quantity": gross,
                "budget": budget,
                "collateral": collateral,
                "entry_time": entry.isoformat(),
                "entry_spot_open": fill.spot_open,
                "entry_perpetual_open": fill.perpetual_open,
                "spot_entry_cost": spot_cost,
                "perpetual_entry_fee": entry_fee,
                "prior_spot_close": prior.spot_close,
                "entry_participation_proxy": participation,
            }
        )
        paths.append({"nav": nav, "margin": margin, "price": price})
        all_events.append(events)
    total = np.sum([p["nav"] for p in paths], axis=0)
    drawdown = 1 - total / np.maximum.accumulate(np.r_[capital, total])[1:]
    margin_hits = np.any([p["margin"] <= 0.20 for p in paths], axis=0)
    hits = np.flatnonzero(margin_hits | (drawdown >= 0.05))
    trigger = int(hits[0]) if len(hits) else None
    exit_index = min(trigger + 1, len(clock) - 1) if trigger is not None else len(clock) - 1
    exit_time = clock[exit_index]
    trades, held_events = [], []
    if not feasible:
        equity = pd.DataFrame(
            {"date": clock.append(pd.DatetimeIndex([end])), "equity": capital, "drawdown": 0.0}
        )
        result = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "capital_usdt": capital,
            "slippage_bps_per_fill": slip_bps,
            "net_profit_usdt": 0.0,
            "net_return": 0.0,
            "hourly_observed_drawdown": 0.0,
            "positive_full_month_fraction": 0.0,
            "full_months": len(monthly_observations(equity, start, end, capital)),
            "funding_events": 0,
            "complete_hedged_round_trips": 0,
            "funding_net_usdt": 0.0,
            "price_pnl_usdt": 0.0,
            "cost_usdt": 0.0,
            "entry_status": "CASH_CAPITAL_INFEASIBLE",
            "checks": {"positive_net": False},
            "historical_economic_screen_passed": False,
            "production_eligible": False,
            "orders_enabled": False,
        }
        save(output / "result.json", result)
        save(output / "trades.json", [])
        equity.to_parquet(output / "hourly_equity.parquet", index=False)
        return result
    for detail, path, events in zip(details, paths, all_events, strict=True):
        held = events.loc[events.event_time.le(exit_time)].copy()
        selected = [
            {
                "time_ms": int(row.calc_time),
                "rate": row.funding_rate,
                "mark": row.mark_proxy,
                "type": "Regular",
            }
            for row in held.itertuples()
        ]
        fill = path["price"].iloc[exit_index]
        cash = factor.cashflows(
            detail["q"],
            detail["entry_spot_open"],
            fill.spot_open,
            detail["entry_perpetual_open"],
            fill.perpetual_open,
            selected,
            entry.value // 1_000_000,
            exit_time.value // 1_000_000,
            slip_bps,
        )
        trades.append(
            {
                **detail,
                "exit_time": exit_time.isoformat(),
                "exit_spot_open": fill.spot_open,
                "exit_perpetual_open": fill.perpetual_open,
                **cash,
            }
        )
        held_events.append(held)
    net = math.fsum(t["net_pnl"] for t in trades)
    total[exit_index:] = capital + net
    equity = pd.DataFrame({"date": clock, "equity": total})
    equity.loc[len(equity)] = [end, capital + net]
    equity["drawdown"] = (
        1 - equity.equity / np.maximum.accumulate(np.r_[capital, equity.equity])[1:]
    )
    monthly_returns = monthly_observations(equity, start, end, capital)
    target = plan["screening_targets"]
    event_count = sum(len(e) for e in held_events)
    positive_months = float(monthly_returns.gt(0).mean()) if len(monthly_returns) else 0.0
    checks = {
        "positive_net": net > 0,
        "observed_drawdown": float(equity.drawdown.max())
        <= target["maximum_hourly_observed_drawdown"],
        "positive_full_month_fraction": positive_months
        >= target["minimum_positive_full_month_fraction"],
        "funding_event_coverage": event_count >= target["minimum_funding_events"],
        "full_calendar_month_coverage": len(monthly_returns)
        >= target["minimum_full_calendar_months"],
        "no_margin_guard": not bool(margin_hits[: exit_index + 1].any()),
        "entry_volume_proxy": max(t["entry_participation_proxy"] for t in trades)
        <= target["maximum_entry_notional_to_previous_hour_quote_volume"],
    }
    checks = {key: bool(value) for key, value in checks.items()}
    result = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "capital_usdt": capital,
        "slippage_bps_per_fill": slip_bps,
        "net_profit_usdt": net,
        "net_return": net / capital,
        "hourly_observed_drawdown": float(equity.drawdown.max()),
        "positive_full_month_fraction": positive_months,
        "full_months": len(monthly_returns),
        "funding_events": event_count,
        "complete_hedged_round_trips": len(trades),
        "funding_net_usdt": math.fsum(t["funding_cash"] for t in trades),
        "price_pnl_usdt": math.fsum(t["price_pnl"] for t in trades),
        "cost_usdt": math.fsum(t["slippage_cost"] + t["spot_fee"] + t["perp_fee"] for t in trades),
        "margin_or_drawdown_trigger_time": clock[trigger].isoformat()
        if trigger is not None
        else None,
        "checks": checks,
        "historical_economic_screen_passed": all(checks.values()),
        "quantity_precision": "continuous initial economic screen, not exchange-lot execution",
        "independent_oos": False,
        "production_eligible": False,
        "orders_enabled": False,
    }
    save(output / "result.json", result)
    save(output / "trades.json", trades)
    equity.to_parquet(output / "hourly_equity.parquet", index=False)
    monthly_returns.rename("return").to_frame().to_csv(output / "monthly_returns.csv")
    pd.concat(held_events, ignore_index=True).to_parquet(
        output / "funding_cash.parquet", index=False
    )
    return result


def run(plan_path, output, continue_future=False):  # noqa: C901
    plan = read(plan_path)
    data = Path(plan["data"])
    if sha(data / "receipts.json") != plan["data_receipts_sha256"]:
        raise ValueError("Carry input receipt identity changed")
    for name, identity in read(data / "receipts.json").items():
        if sha(data / name) != identity:
            raise ValueError("Carry input changed")
    if continue_future:
        frozen = read(output / "freeze.json")
        selection = read(output / "selection.json")
        if (
            read(output / "plan.json") != plan
            or frozen["plan_sha256"] != sha(plan_path)
            or frozen["runner_sha256"] != sha(Path(__file__))
        ):
            raise ValueError("Frozen carry protocol or runner changed")
        if (
            selection["future_results_included"] is not False
            or selection["agent_review_status"] != "COMPLETED"
        ):
            raise ValueError("Carry review did not precede future accounts")
        if (output / "evaluation_attempt.json").exists():
            raise ValueError("Preserve the existing carry evaluation attempt")
        if not read(output / "evaluation.json")["all_executed_screens_passed"]:
            raise ValueError("Carry training failed")
        save(
            output / "evaluation_attempt.json",
            {"status": "STARTED", "selection_sha256": sha(output / "selection.json")},
        )
    else:
        output.mkdir(parents=True, exist_ok=False)
        save(output / "plan.json", plan)
        save(
            output / "freeze.json",
            {
                "plan_sha256": sha(plan_path),
                "runner_sha256": sha(Path(__file__)),
                "orders_enabled": False,
            },
        )
    factor = load_factor(plan)
    panels = {s: pd.read_parquet(data / f"{s}_prices.parquet") for s in plan["symbols"]}
    funding = {s: pd.read_parquet(data / f"{s}_funding.parquet") for s in plan["symbols"]}
    rows = read(output / "train.json") if continue_future else []
    phases = ("validation", "holdout", "continuous") if continue_future else ("train",)
    for phase in phases:
        for capital in plan["capital_scenarios_usdt"]:
            for slip in plan["slippage_bps_per_fill"]:
                folder = output / phase / f"capital_{capital}" / f"slippage_{slip}bps"
                result = account(plan, panels, funding, factor, *plan[phase], capital, slip, folder)
                rows.append(dict(result, phase=phase))
                print(
                    {
                        "phase": phase,
                        "capital": capital,
                        "slip": slip,
                        "return": result["net_return"],
                        "passed": result["historical_economic_screen_passed"],
                    },
                    flush=True,
                )
        save(output / (phase + ".json"), [r for r in rows if r["phase"] == phase])
        if phase == "train" and not all(r["historical_economic_screen_passed"] for r in rows):
            break
    summary = {
        "accounts": rows,
        "all_registered_phases_evaluated": {r["phase"] for r in rows}
        == {"train", "validation", "holdout", "continuous"},
        "all_executed_screens_passed": all(r["historical_economic_screen_passed"] for r in rows),
        "production_eligible": False,
        "orders_enabled": False,
    }
    save(output / "evaluation.json", summary)
    if continue_future:
        save(
            output / "evaluation_attempt.json",
            {"status": "COMPLETED", "selection_sha256": sha(output / "selection.json")},
        )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--continue-future", action="store_true")
    args = parser.parse_args()
    run(args.plan.absolute(), args.output.absolute(), args.continue_future)
