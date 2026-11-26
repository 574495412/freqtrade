"""Cross-check the preserved native accounts and assemble the study evidence."""

from pathlib import Path

from research.crypto.nfi40_boost_profit_tail_20261125_v1.audit import analyze
from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked, save, verify,
)

from .study import FIRST, HERE, NAME, OUTPUT, PLAN, STORAGE, read, receipt, verify_plan


CONTRACT = HERE.parent / "mainline_comparison_contract_20261117_v1/contract.json"
ACCOUNT_KEYS = (
    "engine_commit", "engine_source", "engine_manifest", "environment", "pairs",
    "start", "end", "periods", "allowed_leverage", "initial_equity_usdt",
    "market_snapshot", "data_receipts", "datadir", "marks",
)
ROLES = ("original", "fixed_boost", "candidate")
STRATEGIES = ("NostalgiaForInfinityX8", "FlatTopBoost", NAME)
SLOTS = (6, 6, 8)


def verified_account(plan, account_files, strategy, slots, cost):
    result, archive, curve = map(checked, account_files)
    folder = result.parent
    config_path = folder / "effective_config.json"
    saved_plan_path = folder / "registered_plan.json"
    cash_path = folder / "fill_cash_audit.json"
    config, saved_plan, cash = map(read, (config_path, saved_plan_path, cash_path))
    native = read(result)
    assert all(saved_plan[key] == plan[key] for key in ACCOUNT_KEYS)
    assert config["strategy"] == strategy and config["max_open_trades"] == slots
    assert config["dry_run"] and config["dry_run_wallet"] == plan["initial_equity_usdt"]
    assert config["exchange"]["pair_whitelist"] == plan["pairs"]
    assert config["timeframe"] == "5m" and config["margin_mode"] == "isolated"
    assert config["fee"] == cost / 10000 and cash["passed"]
    assert native["orders_enabled"] is False and native["production_eligible"] is False
    assert Path(native["engine_source"]).resolve().is_relative_to(Path(plan["engine_source"]))
    assert native["strategy"] == strategy and native["cost_bps_per_side"] == cost
    assert native["native_terminal_not_used_for_ranking"]
    case = next(row for row in saved_plan["candidates"] if row["strategy"] == strategy)
    assert native["source_strategy_files"] == case["source_files"]
    for name, source in native["source_strategy_files"].items():
        checked(source)
        checked({"path": folder / "strategies" / name, "sha256": source["sha256"]})
    reference_sources = plan["candidates"][-1]["source_files"]
    assert all(source == reference_sources[name]
               for name, source in native["source_strategy_files"].items())
    _, summary = analyze(plan, result, archive, curve, cost)
    assert summary["reconciliation"]["passed"]
    assert all(row["status"] == "NO_GROSS_EXPOSURE"
               for row in summary["reconciliation"]["missing_rate_exposure"])
    summary["additional_receipts"] = [receipt(path) for path in (config_path, saved_plan_path, cash_path)]
    return summary, invariant_config(config)


def group(plan, records, cost):
    accounts, configs = {}, []
    for role, strategy, slots in zip(ROLES, STRATEGIES, SLOTS, strict=True):
        summary, config = verified_account(plan, records[role], strategy, slots, cost)
        accounts[role] = summary
        configs.append(config)
    assert configs[0] == configs[1] == configs[2]
    returns = {role: accounts[role]["periods"][0]["net_return"] for role in ROLES}
    return {
        "accounts": accounts,
        "return_lift_over_fixed_boost": returns["candidate"] - returns["fixed_boost"],
        "return_lift_over_original": returns["candidate"] - returns["original"],
    }


def main():
    plan = read(PLAN)
    assert len(plan["pairs"]) == 40 and plan["initial_equity_usdt"] == 1000
    assert plan["allowed_leverage"] == 1 and plan["total_cost_bps_per_side"] == [15, 8]
    verify(plan, plan["candidates"][0])
    baselines = {row["cost_bps_per_side"]: row for row in read(CONTRACT)["baselines"]}
    main_cases, evidence = {}, [receipt(PLAN), receipt(CONTRACT), plan["protocol"]]
    for cost in (15, 8):
        verify_plan(plan, cost)
        comparison_path = HERE / f"comparison_{cost}bp.json"
        comparison = read(comparison_path)
        native = {
            "original": baselines[cost]["old_native_outputs_rechecked"],
            "fixed_boost": comparison["fixed_boost"]["source_receipts"],
            "candidate": comparison["candidate"]["source_receipts"],
        }
        assert native["fixed_boost"] == plan["controls"][str(cost)][:3]
        assert all(OUTPUT / f"cost_{cost}bp" in Path(row["path"]).parents
                   for row in native["candidate"])
        report = group(plan, native, cost)
        assert report["accounts"]["original"]["periods"][0]["net_return"] == baselines[cost]["net_return"]
        assert report["return_lift_over_fixed_boost"] == comparison["return_lift_over_boost"]
        main_cases[str(cost)] = report
        evidence.append(receipt(comparison_path))

    holdout_path = HERE / "holdout_plan.json"
    holdout_plan = read(holdout_path)
    verify(holdout_plan, holdout_plan["candidates"][2])
    for key in ("engine_commit", "engine_source", "engine_manifest", "environment", "pairs",
                "allowed_leverage", "initial_equity_usdt", "market_snapshot"):
        assert holdout_plan[key] == plan[key]
    assert holdout_plan["start"] > plan["end"]
    assert holdout_plan["candidates"][2]["source_files"] == plan["candidates"][0]["source_files"]
    assert holdout_plan["total_cost_bps_per_side"] == [15]
    assert [case["name"] for case in holdout_plan["candidates"]] == list(ROLES)
    trigger = checked(holdout_plan["trigger"])
    assert trigger == HERE / "comparison_15bp.json" and read(trigger)["advance"]
    coverage = read(checked(holdout_plan["coverage"]))
    assert not coverage["errors"] and set(coverage["completed"]) == set(plan["pairs"])
    ref = HERE / "holdout_comparison.json"
    independent = read(ref)
    records = {role: value["source_receipts"] for role, value in independent["accounts"].items()}
    held = group(holdout_plan, records, 15)
    assert held["return_lift_over_fixed_boost"] == independent["return_lift_over_boost"]
    assert held["return_lift_over_original"] == independent["return_lift_over_original"]
    for path in (holdout_path, ref, FIRST / "comparison_15bp.json", STORAGE / "comparison_15bp.json"):
        evidence.append(receipt(path))
    evidence.extend((holdout_plan["coverage"], plan["parent_plan"]))
    save(HERE / "verification_summary.json", {
        "engine_commit": plan["engine_commit"], "study": NAME,
        "main_window": main_cases, "independent_20_day_window": held,
        "negative_studies": {
            "plain_eight_slots_drawdown_gate_passed": read(STORAGE / "comparison_15bp.json")["passes_prefrozen_screen"],
            "delayed_exit_advance": read(FIRST / "comparison_15bp.json")["advance"],
        },
        "evidence": evidence, "orders_enabled": False, "production_eligible": False,
    })
    print("Verified six main-window and three independent-window native accounts")


if __name__ == "__main__":
    main()
