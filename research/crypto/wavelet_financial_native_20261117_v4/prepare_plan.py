"""Register complete native financial accounts before inspecting their returns."""

# ruff: noqa: S101
import copy
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def record(path):
    return {"path": str(path.resolve()), "sha256": sha(path)}


def save(name, obj):
    (HERE / name).write_text(json.dumps(obj, indent=2, allow_nan=False) + "\n")


def prepare_data(base):
    output = ROOT / "user_data/crypto_quant" / HERE.name
    candles = output / "candles"
    candles.mkdir(exist_ok=False)
    feature = HERE.with_name("wavelet_feature_native_20261117_v2")
    for asset in ["BTC", "ETH"]:
        frame = pd.read_parquet(feature / (asset + "_15m.parquet")).reset_index()
        assert len(frame) == 67200 and frame.date.is_monotonic_increasing
        frame.to_feather(candles / (asset + "_USDT-15m.feather"))
        for timeframe in ["5m", "1h"]:
            source = Path(base["datadir"]) / (asset + "_USDT-" + timeframe + ".feather")
            os.link(source, candles / source.name)
    save("data_receipts.json", {p.name: sha(p) for p in sorted(candles.iterdir())})
    return output, candles


def frozen_controls():
    controls = []
    for study, prefixes in [
        ("trendcore_source_native_20261117_v1", ["trendcore_"]),
        ("universal_allocation_native_20261116_v1", ["crp_"]),
        ("nfi_carry_matched_native_20261115_v1", ["nfi_spot_"]),
    ]:
        folder = HERE.with_name(study)
        plan = read(folder / "plan.json")
        for candidate in plan["candidates"]:
            if not any(candidate["name"].startswith(p) for p in prefixes):
                continue
            path = Path(plan["output_root"]) / (candidate["name"] + f"_{candidate['cost_bps']}bps")
            controls.append(
                {
                    "name": candidate["name"],
                    "capital": candidate["capital"],
                    "cost_bps": candidate["cost_bps"],
                    "source_plan": record(folder / "plan.json"),
                    "source_manifest": record(folder / "artifact_manifest.json"),
                    "result": record(path / "result.json"),
                    "hourly_equity": record(path / "hourly_equity.parquet"),
                }
            )
    # Existing benchmark curves are separate frozen whole-account references.
    folder = HERE.with_name("paired_carry_reservation_native_20261115_v2")
    for scenario, cost in [("conservative", 20), ("lower", 13)]:
        path = folder / ("benchmark_1000_" + scenario + ".parquet")
        assert path.is_file()
        controls.append(
            {
                "name": "benchmark_1000_" + scenario,
                "capital": 1000,
                "cost_bps": cost,
                "hourly_equity": record(path),
            }
        )
    return controls


def main():
    assert not (HERE / "plan.json").exists(), "Preserve registered accounts"
    assert read(HERE / "preflight.json")["passed"]
    protocol = read(HERE / "protocol.json")
    for path, digest in read(HERE / "implementation_freeze.json")["files"].items():
        assert sha(path) == digest, path
    baseline = read(protocol["native_template_plan"]["path"])
    assert (
        sha(protocol["native_template_plan"]["path"]) == protocol["native_template_plan"]["sha256"]
    )
    controls = frozen_controls()
    output, candles = prepare_data(baseline)
    plan = copy.deepcopy(baseline)
    plan.update(
        schema="source-wavelet-native-account-v4",
        registered_at=datetime.now(UTC).isoformat(),
        output_root=str(output),
        datadir=str(candles),
        data_receipts=record(HERE / "data_receipts.json"),
        runner_sha256=sha(HERE / "native_runner.py"),
        runner_module="research.crypto.wavelet_financial_native_20261117_v4.native_runner",
        storage_source=record(HERE / "memory_storage.py"),
        memory_guard_bytes=3221225472,
        wall_guard_seconds=14400,
        minimum_free_disk_bytes=1073741824,
        candidates=[],
    )
    policy = HERE.with_name("wavelet_policy_native_20261117_v3")
    seals = [
        HERE / "protocol.json",
        HERE / "implementation_freeze.json",
        HERE / "preflight.json",
        HERE / "CaptureRules.py",
        policy / "artifact_manifest.json",
        policy / "source_policy.py",
        policy / "SourceWaveletCPU.py",
        policy / "source_receipts.json",
        policy / "sources/utils/ExitPolicy.py",
        policy / "sources/utils/custom_indicators.py",
    ]
    plan["additional_source_seals"] = [record(p) for p in seals]
    for capital in [1000, 500]:
        for scenario, cost in [("conservative", 20), ("lower", 13)]:
            name = f"wavelet_{capital}_{scenario}"
            config = read(
                HERE.with_name("trendcore_source_native_20261117_v1")
                / f"trendcore_{capital}_{scenario}.json"
            )
            for key in [
                "stoploss",
                "minimal_roi",
                "trailing_stop",
                "position_adjustment_enable",
                "max_entry_position_adjustment",
                "research_trendcore_nooi",
                "research_matched_nfi",
                "signal_prices",
            ]:
                config.pop(key, None)
            config.update(
                strategy="ResearchWaveletCPU",
                timeframe="15m",
                timeframe_detail="5m",
                bot_name=name,
                research_wavelet_cpu=True,
                research_feature_cache=str(output / "full_native_feature_cache"),
                research_artifact_root=str(output / (name + f"_{cost}bps")),
            )
            save(name + ".json", config)
            candidate = {
                "name": name,
                "strategy": "ResearchWaveletCPU",
                "capital": capital,
                "mode": "spot",
                "scenario": scenario,
                "cost_bps": cost,
                "pairs": ["BTC/USDT", "ETH/USDT"],
                "config": record(HERE / (name + ".json")),
                "source_files": {"ResearchWaveletCPU.py": record(HERE / "ResearchWaveletCPU.py")},
                "market_snapshot": baseline["market_snapshot"],
                "observer_source": baseline["candidates"][0]["observer_source"],
            }
            plan["candidates"].append(candidate)
    save(
        "comparison_contract.json",
        {
            "frozen_at": datetime.now(UTC).isoformat(),
            "new_returns_seen": False,
            "financial_gate": protocol["financial_gate"],
            "window": protocol["financial_window"],
            "controls": controls,
            "portfolios": protocol["portfolio_splits"],
            "no_global_40perp_comparison": True,
            "initial_cash_ramp_preserved": True,
            "selection_not_independent_oos": True,
        },
    )
    plan["additional_source_seals"].append(record(HERE / "comparison_contract.json"))
    save("plan.json", plan)
    print(
        json.dumps(
            {
                "registered_accounts": [c["name"] for c in plan["candidates"]],
                "financial_start_called": False,
                "orders_enabled": False,
            }
        )
    )


if __name__ == "__main__":
    main()
