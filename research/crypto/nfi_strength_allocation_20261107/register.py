"""Register matched native NFI X8 accounts before observing A04 economics."""

from datetime import UTC, datetime
from pathlib import Path

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PARENT = HERE.parent / "strategy_comparison_wide_20261105"
DATA = ROOT / "user_data/crypto_quant"
OUT = DATA / "nfi_strength_allocation_20261107_v001"
PLAN = HERE / "registration.json"
BASELINES = {
    8: DATA / "wide_strategy_comparison_20261105_v001/nfi-x8-1x-cost8",
    15: DATA / "wide_strategy_cost15_20261105_v001/nfi-x8-1x-cost15",
}


def record(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    import json

    if PLAN.exists() or OUT.exists():
        raise FileExistsError("A04 already registered")
    base_plan = PARENT / "plan.json"
    plan = json.loads(base_plan.read_text())
    source = next(c for c in plan["candidates"] if c["name"] == "nfi-x8-1x")
    config = json.loads(Path(source["config"]["path"]).read_text())
    candidates = []
    for strategy in ("StrengthAllocation", "FlatTopBoost"):
        config_path = HERE / f"{strategy}.json"
        save(config_path, {**config, "strategy": strategy})
        candidates.append(
            {
                **source,
                "name": strategy,
                "strategy": strategy,
                "config": record(config_path),
                "source_files": {
                    **source["source_files"],
                    "StrengthAllocation.py": record(HERE / "StrengthAllocation.py"),
                },
                "source_and_risk_behavior": "X8 plus first-stake sizing on Top Coins longs.",
            }
        )
    save(
        PLAN,
        {
            **plan,
            "schema": "freqtrade.research.nfi-strength-allocation.v1",
            "registered_at": datetime.now(UTC).isoformat(),
            "parent_plan": record(base_plan),
            "baseline_economic_results_known": True,
            "candidate_economic_results_observed": False,
            "candidates": candidates,
            "total_cost_bps_per_side": [8, 15],
            "hypothesis": (
                "The native Top Coins pullback sleeve earns more per allocated margin when the "
                "asset has established relative strength versus BTC."
            ),
            "sizing": (
                "Pure native Top Coins longs only. Last fully closed UTC daily close divided "
                "by the close 30 days earlier, divided by BTC perpetual gross return. "
                "Ratio >=1.05 sizes 1.75x, ratio <1 sizes 0.65x, otherwise 1x. Unknown is 1x. "
                "FlatTopBoost uses 1.25x on the same sleeve as an aggressive sizing control. "
                "Use native min/max and wallet checks. No change to add, reduce or exit callbacks; "
                "their quantities may depend on the changed first stake."
            ),
            "baselines": {
                str(cost): {
                    name: record(path / name)
                    for name in (
                        "registered_plan.json",
                        "result.json",
                        "hourly_equity.parquet",
                        "independent-account-audit.json",
                    )
                }
                for cost, path in BASELINES.items()
            },
            "decision": (
                "At both costs, higher full-window and both slice net returns than native X8, "
                "higher full-window net return than FlatTopBoost, and hourly mark drawdown no "
                "worse than X8 by more than 1 percentage point. Report uplift separately. "
                "Retrospective screening only. Candidate cases and their failures are retained."
            ),
            "orders_enabled": False,
        },
    )


if __name__ == "__main__":
    main()
