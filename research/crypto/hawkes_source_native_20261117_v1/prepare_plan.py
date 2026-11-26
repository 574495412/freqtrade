"""Register actual native wallets and source adaptations before economics."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = HERE.with_name("nfi_carry_matched_native_20261115_v1")
WAVE = HERE.with_name("wavelet_financial_native_20261117_v4")
FUNDING = HERE.with_name("session_funding_native_20261114_v3")


def record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    assert not (HERE / "plan.json").exists()
    plan = json.loads((BASE / "plan.json").read_text())
    originals = [c for c in plan["candidates"] if c["mode"] == "futures"]
    plan.update(schema="hawkes-native-source-v1", registered_at=datetime.now(UTC).isoformat(),
                output_root=str(ROOT / "user_data/crypto_quant" / HERE.name),
                native_start="2024-12-31T23:00:00Z", minimum_free_disk_bytes=1073741824,
                runner_module="research.crypto.hawkes_source_native_20261117_v1.native_runner",
                runner_sha256=record(HERE / "native_runner.py")["sha256"],
                storage_source=record(WAVE / "memory_storage.py"),
                price_precision_source=record(HERE / "price_precision.py"),
                funding_extension=record(FUNDING / "FundingOwnershipBacktesting.py"),
                pairs=["BTC/USDT:USDT", "ETH/USDT:USDT"],
                total_cost_bps_per_side=[15, 8])
    plan["marks"] = {k: v for k, v in plan["marks"].items() if k in plan["pairs"]}
    plan["candidates"] = []
    for policy in ["before_orders", "after_orders"]:
        for original in originals:
            row = copy.deepcopy(original)
            row.update(name=f"hawkes_{policy}_{row['capital']}_{row['scenario']}",
                       strategy="HawkesStrategy", funding_ownership=policy)
            config = json.loads(Path(row["config"]["path"]).read_text())
            for key in ["nfi_parameters", "grinding_enable", "research_matched_nfi"]:
                config.pop(key, None)
            config.update(strategy="HawkesStrategy", bot_name=row["name"], timeframe="1h",
                          timeframe_detail="5m", research_hawkes=True,
                          research_funding_ownership=policy)
            for key in ["entry", "exit", "emergency_exit", "force_entry", "force_exit", "stoploss"]:
                config["order_types"][key] = "market"
            for key in ["entry_pricing", "exit_pricing"]:
                config[key]["use_order_book"] = False
            path = HERE / (row["name"] + ".json")
            path.write_text(json.dumps(config, indent=2) + "\n")
            row["config"] = record(path)
            row["source_files"] = {
                "HawkesStrategy.py": record(HERE / "HawkesStrategy.py"),
                "HawkesInputs.py": record(HERE / "HawkesInputs.py"),
                "source_hawkes.txt": record(HERE / "source/hawkes.py"),
                "SOURCE_LICENSE.txt": record(HERE / "source/LICENSE"),
            }
            plan["candidates"].append(row)
    plan["market_snapshot"] = originals[0]["market_snapshot"]
    plan["observer_source"] = originals[0]["observer_source"]
    extras = [HERE / n for n in ["protocol.json", "source_receipts.json", "HawkesInputs.py",
              "audit_accounts.py", "analyze.py", "preflight.py", "execute.py", "prepare_plan.py",
              "test_inputs.py", "test_funding_audit.py", "feature_validation_v2.json"]]
    extras += [WAVE / "CaptureRules.py", FUNDING / "FundingOwnershipBacktesting.py",
               ROOT / "freqtrade/optimize/matched_price_precision.py"]
    plan["additional_source_seals"] = [record(p) for p in extras]
    Path(plan["output_root"]).mkdir(exist_ok=False)
    (HERE / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    paths = [HERE / "plan.json", HERE / "native_runner.py", HERE / "price_precision.py", *extras]
    paths += [Path(c["config"]["path"]) for c in plan["candidates"]]
    paths += [Path(v["path"]) for c in plan["candidates"] for v in c["source_files"].values()]
    frozen = {str(p): record(p)["sha256"] for p in paths}
    (HERE / "implementation_freeze.json").write_text(json.dumps({
        "frozen_at": datetime.now(UTC).isoformat(), "financial_returns_seen": False,
        "files": frozen}, indent=2) + "\n")
    print("Registered8nativewallets; no financial starts")


if __name__ == "__main__":
    main()
