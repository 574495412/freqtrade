"""Independent native orders/NAV, frozen actual500 combinations and gate results."""

# ruff: noqa: S101
# Assertions are audit invariants; optimized Python is rejected below.

import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = HERE.with_name("trendcore_source_native_20261117_v1")
if not __debug__:
    raise RuntimeError("Account audit requires assertions")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def main():
    plan = read(HERE / "plan_v2.json")
    assert read(HERE / "execution_complete_v2.json")["accounts"] == 4
    for path, digest in read(HERE / "implementation_freeze_v2.json").items():
        assert sha(path) == digest, path
    audit = load("unchanged_independent_native_order_audit", OLD / "audit_accounts.py")
    budget = load("same_contract_quantity_audit", HERE / "budget_audit.py")
    oracle = load(
        "independent_cash_oracle",
        ROOT.parent / "TradingAgents/research_tools/nfi_reproduction/audit_original.py",
    )
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    navs, accounts, budgets, source_curves = {}, [], [], []
    for candidate in plan["candidates"]:
        nav, row = audit.audit(candidate, plan, grid, oracle)
        navs[candidate["name"]] = nav
        accounts.append(row)
        budgets.append(budget.budget_audit(candidate, plan, nav, audit))
        print("AUDITED", candidate["name"], row["periods"][0]["net_return"], flush=True)
    # Re-read sealed controls and verify actual native archives/curves, not metrics only.
    for parent, roles in [
        (OLD, ["trendcore"]),
        (HERE.with_name("nfi_carry_matched_native_20261115_v1"), ["nfi_spot"]),
    ]:
        pp = read(parent / "plan.json")
        existing = read(parent / "results.json")["accounts"]
        for candidate in pp["candidates"]:
            if not any(candidate["name"].startswith(role + "_") for role in roles):
                continue
            receipt = next(r for r in existing if r["account"] == candidate["name"])
            directory = Path(pp["output_root"]) / (
                candidate["name"] + f"_{candidate['cost_bps']}bps"
            )
            _, archive = audit.native_stats(directory, candidate["strategy"])
            assert sha(archive) == receipt["archive_sha256"]
            process = read(directory / "process_receipt.json")
            assert process["returncode"] == 0 and process["result_sha256"] == sha(
                directory / "result.json"
            )
            path = directory / "hourly_equity.parquet"
            nav = pd.read_parquet(path).set_index("date").hourly_mark_equity
            assert nav.index.as_unit("ns").equals(grid)
            assert nav.iloc[0] == candidate["capital"]
            p = audit.periods(nav, plan)
            for now, sealed in zip(p, receipt["periods"], strict=True):
                for key in ["net_return", "hourly_mark_drawdown", "daily_nav_sharpe"]:
                    if now[key] is None or sealed[key] is None:
                        assert now[key] is sealed[key]
                    else:
                        assert abs(now[key] - sealed[key]) < 1e-12
            navs[candidate["name"]] = nav
            accounts.append(
                {
                    "account": candidate["name"],
                    "capital": candidate["capital"],
                    "scenario": candidate["scenario"],
                    "periods": p,
                    "reused": True,
                }
            )
            source_curves.append(
                {"path": str(path), "sha256": sha(path), "archive_sha256": sha(archive)}
            )
    gates, portfolios = [], []
    for scenario in ["conservative", "lower"]:
        current = next(r for r in accounts if r["account"] == f"entropytrend_1000_{scenario}")
        reference = next(r for r in accounts if r["account"] == f"trendcore_1000_{scenario}")
        p, ref = current["periods"], reference["periods"][0]
        flags = {
            "positive_all_periods": all(x["net_return"] > 0 for x in p),
            "higher_net_than_matched_trend": p[0]["net_return"] > ref["net_return"],
            "drawdown_at_most15pct": p[0]["hourly_mark_drawdown"] <= 0.15,
            "drawdown_no_worse_than_trend": p[0]["hourly_mark_drawdown"]
            <= ref["hourly_mark_drawdown"],
            "daily_sharpe_at_least_half": p[0]["daily_nav_sharpe"] is not None
            and p[0]["daily_nav_sharpe"] >= 0.5,
        }
        gates.append(dict(account=current["account"], **flags, financial_gate=all(flags.values())))
        baseline = navs[f"trendcore_500_{scenario}"] + navs[f"nfi_spot_500_{scenario}"]
        baseline_metrics = audit.periods(baseline, plan)
        for other in ["nfi_spot", "trendcore"]:
            names = [f"entropytrend_500_{scenario}", f"{other}_500_{scenario}"]
            nav = navs[names[0]] + navs[names[1]]
            assert nav.iloc[0] == 1000 and nav.index.as_unit("ns").equals(grid)
            p = audit.periods(nav, plan)
            name = f"entropytrend_{other}_1000_{scenario}"
            curve = HERE / (name + ".parquet")
            pd.DataFrame({"date": nav.index, "hourly_mark_equity": nav.to_numpy()}).to_parquet(
                curve, index=False
            )
            comparison = {
                "higher_net_than_trend_nfi": p[0]["net_return"] > baseline_metrics[0]["net_return"],
                "drawdown_no_worse_than_trend_nfi": p[0]["hourly_mark_drawdown"]
                <= baseline_metrics[0]["hourly_mark_drawdown"],
            }
            portfolios.append(
                {
                    "name": name,
                    "actual_sleeves": names,
                    "periods": p,
                    "curve": {"path": str(curve), "sha256": sha(curve)},
                    "comparison_to_sealed_trend_nfi": comparison,
                    "standalone_combo_gate": all(comparison.values())
                    if other == "nfi_spot"
                    else None,
                    "capital_comparison_only": True,
                    "daily_return_correlation": float(
                        navs[names[0]]
                        .reindex(grid[::24])
                        .pct_change()
                        .corr(navs[names[1]].reindex(grid[::24]).pct_change())
                    ),
                }
            )
    fresh = [r for r in accounts if not r.get("reused")]
    financial = all(g["financial_gate"] for g in gates) and all(
        p["standalone_combo_gate"] for p in portfolios if "nfi_spot" in p["name"]
    )
    result = {
        "new_native_wallets": len(fresh),
        "new_native_trades": sum(r["actual_native_trades"] for r in fresh),
        "new_simulated_fills": sum(r["filled_orders"] for r in fresh),
        "new_hour_points": sum(r["hour_points"] for r in fresh),
        "maximum_independent_nav_error_usdt": max(r["maximum_nav_error_usdt"] for r in fresh),
        "accounts": accounts,
        "gates": gates,
        "portfolios": portfolios,
        "reused_curves": source_curves,
        "financial_gate_passed": financial,
        "source_is_indicator_only": True,
        "independent_oos": False,
        "historical_venue_rules_certified": False,
        "production_eligible": False,
        "orders_enabled": False,
        "goal_complete": False,
    }
    save("budget_audit_v2.json", {"passed": True, "accounts": budgets})
    save("results_v2.json", result)
    print(
        json.dumps({"new_wallets": len(fresh), "gates": gates, "financial_gate_passed": financial})
    )


if __name__ == "__main__":
    main()
