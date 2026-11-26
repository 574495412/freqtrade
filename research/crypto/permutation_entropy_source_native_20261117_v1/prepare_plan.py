"""Freeze native accounts using the unchanged sealed TrendCore native runner."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = HERE.with_name("trendcore_source_native_20261117_v1")


def record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    if (HERE / "plan.json").exists():
        raise ValueError("Preserve frozen registration")
    plan = json.loads((OLD / "plan.json").read_text())
    original = plan["candidates"]
    plan.update(
        schema="local-entropy-trendcore-native-v1",
        registered_at=datetime.now(UTC).isoformat(),
        output_root=str(ROOT / "user_data/crypto_quant" / HERE.name),
    )
    plan["candidates"] = []
    for original_candidate in original:
        row = copy.deepcopy(original_candidate)
        row["name"] = row["name"].replace("trendcore_", "entropytrend_", 1)
        row["strategy"] = "EntropyTrendCoreSpot"
        config = json.loads(Path(row["config"]["path"]).read_text())
        config.update(bot_name=row["name"], strategy=row["strategy"], research_ordinal_entropy=True)
        path = HERE / (row["name"] + ".json")
        path.write_text(json.dumps(config, indent=2) + "\n")
        row["config"] = record(path)
        row["source_files"].update(
            {
                "EntropyTrendCoreSpot.py": record(HERE / "EntropyTrendCoreSpot.py"),
                "OrdinalEntropy.py": record(ROOT / "freqtrade/data/ordinal_entropy.py"),
            }
        )
        plan["candidates"].append(row)
    extras = [
        HERE / name
        for name in [
            "protocol.json",
            "source_receipts.json",
            "source/perm_entropy.py",
            "source/LICENSE",
            "test_features.py",
            "run.py",
            "preflight.py",
            "prepare_plan.py",
            "analyze.py",
            "budget_audit.py",
        ]
    ]
    extras += [OLD / "preflight.py", OLD / "audit_accounts.py"]
    plan["additional_source_seals"] += [record(path) for path in extras]
    Path(plan["output_root"]).mkdir(exist_ok=False)
    (HERE / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    frozen = [HERE / "plan.json", *extras, Path(plan["engine_manifest"]["path"])]
    frozen += [Path(r["path"]) for c in plan["candidates"] for r in c["source_files"].values()]
    frozen += [Path(c["config"]["path"]) for c in plan["candidates"]]
    (HERE / "implementation_freeze.json").write_text(
        json.dumps({str(path.resolve()): record(path)["sha256"] for path in frozen}, indent=2)
        + "\n"
    )
    print("Registered4nativewallets; existing official runner unchanged;0financialstarts")


if __name__ == "__main__":
    main()
