"""Apply the user's new 30% risk filter to completed, preserved native accounts."""

from collections import defaultdict
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from research.crypto.nfi80_flatboost_20261109.study import BASE, HERE, read, ref, save


def selected_comparison(accounts, selected):
    comparisons = []
    for cost in (15, 8):
        original = next(
            r
            for r in accounts
            if r["variant"] == "Original"
            and r["case"] == selected[1]
            and r["cost_bps_per_side"] == cost
        )
        optimized = next(
            r
            for r in accounts
            if r["variant"] == selected[0]
            and r["case"] == selected[1]
            and r["cost_bps_per_side"] == cost
        )
        sharpes = []
        for row in (original, optimized):
            curve = pd.read_parquet(row["observed_equity"]["path"])
            daily = curve.groupby(curve.date.dt.floor("D"), sort=True).marked_equity.last()
            values = daily.to_numpy(dtype=float)
            returns = values[1:] / values[:-1] - 1
            sharpe = float(returns.mean() / returns.std(ddof=1) * np.sqrt(365))
            if abs(sharpe - row["periods"][0]["daily_nav_sharpe"]) > 1e-10:
                raise ValueError("Independent daily Sharpe disagrees with reported metric")
            sharpes.append(sharpe)
        ratios = [
            r["net_return_pct"] / r["maximum_5m_mark_drawdown_pct"] for r in (original, optimized)
        ]
        comparisons.append(
            {
                "cost_bps_per_side": cost,
                "return_increase_pp": optimized["net_return_pct"] - original["net_return_pct"],
                "extra_net_profit_usdt_per_1000_initial": optimized["net_profit_usdt"]
                - original["net_profit_usdt"],
                "relative_net_profit_increase_pct": 100
                * (optimized["net_profit_usdt"] / original["net_profit_usdt"] - 1),
                "drawdown_increase_pp": optimized["maximum_5m_mark_drawdown_pct"]
                - original["maximum_5m_mark_drawdown_pct"],
                "original_daily_sharpe": sharpes[0],
                "optimized_daily_sharpe": sharpes[1],
                "original_simple_return_to_drawdown": ratios[0],
                "optimized_simple_return_to_drawdown": ratios[1],
                "relative_simple_ratio_change_pct": 100 * (ratios[1] / ratios[0] - 1),
                "halfyear_return_changes_pp": [
                    (b["net_return"] - a["net_return"]) * 100
                    for a, b in zip(original["periods"][1:], optimized["periods"][1:], strict=True)
                ],
            }
        )
    return comparisons


def main():
    original = read(BASE / "comparison.json")
    optimized = read(HERE / "comparison.json")
    accounts = [{**r, "variant": "Original"} for r in original["accounts"]]
    accounts.extend(r for r in optimized["accounts"] if r["variant"] == "FlatTopBoost")
    if len({(r["variant"], r["case"], r["cost_bps_per_side"]) for r in accounts}) != 12:
        raise ValueError("Expected twelve distinct completed native accounts")
    groups = defaultdict(list)
    for row in accounts:
        groups[(row["variant"], row["case"])].append(row)
    profiles = []
    for (variant, case), rows in groups.items():
        if {r["cost_bps_per_side"] for r in rows} != {8, 15} or len(rows) != 2:
            raise ValueError("Both frozen cost scenarios required per profile")
        worst_dd = max(r["maximum_5m_mark_drawdown_pct"] for r in rows)
        liquidations = sum(r["native_modeled_liquidations"] for r in rows)
        profiles.append(
            {
                "variant": variant,
                "case": case,
                "worst_5m_drawdown_pct_across_costs": worst_dd,
                "modeled_liquidations_across_costs": liquidations,
                "eligible_under_current_risk_preference": worst_dd <= 30 and liquidations == 0,
                "scenarios": [
                    {
                        "cost_bps_per_side": r["cost_bps_per_side"],
                        "net_return_pct": r["net_return_pct"],
                        "maximum_5m_mark_drawdown_pct": r["maximum_5m_mark_drawdown_pct"],
                        "actual_addition_fills": r["actual_addition_fills"],
                        "natural_completed_trades": r["natural_completed_trades"],
                    }
                    for r in rows
                ],
            }
        )
    rankings = {}
    winners = []
    for cost in (15, 8):
        eligible = [
            {"variant": p["variant"], "case": p["case"], **s}
            for p in profiles
            if p["eligible_under_current_risk_preference"]
            for s in p["scenarios"]
            if s["cost_bps_per_side"] == cost
        ]
        eligible.sort(key=lambda r: r["net_return_pct"], reverse=True)
        rankings[str(cost)] = eligible
        winners.append((eligible[0]["variant"], eligible[0]["case"]) if eligible else None)
    shared_winner = winners[0] if winners[0] == winners[1] else None
    result = {
        "evaluated_at_utc": datetime.now(UTC).isoformat(),
        "user_instruction": "Maximum historical drawdown 30%; reject approximately40% drawdown",
        "maximum_historical_drawdown_pct": 30,
        "risk_observation": "Complete 5m marked equity, open PnL and exit-fee reserve included",
        "source_comparisons": [ref(BASE / "comparison.json"), ref(HERE / "comparison.json")],
        "source": ref(__file__),
        "profiles": profiles,
        "eligible_return_rankings_by_cost": rankings,
        "highest_return_eligible_in_both_cost_scenarios": {
            "variant": shared_winner[0],
            "case": shared_winner[1],
        }
        if shared_winner
        else None,
        "selected_vs_original_same_slots": selected_comparison(accounts, shared_winner)
        if shared_winner
        else [],
        "additional_metric_definitions": {
            "simple_return_to_drawdown": (
                "Cumulative net return / maximum5m drawdown; not annualized Calmar"
            ),
            "daily_sharpe": (
                "UTC daily last NAV simple-return mean / sample std * sqrt365; "
                "zero riskfree assumption, last partial day retained in both accounts"
            ),
        },
        "policy_applied_after_observation": True,
        "original_50pct_registration_and_results_unchanged": True,
        "previous_failed_experiments_unchanged": True,
        "new_backtests": 0,
        "parameter_changes": 0,
        "deployment_changes": 0,
        "production_eligible": False,
        "orders_enabled": False,
        "qualifications": original["qualifications"],
        "historical_drawdown_is_not_a_live_loss_guarantee": True,
    }
    save(HERE / "selection-dd30.json", result)
    print(result["highest_return_eligible_in_both_cost_scenarios"])


if __name__ == "__main__":
    main()
