"""Independent fill/cash/NAV audit and preregistered complete-wallet comparison."""

import importlib.util
import json
from pathlib import Path

import pandas as pd

from research.crypto.hawkes_source_native_20261117_v1 import audit_accounts as audit

HERE = Path(__file__).resolve().parent
BASE = HERE.with_name("nfi_carry_matched_native_20261115_v1")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gates(row, reference):
    full = row["periods"][0]
    return {"all_continuous_periods_positive": all(p["net_return"] > 0 for p in row["periods"]),
            "hourly_drawdown_lte15pct": full["hourly_mark_drawdown"] <= 0.15,
            "daily_nav_sharpe_gt0_5": full["daily_nav_sharpe"] is not None and full["daily_nav_sharpe"] > 0.5,
            "beats_matched_nfi1000_return": full["net_return"] > reference["periods"][0]["net_return"]}


def main():
    plan = audit.read(HERE / "plan.json")
    frozen = audit.read(HERE / "implementation_freeze.json")
    assert all(audit.sha(p) == sha for p, sha in frozen["files"].items())
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h")
    oracle = load("hawkes_independent_cash_oracle", "/Users/chrise/GitHub/TradingAgents/research_tools/nfi_reproduction/audit_original.py")
    references = audit.read(BASE / "plan.json")
    prior = load("hawkes_reference_auditor", HERE.with_name("trendcore_source_native_20261117_v1") / "audit_accounts.py")
    # Retain old sealed files byte-for-byte; new read-only verification receipts go here.
    prior.save = lambda path, result: audit.save(HERE / ("reference_" + Path(path).parent.name + ".json"), result)
    curves, rows = {}, {}
    for candidate in references["candidates"]:
        if candidate["mode"] != "futures":
            continue
        nav, row = prior.audit(candidate, references, grid, oracle)
        row.update(reused=True, evidence_type="PRIOR_NATIVE_ACCOUNT", cost_bps=candidate["cost_bps"])
        curves[candidate["name"]], rows[candidate["name"]] = nav, row
    native_rows = []
    for candidate in plan["candidates"]:
        nav, row = audit.audit(candidate, plan, grid, oracle)
        row.update(reused=False, evidence_type="NEW_NATIVE_ACCOUNT", cost_bps=candidate["cost_bps"],
                   funding_ownership=candidate["funding_ownership"])
        curves[candidate["name"]], rows[candidate["name"]] = nav, row
        native_rows.append(row)
    standalone, combos = [], []
    for candidate in plan["candidates"]:
        reference = rows["nfi_futures_1000_" + candidate["scenario"]]
        if candidate["capital"] == 1000:
            row = rows[candidate["name"]]
            criteria = gates(row, reference)
            standalone.append({"account": candidate["name"], "criteria": criteria,
                               "financial_gate_passed": all(criteria.values())})
        else:
            other = "nfi_futures_500_" + candidate["scenario"]
            nav = curves[candidate["name"]] + curves[other]
            name = candidate["name"] + "+" + other
            row = {"name": name, "legs": [candidate["name"], other], "capital": 1000,
                   "cost_bps": candidate["cost_bps"], "funding_ownership": candidate["funding_ownership"],
                   "wallet_structure": "Two actual500USDT native wallets,no transfers/rebalancing",
                   "periods": audit.periods(nav, plan)}
            criteria = gates(row, reference)
            criteria["no_worse_drawdown_than_matched_nfi1000"] = row["periods"][0]["hourly_mark_drawdown"] <= reference["periods"][0]["hourly_mark_drawdown"]
            row.update(criteria=criteria, financial_gate_passed=all(criteria.values()))
            path = HERE / (name + ".parquet")
            nav.rename("hourly_mark_equity").reset_index().to_parquet(path, index=False)
            row["curve"] = {"path": str(path), "sha256": audit.sha(path)}
            combos.append(row)
    passed = all(r["financial_gate_passed"] for r in standalone) or all(r["financial_gate_passed"] for r in combos)
    result = {"accounts": list(rows.values()), "portfolios": combos, "standalone_gates": standalone,
              "financial_promotion_gate_passed": passed, "new_independently_validated_alpha": 0,
              "independent_oos_proven": False, "orders_enabled": False, "production_eligible": False,
              "native_accounts_completed": len(native_rows), "goal_complete": False}
    audit.save(HERE / "results.json", result)
    print(json.dumps({"native_accounts": len(native_rows), "financial_promotion_gate_passed": passed,
                      "results": str(HERE / "results.json")}))


if __name__ == "__main__":
    main()
