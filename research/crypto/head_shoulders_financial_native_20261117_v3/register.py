"""Register the four-wallet conditional financial study before new economics."""

# ruff: noqa: E501 - frozen report/contract strings

import difflib
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).parent.resolve()
ROOT = HERE.parents[2]
V1 = HERE.with_name("head_shoulders_source_native_20261117_v1")
V2 = HERE.with_name("head_shoulders_policy_native_20261117_v2")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path)}


def write(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, indent=2, default=str, allow_nan=False)
        stream.write("\n")


def main():
    wide = json.loads(
        HERE.with_name("strategy_comparison_wide_20261105").joinpath("plan.json").read_text()
    )
    validation = json.loads((HERE / "input_validation.json").read_text())
    if not validation["passed"] or validation["ordered_pairs"] != wide["pairs"]:
        raise ValueError("All original40 source/detail input checks must pass")
    precision = json.loads((HERE / "precision_route_validation.json").read_text())
    if (
        not precision["diagnostic_completed"]
        or precision["default_route_exactly_equal"]
        or precision["common_hourly_tick_mismatches"] != 6648
    ):
        raise ValueError(
            "Preserve the observed default precision mismatch before selecting shared reference"
        )
    for stage in [V1, V2]:
        manifest = json.loads((stage / "artifact_manifest.json").read_text())
        for item in manifest["files"]:
            if sha(item["path"]) != item["sha256"]:
                raise ValueError("A sealed prior stage changed")
    files = {}
    for name in ["HeadShouldersInputs.py", "HeadShouldersStrategy.py"]:
        if sha(HERE / "sources" / name) != sha(V2 / name):
            raise ValueError("Financial strategy must be an unchanged byte copy")
        files[name] = record(HERE / "sources" / name)
    nfi_plan_path = HERE.with_name("nfi40_allocation_native_20261117_v1") / "plan.json"
    nfi_plan = json.loads(nfi_plan_path.read_text())
    base_config = json.loads((V2 / "native_config.json").read_text())
    candidates = []
    for capital in [1000, 500]:
        for cost in [15, 8]:
            name = f"hs40_{capital}_cost{cost}"
            config = json.loads(json.dumps(base_config))
            config.update(
                dry_run_wallet=capital,
                fee=cost / 10000,
                bot_name=name,
                strategy_path=str(HERE / "sources"),
                export="trades",
                user_data_dir=str(ROOT / "user_data/crypto_quant" / HERE.name / name),
            )
            write(name + ".json", config)
            candidates.append(
                {
                    "name": name,
                    "strategy": "HeadShouldersStrategy",
                    "capital": capital,
                    "cost_bps": cost,
                    "pairs": wide["pairs"],
                    "config": record(HERE / (name + ".json")),
                    "source_files": files,
                    "market_snapshot": wide["market_snapshot"],
                    "observer_source": wide["observer_source"],
                }
            )
    benchmarks = []
    for cost, cohort in [
        (15, "wide_strategy_cost15_20261105_v001"),
        (8, "wide_strategy_comparison_20261105_v001"),
    ]:
        path = ROOT / "user_data/crypto_quant" / cohort / f"nfi-x8-1x-cost{cost}"
        benchmarks.append(
            {
                "capital": 1000,
                "cost_bps": cost,
                "candidate": "nfi-x8-1x",
                "result": record(path / "result.json"),
                "hourly_curve": record(path / "hourly_equity.parquet"),
                "scope": "Known old conditional native model, not new validated causal profit",
            }
        )
    economic_protocol = {
        "registered_at": datetime.now(UTC).isoformat(),
        "new_hs_financial_results_observed": False,
        "known_prior_research": "Original NFI1000 returns and v1/v2 source/interface diagnostics known; repeated historical window is not independent OOS",
        "wallets": [
            {"capital": c["capital"], "cost_bps_per_side": c["cost_bps"]} for c in candidates
        ],
        "pool": wide["pairs"],
        "leverage": 1,
        "slots_per_wallet": 6,
        "tradable_balance_ratio": 0.99,
        "source_timeframe": "1h",
        "matching_detail": "5m",
        "entry_exit_order_type": "market",
        "window_start": wide["start"],
        "window_end_common_mark": wide["comparison_end"],
        "periods": wide["periods"],
        "standalone_admission": {
            "both_costs_required_for_each_capital": True,
            "each_declared_period_net_return_strictly_positive": True,
            "each_declared_period_hourly_mark_drawdown_max": 0.15,
            "each_declared_period_complete_24h_sharpe_strictly_greater_than": 0.5,
            "missing_or_nonfinite_metric_rejected": True,
            "all_native_cash_fill_nav_and_funding_audits_required": True,
        },
        "fixed_combo": {
            "total_initial_capital": 1000,
            "hs_actual_wallet": 500,
            "nfi_x8_actual_wallet": 500,
            "no_linear_scaling_of1000_wallets": True,
            "no_dynamic_rebalancing_or_weight_search": True,
            "wallets_are_separate_fixed_sleeves": True,
            "max_positions_per_sleeve": 6,
            "combined_possible_position_slots": 12,
            "same_asset_opposite_directions_not_netted_for_funding_or_gross_risk": True,
            "nav": "sum the two complete actual500 hourly equity levels on identical clocks",
            "both_costs_required": True,
            "full_window_net_return_strictly_above_same_cost_NFI1000": True,
            "full_window_hourly_drawdown_no_higher_than_same_cost_NFI1000": True,
            "each_declared_period_net_return_strictly_positive": True,
            "both_actual_sleeves_independently_audited_required": True,
        },
        "precision_route": "supported get_pair_precision returns exact originalNFI5m whole-month table/asof",
        "default_hs1h_route_mismatch_preserved": {
            "monthly_rows": 10,
            "common_hourly_decisions": 6648,
        },
        "historical_rules_or_native_lookahead_corrected": False,
        "funding_model": "official native default inclusive funding; no alternative ownership extension",
        "funding_gate": "independent reconstruction of every boundary fill and hourly accrued amount; no admission from native total alone",
        "missing_funding_gate": "AXS/TRB2026-06-24T04Z remain UNKNOWN if any actual gross exposure; never impute0 or net opposing positions",
        "source_policy_gate": "unchanged v2 callback byte copies; any callback_error aborts run; bind actual enter_tag and distinguish source/risk/force exits",
        "model_contract": "Conditional old execution model with explicitly shared legacy5m inferred precision; no independent OOS, historical rule certification or live evidence",
        "no_post_result_retuning": True,
        "all_failures_retained": True,
        "orders_enabled": False,
        "production_eligible": False,
        "goal_complete": False,
    }
    write("economic_protocol.json", economic_protocol)
    seals = []
    for stage in [V1, V2]:
        seals.append(record(stage / "artifact_manifest.json"))
        seals.extend(
            {"path": item["path"], "sha256": item["sha256"]}
            for item in json.loads((stage / "artifact_manifest.json").read_text())["files"]
        )
    own_sealed = [
        "economic_protocol.json",
        "input_protocol.json",
        "input_validation.json",
        "audit_inputs.py",
        "precision_protocol.json",
        "precision_route_validation.json",
        "audit_precision_route.py",
        "precision_monthly_comparison.parquet",
        "precision_hourly_comparison.parquet",
        "legacy5m_reference.parquet",
        "PolicyReceipts.py",
        "FundingReceipts.py",
        "release_guard.py",
        "LegacyFiveMinutePrecision.py",
    ]
    seals.extend(record(HERE / name) for name in own_sealed)
    seals.extend(
        record(HERE.with_name(folder) / name)
        for folder, name in [
            ("wavelet_financial_native_20261117_v4", "CaptureRules.py"),
            ("band_trend_native_20261113_v1", "observe_clock.py"),
            ("skew_vol_native_20261113_v1", "clock_fix.py"),
        ]
    )
    inherited = [
        "engine_source",
        "engine_manifest",
        "engine_commit",
        "environment",
        "research_support",
        "data_receipts",
        "datadir",
        "market_snapshot",
        "observer_source",
        "marks",
        "start",
        "end",
        "comparison_end",
        "periods",
    ]
    plan = {key: wide[key] for key in inherited}
    plan.update(
        registered_at=datetime.now(UTC).isoformat(),
        schema="head-shoulders-conditional-financial-v3",
        candidates=candidates,
        pairs=wide["pairs"],
        total_cost_bps_per_side=[15, 8],
        allowed_leverage=1,
        initial_equity_usdt=1000,
        native_start="2024-12-31T23:00:00Z",
        output_root=str(ROOT / "user_data/crypto_quant" / HERE.name),
        runner_module="research.crypto.head_shoulders_financial_native_20261117_v3.native_runner",
        runner_sha256=sha(HERE / "native_runner.py"),
        storage_source=record(
            HERE.with_name("wavelet_financial_native_20261117_v4") / "memory_storage.py"
        ),
        storage_contract="unchanged native dict/object semantics for40 narrow1h analyzed frames, under RSS guard",
        price_precision_source=record(HERE / "LegacyFiveMinutePrecision.py"),
        legacy5m_reference=record(HERE / "legacy5m_reference.parquet"),
        precision_diagnostic=record(HERE / "precision_route_validation.json"),
        economic_protocol=record(HERE / "economic_protocol.json"),
        input_validation=record(HERE / "input_validation.json"),
        policy_interface_receipt=record(V2 / "native_probe/admission.json"),
        additional_source_seals=seals,
        benchmark_nfi1000=benchmarks,
        nfi500_dependency={
            "plan": record(nfi_plan_path),
            "output_root": nfi_plan["output_root"],
            "execution_complete_path": str(nfi_plan_path.parent / "execution_complete.json"),
            "candidate_names": [item["name"] for item in nfi_plan["candidates"]],
        },
        memory_guard_bytes=3 * 1024**3,
        minimum_free_disk_bytes=int(1.5 * 1024**3),
        wall_guard_seconds=4 * 3600,
        maximum_financial_workers=1,
        shared_financial_lock=str(nfi_plan_path.parent / "financial_worker.lock"),
        queue_state_at_registration="HOLD_ROOT_REVIEW_AND_NFI500_COMPLETION",
        conditional_old_execution_model=True,
        historical_rules_certified=False,
        new_financial_outcomes_observed=False,
        orders_enabled=False,
        production_eligible=False,
    )
    write("plan.json", plan)
    template = HERE.with_name("wavelet_financial_native_20261117_v4") / "native_runner.py"
    patch = "".join(
        difflib.unified_diff(
            template.read_text().splitlines(True),
            (HERE / "native_runner.py").read_text().splitlines(True),
            fromfile=str(template),
            tofile=str(HERE / "native_runner.py"),
        )
    )
    (HERE / "runner_derivation.patch").write_text(patch)
    write(
        "runner_derivation.json",
        {
            "template": record(template),
            "new_runner": record(HERE / "native_runner.py"),
            "patch_sha256": sha(HERE / "runner_derivation.patch"),
            "official_engine_modified": False,
            "changes": [
                "root release and single-worker guards",
                "shared legacyNFI5m supported precision",
                "strict callback abort and sourceevent binding receipts",
                "funding boundary/missing-slot inventory",
                "default native funding retained",
                "datetime-unit-only calendar normalization",
            ],
        },
    )
    frozen = [
        "plan.json",
        "economic_protocol.json",
        "native_runner.py",
        "execute.py",
        "preflight.py",
        "register.py",
        "PolicyReceipts.py",
        "FundingReceipts.py",
        "release_guard.py",
        "LegacyFiveMinutePrecision.py",
        "runner_derivation.json",
        "runner_derivation.patch",
        *[c["name"] + ".json" for c in candidates],
    ]
    write(
        "implementation_freeze.json",
        {
            "frozen_at": datetime.now(UTC).isoformat(),
            "before_new_financial_outcomes": True,
            "files": {str(HERE / name): sha(HERE / name) for name in frozen},
        },
    )
    print(
        json.dumps(
            {
                "registered": True,
                "plan_sha256": sha(HERE / "plan.json"),
                "candidate_count": len(candidates),
                "financial_queue_released": False,
            }
        )
    )


if __name__ == "__main__":
    main()
