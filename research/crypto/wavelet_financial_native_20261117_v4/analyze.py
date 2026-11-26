"""Independent exported-order/cash/NAV audit and frozen matched-wallet comparisons.

No strategy, prediction calculator, or trading engine is imported here.
"""

# ruff: noqa: S101
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frozen_inputs():
    freeze = read(HERE / "analysis_freeze.json")
    for path, digest in freeze["files"].items():
        assert sha(path) == digest, path
    return freeze


def load_controls(contract, grid, audit, plan):
    accounts, navs = [], {}
    for control in contract["controls"]:
        for key in ["hourly_equity", "result", "source_manifest", "source_plan"]:
            if key in control:
                assert sha(control[key]["path"]) == control[key]["sha256"]
        frame = pd.read_parquet(control["hourly_equity"]["path"])
        nav = frame.set_index("date").hourly_mark_equity
        assert nav.index.as_unit("ns").equals(grid)
        assert abs(nav.iloc[0] - control["capital"]) < 1e-10
        navs[control["name"]] = nav
        accounts.append(
            {
                "account": control["name"],
                "capital": control["capital"],
                "cost_bps": control["cost_bps"],
                "reused": True,
                "evidence_type": "ANALYTICAL_BENCHMARK_REFERENCE"
                if "result" not in control
                else "PRIOR_NATIVE_ACCOUNT",
                "periods": audit.periods(nav, plan),
                "source_curve": control["hourly_equity"],
            }
        )
    return navs, accounts


def economic_gate(periods, controls):
    full = periods[0]
    return {
        "all_complete_periods_positive": all(p["net_return"] > 0 for p in periods),
        "hourly_drawdown_lte15pct": full["hourly_mark_drawdown"] <= 0.15,
        "daily_nav_sharpe_gt0_5": full["daily_nav_sharpe"] is not None
        and full["daily_nav_sharpe"] > 0.5,
        "return_beats_all_frozen_matched1000controls": all(
            full["net_return"] > r["periods"][0]["net_return"] for r in controls
        ),
    }


def source_cache_audit(plan):
    first = plan["candidates"][0]
    config = read(first["config"]["path"])
    cache = Path(config["research_feature_cache"])
    rows = []
    for asset in ["BTC", "ETH"]:
        receipt = read(cache / (asset + "_USDT.json"))
        assert sha(cache / (asset + "_USDT.parquet")) == receipt["output_sha256"]
        n = receipt["identity"]["source_rows"]
        fits = [e for e in receipt["model_events"] if e["stage"] == "source_model_fit_complete"]
        predictions = [
            e for e in receipt["model_events"] if e["stage"] == "source_model_predict_complete"
        ]
        starts = list(range(1000, n, 2500))
        assert len(fits) == len(predictions) == len(starts)
        for start, fit, pred in zip(starts, fits, predictions, strict=True):
            assert (
                fit["rows"] == start - 6 and fit["columns"] == 101 and fit["estimator_count"] == 101
            )
            assert pd.Timestamp(fit["label_last"]) < pd.Timestamp(fit["first_prediction"])
            assert fit["booster_parameters"]["num_iterations"] == 100
            assert fit["booster_parameters"]["random_state"] == 42
            assert pred["rows"] == min(2500, n - start)
        rows.append(
            {
                "asset": asset,
                "rows": n,
                "training_windows": len(fits),
                "latest_label_before_first_prediction_each_window": True,
                "feature_sha256": receipt["output_sha256"],
                "scope": "Receipts and chronology,not independent full matrix reconstruction",
            }
        )
    return rows


def main():
    frozen_inputs()
    assert read(HERE / "execution_complete_attempt2.json")["native_accounts_completed"] == 4
    plan = read(HERE / "plan_attempt2.json")
    contract = read(HERE / "comparison_contract.json")
    helper = HERE.with_name("trendcore_source_native_20261117_v1") / "audit_accounts.py"
    oracle_path = ROOT.parent / "TradingAgents/research_tools/nfi_reproduction/audit_original.py"
    audit = load("_wavelet_exported_cash_audit", helper)
    oracle = load("_wavelet_independent_cash_oracle", oracle_path)
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    navs, accounts = load_controls(contract, grid, audit, plan)
    native = []
    for candidate in plan["candidates"]:
        nav, row = audit.audit(candidate, plan, grid, oracle)
        row.update(cost_bps=candidate["cost_bps"], reused=False, evidence_type="NEW_NATIVE_ACCOUNT")
        navs[candidate["name"]] = nav
        native.append(row)
        print("INDEPENDENT_NATIVE_AUDIT_PASS", candidate["name"], flush=True)
    cache = source_cache_audit(plan)
    gates, portfolios, correlations = [], [], []
    for scenario, cost in [("conservative", 20), ("lower", 13)]:
        controls = [r for r in accounts if r["capital"] == 1000 and r["cost_bps"] == cost]
        assert len(controls) == 4
        best = max(controls, key=lambda r: r["periods"][0]["net_return"])
        standalone = next(r for r in native if r["account"] == f"wavelet_1000_{scenario}")
        criteria = economic_gate(standalone["periods"], controls)
        gates.append(
            {
                "account": standalone["account"],
                "criteria": criteria,
                "financial_gate_passed": all(criteria.values()),
            }
        )
        for other in ["nfi_spot", "trendcore"]:
            names = [f"wavelet_500_{scenario}", f"{other}_500_{scenario}"]
            nav = navs[names[0]] + navs[names[1]]
            assert abs(nav.iloc[0] - 1000) < 1e-10
            periods = audit.periods(nav, plan)
            criteria = economic_gate(periods, controls)
            criteria["no_worse_drawdown_than_best_matched1000control"] = (
                periods[0]["hourly_mark_drawdown"] <= best["periods"][0]["hourly_mark_drawdown"]
            )
            path = HERE / ("_".join(names) + ".parquet")
            pd.DataFrame({"date": grid, "hourly_mark_equity": nav.to_numpy()}).to_parquet(
                path, index=False
            )
            portfolios.append(
                {
                    "name": "+".join(names),
                    "legs": names,
                    "capital": 1000,
                    "cost_bps": cost,
                    "wallet_structure": (
                        "Two actual native500USDT wallets,no transfers or rebalancing"
                    ),
                    "periods": periods,
                    "best_matched1000_control": best["account"],
                    "criteria": criteria,
                    "financial_gate_passed": all(criteria.values()),
                    "curve": {"path": str(path), "sha256": sha(path)},
                }
            )
            daygrid = pd.date_range(grid[0], grid[-1], freq="24h")
            a = navs[f"wavelet_1000_{scenario}"].reindex(daygrid).pct_change().dropna()
            b = navs[f"{other}_1000_{scenario}"].reindex(daygrid).pct_change().dropna()
            correlation = a.corr(b)
            correlations.append(
                {
                    "cost_bps": cost,
                    "other": other,
                    "complete24h_samples": len(a),
                    "daily_nav_return_correlation": float(correlation)
                    if np.isfinite(correlation)
                    else None,
                    "statistical_independence_proven": False,
                }
            )
    standalone_pass = all(r["financial_gate_passed"] for r in gates)
    combos_pass = any(
        all(p["financial_gate_passed"] for p in portfolios if p["legs"][1].startswith(other))
        for other in ["nfi_spot", "trendcore"]
    )
    save(
        HERE / "results_attempt2.json",
        {
            "accounts": native + accounts,
            "portfolios": portfolios,
            "standalone_gates": gates,
            "correlations": correlations,
            "source_cache_receipt_audit": cache,
            "both_cost_standalone_gate_passed": standalone_pass,
            "at_least_one_fixed_combo_passed_both_costs": combos_pass,
            "financial_promotion_gate_passed": standalone_pass and combos_pass,
            "new_independently_validated_alpha": 0,
            "independent_oos_proven": False,
            "orders_enabled": False,
            "production_eligible": False,
            "goal_complete": False,
        },
    )
    print("COMPLETE_NATIVE_ACCOUNT_ANALYSIS_WRITTEN", flush=True)


if __name__ == "__main__":
    main()
