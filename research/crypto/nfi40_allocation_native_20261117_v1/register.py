"""Freeze actual500USDT native account contracts before the bounded probe."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = HERE.with_name("strategy_comparison_wide_20261105")


def record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    assert not (HERE / "plan.json").exists()
    original = json.loads((BASE / "plan.json").read_text())
    now = datetime.now(UTC).isoformat()
    protocol = {
        "registered_at": now,
        "scope": "Two actual500USDT NFI X8 wallets for later fixed-total-account combinations",
        "unchanged": [
            "Original ordered40pool",
            "sourceX8v18.0.146",
            "isolated1x",
            "6slots",
            "native5m",
            "sourceDCA/limits/exits",
            "99percent balance",
            "original dataset",
            "full2025Jan1-2026Aug31T23UTC",
            "cost15/8bps plus native funding",
        ],
        "not_scaled_from1000": True,
        "new_alpha_claim": False,
        "cold_profit_cache_each_wallet": True,
        "old_indicator_pickles_reused": False,
        "conditional_old_execution_model": True,
        "unresolved": [
            "Native whole-month price precision heuristic uses later month observations",
            "Historical rules/tradability and survivorship not certified",
            "AXS/TRB2026Jun24T04 funding events unknown; recheck each new actual wallet exposure",
            "Flat fee proxy is not venue queue/fill or private settlement proof",
            "Repeated research window is not independent OOS",
        ],
        "resource_limits": {
            "rss_bytes": 5905580032,
            "minimum_free_disk_bytes": 1610612736,
            "wall_seconds": 14400,
            "financial_workers": 1,
            "analyzed_provider_lru": 4,
        },
        "preflight": {
            "configured_universe": "unchanged40orderedpairs",
            "computed_pair": "BTC/USDT:USDT",
            "source_main_rows": 4000,
            "original_startup_rows": 800,
            "source_informative_history": "OfficialDataProvider with unchanged source requests",
            "financial_start_forbidden": True,
            "full40resource_feasibility_not_proven_by_one_frame": True,
        },
        "orders_enabled": False,
        "production_eligible": False,
        "goal_complete": False,
    }
    (HERE / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    plan = copy.deepcopy(original)
    for key in [
        "registration_completed_at",
        "technical_revision",
        "revision_reason",
        "revision_at",
        "registration_note",
    ]:
        plan.pop(key, None)
    plan["parent_plan"] = record(BASE / "plan.json")
    plan.update(
        schema="nfi40-actual500-allocation-native-v1",
        registered_at=now,
        initial_equity_usdt=500,
        total_cost_bps_per_side=[15, 8],
        runner_module="research.crypto.nfi40_allocation_native_20261117_v1.native_runner",
        runner_sha256=record(HERE / "native_runner.py")["sha256"],
        storage_source=record(HERE.with_name("nfi80_capacity_20261109") / "zstd_storage.py"),
        row_storage=record(
            HERE.with_name("nfi80_historical_universe_20261109") / "v003/row_storage.py"
        ),
        storage_adapter=record(HERE / "storage_adapter.py"),
        output_root=str(ROOT / "user_data/crypto_quant" / HERE.name),
        memory_guard_bytes=5905580032,
        minimum_free_disk_bytes=1610612736,
        wall_guard_seconds=14400,
        maximum_financial_workers=1,
        storage_policy={
            "lossless_indicator_map_lru": 1,
            "analyzed_provider_lru": 4,
            "trim": "existing v003 LazyTrimMapping",
            "native_rows": "existing v003 RowBlocks",
            "old_cache_reuse": False,
            "numerical_downcast": False,
        },
        comparison_purpose="Actual500wallets only, no scaled1000 returns or causal model upgrade",
        economic_results_observed_at_registration=True,
        new_factors=0,
        parameter_optimization=False,
        conditional_old_execution_model=True,
    )
    candidate = original["candidates"][0]
    plan["candidates"] = []
    for fee in [15, 8]:
        item = copy.deepcopy(candidate)
        item.update(name=f"nfi40_x8_500_cost{fee}", capital=500, cost_bps=fee)
        cfg = json.loads(Path(candidate["config"]["path"]).read_text())
        cfg.update(dry_run_wallet=500, fee=fee / 10000, bot_name=item["name"])
        path = HERE / (item["name"] + ".json")
        path.write_text(json.dumps(cfg, indent=2) + "\n")
        item["config"] = record(path)
        plan["candidates"].append(item)
    extras = [
        HERE / n
        for n in [
            "protocol.json",
            "register.py",
            "preflight.py",
            "storage_adapter.py",
            "runner_derivation.json",
            "runner_derivation.patch",
        ]
    ]
    plan["additional_source_seals"] = [record(p) for p in extras]
    Path(plan["output_root"]).mkdir(exist_ok=False)
    (HERE / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    files = [HERE / "plan.json", HERE / "native_runner.py", *extras]
    files += [Path(c["config"]["path"]) for c in plan["candidates"]]
    files += [Path(plan[n]["path"]) for n in ["storage_source", "row_storage"]]
    (HERE / "implementation_freeze.json").write_text(
        json.dumps(
            {
                "frozen_at": now,
                "financial_start_called": False,
                "preflight_started": False,
                "files": {str(p): record(p)["sha256"] for p in files},
            },
            indent=2,
        )
        + "\n"
    )
    print("REGISTERED_TWO_ACTUAL500_WALLETS_PREFLIGHT_ONLY")


if __name__ == "__main__":
    main()
