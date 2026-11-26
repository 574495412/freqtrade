"""Verify the existing short-rebuy margin cap on the later complete account."""

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save

from .accounts import COSTS, write_once
from .inputs import HERE, OUT
from .low_disk_run import BLOCK_RESEARCH, CAP_STUDY, execute
from .tag_payoff_reserve import profile


STRATEGY = "FlatBoostShortRebuyCap"
ROLE = "short_rebuy_cap_12"


def register(comparisons):
    for cost in comparisons:
        parity = read(BLOCK_RESEARCH / f"recent_parity_{cost}bp/parity.json")
        assert parity["exact_marked_wallet"] and parity["exact_trades"]
    discovered_plan = receipt(CAP_STUDY / "plan.json")
    discovered, = read(checked(discovered_plan))["candidates"]
    assert discovered["strategy"] == STRATEGY and discovered["max_open_trades"] == 12
    original = receipt(OUT / "registrations/fixed_boost_12-plan.json")
    plan = read(checked(original))
    control, = plan["candidates"]
    assert control["strategy"] == "FlatTopBoost" and control["max_open_trades"] == 12
    assert (plan["start"], plan["end"]) == ("2026-11-10T00:00:00+00:00", "2026-11-26T00:00:00+00:00")
    assert plan["initial_equity_usdt"] == 1000 and plan["allowed_leverage"] == 3
    assert pd.Timestamp(plan["comparison_end"]) == pd.Timestamp(plan["end"]) - pd.Timedelta(minutes=5)
    assert all(discovered["source_files"][name]["sha256"] == item["sha256"]
               for name, item in control["source_files"].items())
    filename = f"{STRATEGY}.py"
    source = discovered["source_files"][filename]
    checked(source)
    output = OUT / "short_rebuy_cap_reserve_registration"
    output.mkdir(exist_ok=True)
    config = read(checked(control["config"]))
    assert config["dry_run"] is True and config["margin_mode"] == "isolated"
    config = {**config, "strategy": STRATEGY, "bot_name": "nfi80-short-rebuy-cap-reserve"}
    candidate = {
        **control, "name": ROLE, "strategy": STRATEGY,
        "source_files": {**control["source_files"], filename: source},
        "source_and_risk_behavior": discovered["source_and_risk_behavior"],
        "config": write_once(output / "config.json", config),
    }
    controls = {str(cost): receipt(HERE / f"recent_window_{cost}bp_controls.json") for cost in COSTS}
    control_execution_plans = {}
    for cost, summary in controls.items():
        native = OUT / "accounts" / f"fixed_boost_12_{cost}bp"
        evidence = read(checked(summary))["accounts"]["fixed_boost_12"]
        assert evidence["five_minute_nav_reconciliation"]["passed"]
        assert checked(evidence["sources"][0]) == native / "result.json"
        recorded = receipt(native / "registered_plan.json")
        assert read(checked(recorded)) == plan
        control_execution_plans[cost] = recorded
    registered = write_once(output / "plan.json", {
        **plan, "candidates": [candidate], "comparison_purpose": "Unchanged cap on the later complete window",
        "reserve_protocol": receipt(HERE / "short_rebuy_cap_reserve_protocol.json"),
        "control_plan": original, "discovery_plan": discovered_plan, "discovery_comparisons": comparisons,
        "controls": controls, "control_execution_plans": control_execution_plans,
        "orders_enabled": False, "production_eligible": False,
    })
    return registered, controls


def main():
    comparisons = {str(cost): receipt(CAP_STUDY / f"comparison_{cost}bp.json") for cost in COSTS}
    outcomes = {cost: read(checked(item)) for cost, item in comparisons.items()}
    assert all(str(report["cost_bps_per_side"]) == cost for cost, report in outcomes.items())
    failing = [cost for cost, report in outcomes.items() if not report["advance"]]
    if failing:
        save(HERE / "short_rebuy_cap_reserve_admission.json", {
            "state": "discovery_gate_not_met", "failed_cost_bps_per_side": failing,
            "discovery_comparisons": comparisons,
            "failed_checks": {cost: outcomes[cost]["checks"] for cost in failing},
            "protocol": receipt(HERE / "short_rebuy_cap_reserve_protocol.json"),
            "completed_reserve_accounts": 0, "orders_enabled": False,
        })
        print("Short-rebuy cap reserve admission did not pass", failing, flush=True)
        return
    registered, controls = register(comparisons)
    results = {}
    for cost in COSTS:
        root = BLOCK_RESEARCH / f"reserve_{ROLE}_{cost}bp"
        execute(checked(registered), root, cost, "blocks")
        current = receipt(root / "account_reconciled.json")
        audit = read(checked(current))
        candidate = profile(audit)
        risk = audit["short_rebuy_cap"]
        benchmark_file = controls[str(cost)]
        backtest = read(checked(benchmark_file))
        assert backtest["cost_bps_per_side"] == cost and backtest["window_is_restart"]
        control = profile(backtest["accounts"]["fixed_boost_12"])
        checks = {
            "higher_return": candidate["net_return"] > control["net_return"],
            "no_higher_drawdown": candidate["drawdown"] <= control["drawdown"],
            "no_fewer_large_winners": (
                candidate["wins_at_least_1pct_of_equity"] >= control["wins_at_least_1pct_of_equity"]
            ),
            "cap_acted": risk["blocked_adjustment_requests"] > 0,
            "no_liquidations": candidate["liquidations"] == 0,
        }
        results[str(cost)] = {"candidate": candidate, "control": control, "risk": risk, "checks": checks,
                              "audited_account": current, "audited_control": benchmark_file}
        save(HERE / "short_rebuy_cap_reserve_results.json", {
            "registered_plan": registered, "by_cost": results, "completed_native_accounts": len(results),
            "transfer_verified": len(results) == len(COSTS) and all(
                all(report["checks"].values()) for report in results.values()),
            "orders_enabled": False, "production_eligible": False,
        })
        print("verified short-rebuy reserve", cost, checks, flush=True)


if __name__ == "__main__":
    main()
