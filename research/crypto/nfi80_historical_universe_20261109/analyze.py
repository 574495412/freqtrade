# ruff: noqa: E501, C901
"""Reconcile native archives, selection clocks and complete marked curves."""

import json

import pandas as pd

from research.crypto.nfi80_capacity_20261109 import analyze as metrics
from research.crypto.nfi80_historical_universe_20261109.prepare import (
    HERE,
    OUT,
    PLAN,
    ROOT,
    read,
    ref,
    save,
    sha,
)
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, size_audit


def dynamic(name, cost):
    plan = read(HERE / (name + "-plan.json"))
    folder = OUT / f"{name}_{cost}bps"
    if read(folder / "registered_plan.json") != plan:
        raise ValueError("Actual plan differs from frozen registration")
    candidate = plan["candidates"][0]
    for filename, record in candidate["source_files"].items():
        if sha(folder / "strategies" / filename) != record["sha256"]:
            raise ValueError("Actual strategy bytes differ")
    trades, cash = native_trades(plan, folder, candidate["strategy"], cost)
    row = metrics.account(name, cost)
    schedule = read(OUT / "schedule.json")["days"]
    observed = read(folder / "actual_historical_pairlists.json")
    if set(observed) != set(schedule) or any(
        observed[d]["pairs"] != schedule[d]["pairs"] for d in schedule
    ):
        raise ValueError("Actual native daily pool trace differs")
    outside_initial = []
    checks = read(folder / "initial_entry_checks.json")
    accepted = [r for r in checks if r["accepted"]]
    if any(not r["inside_current_pool"] for r in accepted):
        raise ValueError("Native initial order confirmed outside historical pool")
    accepted_keys = {(r["pair"], int(pd.Timestamp(r["time"]).timestamp() * 1000)) for r in accepted}
    held_outside = exits_outside = 0
    for trade in trades:
        pair = trade["pair"]
        if (pair, int(trade["open_timestamp"])) not in accepted_keys:
            raise ValueError("Initial trade lacks native accepted-order trace")
        first = True
        for order in trade["orders"]:
            day = (
                pd.to_datetime(int(order["order_filled_timestamp"]), unit="ms", utc=True)
                .date()
                .isoformat()
            )
            inside = pair in schedule[day]["pairs"]
            if order["ft_is_entry"]:
                if first and not inside:
                    outside_initial.append({"pair": pair, "filled": str(day)})
                elif not first and not inside:
                    held_outside += 1
                first = False
            elif not inside:
                exits_outside += 1
    row.update(
        variant=name,
        universe="daily",
        independent_fill_cash=cash,
        selection_trace=ref(folder / "actual_historical_pairlists.json"),
        new_initial_orders_outside_current_pool=0,
        prior_accepted_initial_fills_outside_pool=len(outside_initial),
        initial_order_trace=ref(folder / "initial_entry_checks.json"),
        held_addition_fills_outside_current_pool=held_outside,
        exit_fills_outside_current_pool=exits_outside,
        feasibility_passed=row["net_return_pct"] > 0 and row["research_risk_gate_passed"],
    )
    if name == "flatboost":
        row["initial_stake_audit"] = size_audit(plan, folder, candidate["strategy"], trades)
    return row


def fixed(variant, cost):
    base = (
        ROOT
        / "research/crypto"
        / ("nfi80_flatboost_20261109" if variant == "flatboost" else "nfi80_capacity_20261109")
    )
    source = read(base / "comparison.json")
    row = next(
        r for r in source["accounts"] if r["case"] == "L3_S12" and r["cost_bps_per_side"] == cost
    )
    for key in ("native_archive", "native_result", "observed_equity", "cash_audit", "completion"):
        if sha(row[key]["path"]) != row[key]["sha256"]:
            raise ValueError("Known native baseline evidence differs")
    row = {
        **row,
        "variant": variant,
        "universe": "fixed",
        "research_risk_gate_passed": row["maximum_5m_mark_drawdown_pct"] <= 30
        and row["native_modeled_liquidations"] == 0,
    }
    row["feasibility_passed"] = row["net_return_pct"] > 0 and row["research_risk_gate_passed"]
    return row


def main():
    if read(HERE / "input-protocol.json")["research_drawdown_limit"] != 0.30:
        raise ValueError("Current analysis must use the frozen 30% drawdown gate")
    metrics.OUT = OUT
    metrics.PROTOCOL = HERE / "input-protocol.json"
    metrics.CASES = {"original": (3, 12), "flatboost": (3, 12)}
    rows = []
    changes = []
    for cost in (15, 8):
        for name in ("original", "flatboost"):
            baseline = fixed(name, cost)
            current = dynamic(name, cost)
            rows.extend([baseline, current])
            for field in (
                "start",
                "end",
                "comparison_end",
                "initial_equity_usdt",
                "allowed_leverage",
                "total_cost_bps_per_side",
                "environment",
                "storage_source",
                "observer_source",
            ):
                if read(HERE / (name + "-plan.json"))[field] != PLAN[field]:
                    raise ValueError("Dynamic and static account contracts differ: " + field)
            changes.append(
                {
                    "variant": name,
                    "cost_bps_per_side": cost,
                    "net_return_delta_pp": current["net_return_pct"] - baseline["net_return_pct"],
                    "drawdown_delta_pp": current["maximum_5m_mark_drawdown_pct"]
                    - baseline["maximum_5m_mark_drawdown_pct"],
                    "higher_return": current["net_return_pct"] > baseline["net_return_pct"],
                    "passes_30pct_drawdown": current["research_risk_gate_passed"],
                }
            )
    verdicts = {}
    for name in ("original", "flatboost"):
        scoped = [r for r in rows if r["variant"] == name and r["universe"] == "daily"]
        comparison = [r for r in changes if r["variant"] == name]
        verdicts[name] = {
            "feasible_under_frozen_gate": all(r["feasibility_passed"] for r in scoped),
            "daily_refresh_improved_at_both_costs": all(
                r["higher_return"] and r["passes_30pct_drawdown"] for r in comparison
            ),
        }
    result = {
        "accounts": rows,
        "changes": changes,
        "verdicts": verdicts,
        "window": {k: PLAN[k] for k in ("start", "comparison_end")},
        "schedule_summary": read(HERE / "schedule-summary.json"),
        "risk_gate": 0.30,
        "returns_are_cumulative_not_annualized": True,
        "local_historical_extension": True,
        "protocol": ref(HERE / "PROTOCOL.md"),
        "limitation": "Retrospective author membership, blacklist and metadata remain; no independent OOS/PIT proof. Daily UTC differs from rolling-ticker forward clock.",
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(HERE / "comparison.json", result)
    pd.DataFrame(
        [
            {
                k: r[k]
                for k in (
                    "variant",
                    "universe",
                    "cost_bps_per_side",
                    "net_return_pct",
                    "maximum_5m_mark_drawdown_pct",
                    "natural_completed_trades",
                    "actual_addition_fills",
                    "native_modeled_liquidations",
                    "research_risk_gate_passed",
                )
            }
            for r in rows
        ]
    ).to_csv(HERE / "comparison.csv", index=False)
    lines = [
        "# Historical daily universe — native backtest validation",
        "",
        "2026-01-01 through 2026-11-09 05:00 UTC; 1,000 virtual USDT, 3x isolated, 12 positions. Cumulative net returns, complete 5-minute marked equity including open PnL, close cost and native funding.",
        "",
        "| Strategy | Universe | Cost bps/side | Net return | Max drawdown | Natural closes | Add fills | 30% gate |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['variant']} | {r['universe']} | {r['cost_bps_per_side']} | {r['net_return_pct']:.2f}% | {r['maximum_5m_mark_drawdown_pct']:.2f}% | {r['natural_completed_trades']} | {r['actual_addition_fills']} | {'PASS' if r['research_risk_gate_passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            json.dumps(verdicts, indent=2),
            "",
            result["limitation"],
            "",
            "The native engine has an explicit local historical PairList/clock extension. Its fills, wallet, funding, DCA and exits remain the primary evidence. No orders or new deployment. All failed and prior experiments retained.",
        ]
    )
    (HERE / "REPORT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"verdicts": verdicts, "changes": changes}, indent=2))


if __name__ == "__main__":
    main()
