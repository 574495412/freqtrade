"""Audit actual bounded-NFI fills and combine complete independently funded native accounts."""

import argparse
import math
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_ensemble import closed_drawdown, fill_cash
from research.crypto.performance_scorecard import anchor_initial_wallet, marked_metrics
from research.crypto.run_candidate import native_stats


def risk_audit(trades, initial, maximum_entries):
    violations, events, cash_errors = [], [], []
    maximum_risk, maximum_margin, maximum_count = 0.0, 0.0, 0
    for index, trade in enumerate(trades):
        cash_errors.append(fill_cash(trade))
        first = trade["orders"][0]
        stop = first["safe_price"] * 0.88
        entries, exited, quantity, cost, first_stake = 0, False, 0.0, 0.0, None
        add_clocks = set()
        for order in trade["orders"]:
            amount, rate = order["amount"], order["safe_price"]
            if order["ft_is_entry"]:
                entries += 1
                stake = amount * rate
                if first_stake is None:
                    first_stake = stake
                else:
                    clock = order["order_filled_timestamp"] // 300000
                    if clock in add_clocks or stake > first_stake + 1e-5:
                        violations.append([index, "Repeated-bar or oversized addition"])
                    add_clocks.add(clock)
                if exited or entries > maximum_entries:
                    violations.append([index, "Lifetime entry count or post-reduction addition"])
                quantity += amount
                cost += stake
            else:
                exited = True
                fraction = min(1.0, amount / quantity) if quantity else 1.0
                cost *= 1 - fraction
                quantity = max(0, quantity - amount)
            risk = max(
                0, cost * (1 + trade["fee_open"]) - quantity * stop * (1 - trade["fee_close"])
            )
            maximum_risk, maximum_margin = max(maximum_risk, risk), max(maximum_margin, cost)
            if risk > initial * 0.02 + 1e-5 or cost > initial * 0.20 + 1e-5:
                violations.append([index, "Filled stop-price risk or margin exceeds budget"])
            events.append(
                (order["order_filled_timestamp"], not order["ft_is_entry"], index, risk, cost)
            )
        maximum_count = max(maximum_count, entries)
        if trade["stop_loss_abs"] < stop - 1e-7:
            violations.append([index, "Final stop is below the first-fill locked stop"])
        if trade["trade_duration"] > 14 * 24 * 60 + 5:
            violations.append([index, "Position exceeds the registered holding limit"])
    current, aggregate_risk, aggregate_margin = {}, 0.0, 0.0
    for _, _, key, risk, margin in sorted(events):
        current[key] = (risk, margin)
        aggregate_risk = max(aggregate_risk, math.fsum(x[0] for x in current.values()))
        aggregate_margin = max(aggregate_margin, math.fsum(x[1] for x in current.values()))
    return {
        "passed": not violations,
        "violations": violations,
        "native_campaigns": len(trades),
        "maximum_lifetime_entries": maximum_count,
        "maximum_filled_price_stop_risk_usdt": maximum_risk,
        "maximum_campaign_margin_usdt": maximum_margin,
        "conservative_same_clock_entries_first_aggregate_risk_usdt": aggregate_risk,
        "conservative_same_clock_entries_first_aggregate_margin_usdt": aggregate_margin,
        "aggregate_fill_price_bounds_confirmed": aggregate_risk <= initial * 0.08 + 1e-5
        and aggregate_margin <= initial * 0.80 + 1e-5,
        "maximum_native_cash_error_usdt": max(cash_errors, default=0),
        "known_funding_at_each_decision_independently_rebuilt": False,
        "scope": "Actual completed native fills and first-fill stop anchor; entry-first ordering "
        "bounds same-clock sequencing. Aggregate risk excludes event-level funding, not a full "
        "intrahour or partial-fill execution certificate.",
        "nfi_inventory_half_cost_gap_stress_loss_usdt": aggregate_margin * 0.5,
        "gap_stress_scope": "Hypothetical NFI-only loss at50percent remaining cost, before fees; "
        "not a joint marked-market replay, stop guarantee or independent profitability evidence",
    }


def portfolio(
    run, phase, candidate, cost, output, trend_run=None, trend_candidate="Donchian20HalfRisk"
):
    plan = read(run / "plan.json")
    nfi = run / phase / candidate / f"cost_{cost}bps"
    trend_run = trend_run or run.parent / "daily_donchian_halfrisk_run_v001"
    trend = trend_run / phase / trend_candidate / f"cost_{cost}bps"
    curves, trades, sources, components = [], [], [], []
    start = plan["validation_start"] if phase == "continuous" else plan[f"{phase}_start"]
    end = plan["holdout_end"] if phase == "continuous" else plan[f"{phase}_end"]
    begin, finish = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    for folder in [nfi, trend]:
        result = read(folder / "result.json")
        if result["start"] != start or result["end"] != end:
            raise ValueError("Native account periods differ")
        stats = native_stats(folder)
        frame = pd.read_parquet(folder / "hourly_equity.parquet").set_index("date").equity
        frame = anchor_initial_wallet(
            frame, begin, 1000, stats["starting_balance"], stats["trades"]
        )
        if curves and not curves[0].index.equals(frame.index):
            raise ValueError("Full native hourly clocks differ")
        curves.append(frame)
        trades.extend(stats["trades"])
        components.append(result)
        for file in [
            folder / "result.json",
            folder / "hourly_equity.parquet",
            next(folder.glob("*.zip")),
        ]:
            sources.append({"path": str(file), "sha256": sha(file)})
    curve = curves[0] + curves[1]
    metrics, daily = marked_metrics(curve, 2000, begin, finish)
    gross_profit = math.fsum(max(t["profit_abs"], 0) for t in trades)
    gross_loss = -math.fsum(min(t["profit_abs"], 0) for t in trades)
    net = math.fsum(t["profit_abs"] for t in trades)
    if abs(net - metrics["net_profit_usdt"]) > 1e-5:
        raise ValueError("Full native cash and portfolio NAV disagree")
    risk = risk_audit(native_stats(nfi)["trades"], 1000, 1 if candidate.endswith("NoDCA") else 3)
    dd = max(metrics["hourly_marked_mdd"], closed_drawdown(trades, 2000))
    screen = len(trades) >= 30 and net > 0 and gross_profit >= 1.1 * gross_loss and dd <= 0.15
    result = {
        "candidate": candidate
        + ("_Donchian" if trend_candidate == "Donchian20HalfRisk" else "_" + trend_candidate),
        "nfi_candidate": candidate,
        "trend_candidate": trend_candidate,
        "phase": phase,
        "extra_cost_bps_per_side": cost,
        "start": start,
        "end": end,
        "initial_equity_usdt": 2000,
        "native_account_count": 2,
        "marked_metrics": metrics,
        "closed_campaigns": len(trades),
        "whole_campaign_net_win_rate": sum(t["profit_abs"] > 0 for t in trades) / len(trades)
        if trades
        else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "risk_drawdown_for_screen": dd,
        "historical_economic_screen_passed": screen,
        "nfi_fill_risk_audit": risk,
        "components": components,
        "source_files": sources,
        "reviewer_source_sha256": sha(Path(__file__)),
        "capital_rebalanced": False,
        "trades_selected_or_rescaled": False,
        "independent_oos": False,
        "production_eligible": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    curve.rename_axis("date").rename("equity").to_frame().reset_index().to_parquet(
        output / "hourly_equity.parquet", index=False
    )
    daily.to_parquet(output / "daily_returns.parquet", index=False)
    save(output / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--phase", choices=["train", "continuous"], required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--cost", type=int, choices=[0, 3, 10], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trend-run", type=Path)
    parser.add_argument("--trend-candidate", default="Donchian20HalfRisk")
    args = parser.parse_args()
    r = portfolio(
        args.run.resolve(),
        args.phase,
        args.candidate,
        args.cost,
        args.output.resolve(),
        args.trend_run.resolve() if args.trend_run else None,
        args.trend_candidate,
    )
    print(
        {
            "candidate": r["candidate"],
            "net_return": r["marked_metrics"]["net_return"],
            "passed": r["historical_economic_screen_passed"],
            "fill_risk": r["nfi_fill_risk_audit"]["passed"],
        }
    )
