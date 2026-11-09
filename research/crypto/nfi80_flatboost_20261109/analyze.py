"""Validate native optimized accounts against frozen matched original accounts."""

import json
from pathlib import Path

import pandas as pd

from research.crypto.nfi80_capacity_20261109 import analyze as metrics
from research.crypto.nfi80_flatboost_20261109.study import (
    BASE,
    CASES,
    HERE,
    OUT,
    PROTOCOL,
    checked_hash,
    read,
    ref,
    save,
    sha,
)
from research.crypto.nfi_strength_allocation_20261107.audit import (
    CONTRACT_FIELDS,
    native_trades,
    size_audit,
)


def optimized_account(name, cost, protocol):
    plan_record = protocol["plans"][name]
    checked_hash(plan_record)
    plan = read(plan_record["path"])
    original_plan = read(BASE / f"{name}-v003.json")
    fields = (
        *CONTRACT_FIELDS,
        "storage_source",
        "storage_policy",
        "marks",
        "observation_frequency",
    )
    if any(plan[k] != original_plan[k] for k in fields):
        raise ValueError("Optimized native data/account contract differs from control")
    folder = OUT / f"{name}_{cost}bps"
    if read(folder / "registered_plan.json") != plan:
        raise ValueError("Executed plan does not equal frozen plan")
    config = read(folder / "effective_config.json")
    if config["strategy"] != "FlatTopBoost":
        raise ValueError("Native account did not execute the optimized class")
    requested = read(plan["candidates"][0]["config"]["path"])
    control = read(BASE / f"{name}-config.json")
    if {**requested, "strategy": control["strategy"], "bot_name": control["bot_name"]} != control:
        raise ValueError("Config changes beyond strategy identity")
    for filename, record in plan["candidates"][0]["source_files"].items():
        if sha(folder / "strategies" / filename) != record["sha256"]:
            raise ValueError("Actual source copy differs from registration")
    trades, cash = native_trades(plan, folder, "FlatTopBoost", cost)
    sizing = size_audit(plan, folder, "FlatTopBoost", trades)
    row = metrics.account(name, cost)
    row.update(variant="FlatTopBoost", independent_fill_cash=cash, initial_stake_audit=sizing)
    return row


def main():
    protocol = read(PROTOCOL)
    checked_hash(protocol["baseline_comparison"])
    baseline = read(protocol["baseline_comparison"]["path"])
    # Reuse the validated observer-metric audit on a different immutable input root.
    # No fills or financial account are created by this reporting module.
    metrics.OUT = OUT
    metrics.PROTOCOL = PROTOCOL
    rows = []
    changes = []
    for cost in protocol["costs_bps_per_side"]:
        for name in CASES:
            control = next(
                r
                for r in baseline["accounts"]
                if r["case"] == name and r["cost_bps_per_side"] == cost
            )
            for key in (
                "native_archive",
                "native_result",
                "observed_equity",
                "cash_audit",
                "completion",
            ):
                checked_hash(control[key])
            original = {**control, "variant": "Original"}
            optimized = optimized_account(name, cost, protocol)
            rows.extend((original, optimized))
            changes.append(
                {
                    "case": name,
                    "cost_bps_per_side": cost,
                    "net_return_change_pp": optimized["net_return_pct"] - control["net_return_pct"],
                    "drawdown_change_pp": optimized["maximum_5m_mark_drawdown_pct"]
                    - control["maximum_5m_mark_drawdown_pct"],
                    "addition_fill_change": optimized["actual_addition_fills"]
                    - control["actual_addition_fills"],
                    "research_risk_gate_passed": optimized["research_risk_gate_passed"],
                }
            )
    rankings = {}
    for cost in protocol["costs_bps_per_side"]:
        ordered = sorted(
            [r for r in rows if r["cost_bps_per_side"] == cost],
            key=lambda r: r["net_return_pct"],
            reverse=True,
        )
        rankings[str(cost)] = [
            {
                "variant": r["variant"],
                "case": r["case"],
                "net_return_pct": r["net_return_pct"],
                "maximum_5m_mark_drawdown_pct": r["maximum_5m_mark_drawdown_pct"],
                "research_risk_gate_passed": r["research_risk_gate_passed"],
            }
            for r in ordered
        ]
    result = {
        "protocol": ref(PROTOCOL),
        "accounts": rows,
        "matched_changes": changes,
        "rankings": rankings,
        "orders_enabled": False,
        "production_eligible": False,
        "old_failure_preserved": True,
        "independent_forward_validation": False,
        "qualifications": protocol["limitations"],
        "basis": (
            "Native Backtesting fills; full 5m marked equity includes open positions "
            "and exit-fee reserve"
        ),
        "native_metadata_correction": baseline["legacy_native_metadata_correction"],
    }
    save(HERE / "comparison.json", result)
    pd.DataFrame(
        [{k: v for k, v in row.items() if not isinstance(v, (dict, list))} for row in rows]
    ).to_csv(HERE / "comparison.csv", index=False)
    save(
        HERE / "analysis-receipt.json",
        {
            "source": ref(Path(__file__)),
            "reused_metric_source": ref(BASE / "analyze.py"),
            "native_cash_and_sizing_audit_source": ref(Path(size_audit.__code__.co_filename)),
            "comparison": ref(HERE / "comparison.json"),
            "completed_native_variants": 4,
            "all_fill_cash_audits_passed": True,
            "all_sizing_audits_passed": True,
            "orders_enabled": False,
        },
    )
    print(json.dumps({"rankings": rankings, "changes": changes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
