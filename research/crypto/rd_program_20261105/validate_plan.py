"""Read-only validation of the frozen RD plan; never starts a trading process."""

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(name):
    return json.loads((HERE / name).read_text())


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def main():
    plan = read("plan.json")
    registry = read("experiment_registry.json")
    sources = read("sources.json")
    evidence = read("evidence_snapshot.json")
    freeze = read("freeze.json")
    for relative, expected in freeze["files"].items():
        path = ROOT / relative
        require(path.is_file(), f"Missing frozen file: {relative}")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                f"Frozen file changed: {relative}")
    for relative, expected in freeze["deployment_locks"].items():
        require(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected,
                f"Deployment lock changed: {relative}")
    require(plan["orders_enabled"] is False, "Plan must not enable real orders")
    require(plan["production_eligible"] is False, "No production qualification")
    require(plan["runtime_changes_allowed"] is False, "Existing services must remain intact")
    require(len(sources["repositories"]) <= 4, "Source research scope expanded")
    for repo in sources["repositories"]:
        require(re.fullmatch(r"[0-9a-f]{40}", repo["commit"]), "Unpinned repository")
        require(repo.get("license") and repo.get("license_url"), "Missing license evidence")
    require(len(plan["baselines"]) == 5, "Baseline family limit changed")
    require(len(plan["markets"]["spot"]["pairs"]) == 36, "Spot universe changed")
    require(len(plan["markets"]["futures"]["pairs"]) == 40, "Futures universe changed")
    for market, expected_costs in [("spot", [13, 20]), ("futures", [8, 15])]:
        spec = plan["markets"][market]
        require(spec["initial_equity_usdt"] == 1000 and spec["leverage"] == 1,
                f"Capital/leverage changed: {market}")
        require([c["total_bps_per_side"] for c in spec["cost_scenarios"]] == expected_costs,
                f"Cost scenario changed: {market}")
        for cost in spec["cost_scenarios"]:
            require(cost["total_bps_per_side"] == cost["fee_bps_per_side"] +
                    cost["execution_proxy_bps_per_side"], "Cost sum mismatch")
    risk = plan["risk_contract"]
    require(risk["absolute_equity_floor_usdt"] == 500, "User absolute floor changed")
    require(risk["existing_deployment_has_this_contract"] is False,
            "Proposed control misrepresented as deployed")
    require(risk["runtime_activation_authorized"] is False, "Runtime activation not authorized")
    require(risk["native_protections_are_mtm_fuse"] is False, "Native protection semantics wrong")
    require(all("stop_duration" in p for p in risk["native_protections"]),
            "Use minutes; detail timeframe is not protection timeframe")
    require(evidence["runtime_equity_floor_implemented"] is False,
            "Current implementation fact changed without new observation")
    ids = [r["id"] for r in registry["runs"]]
    require(len(ids) == len(set(ids)) == plan["budgets"]["P1_max_accounts"] == 32,
            "P1 registry must contain exactly 32 unique account registrations")
    baselines = {b["id"] for b in plan["baselines"] if b["phase"] == "P1"}
    expected = {(b, m, w["id"], c) for b in baselines for m in plan["markets"]
                for w in plan["windows"] for c in ("base", "stress")}
    actual = {(r["baseline_id"], r["market"], r["window_id"], r["cost_id"])
              for r in registry["runs"]}
    require(actual == expected, "P1 Cartesian matrix has a missing or extra cell")
    require(all(r["result"] is None and r["status"] == "registered_not_run"
                for r in registry["runs"]), "Registration must not overwrite observed results")
    for matrix in registry["conditional_future_matrices"]:
        require(matrix["authorized"] is False, "P2/P3 not authorized by current scope")
        require(matrix["max_accounts"] == plan["budgets"][matrix["phase"] + "_max_accounts"]
                == 80, "Future continuous accounts must count toward budget")
    require(plan["final_holdout"]["status"] == "not_started" and
            plan["final_holdout"]["start"] is None, "No prospective holdout has started")
    require(plan["budgets"]["hyperopt_trials"] == plan["budgets"]["ML_trials"] == 0,
            "No search/ML budget registered")
    for record in registry["historical_records"]:
        require(hashlib.sha256((ROOT / record["source_path"]).read_bytes()).hexdigest()
                == record["source_sha256"], "Historical failure evidence changed")
    print(json.dumps({"plan_valid": True, "p1_registered_accounts": len(ids),
                      "new_experiments_run_by_validator": 0,
                      "runtime_modified": False, "execution_readiness": "gated_by_P0"}))


if __name__ == "__main__":
    main()
