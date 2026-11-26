"""Register a native OI attribution matrix before any financial outcome."""

import json
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/oi_cascade_source_native_20261113_v1"


def ref(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    if (HERE / "plan.json").exists():
        raise FileExistsError("Do not overwrite registration")
    if not json.loads((HERE / "input_admission.json").read_text())["passed"]:
        raise ValueError("No admitted OI input")
    parent = json.loads(
        (ROOT / "research/crypto/skew_vol_native_20261113_v1/v002/plan.json").read_text()
    )
    keys = [
        "engine_source",
        "engine_manifest",
        "engine_commit",
        "environment",
        "market_snapshot",
        "research_support",
        "storage_source",
        "observer_source",
        "start",
        "end",
        "comparison_end",
        "periods",
    ]
    plan = {key: parent[key] for key in keys}
    pairs = ["BTC/USDT:USDT"]
    source_files = {
        name: ref(HERE / name) for name in ["OpenInterestReference.py", "OIConfirmation.py"]
    }
    source = HERE / "source/freqtrade-open-interest/example_strategy.py"
    if sha(source) != sha(HERE / "OpenInterestReference.py"):
        raise ValueError("Reference must remain byte-identical")
    candidates = []
    for strategy in ["OIConfirmedBreakout", "PriceBreakoutAvailable", "PriceBreakoutFull"]:
        config = json.loads(Path(parent["candidates"][0]["config"]["path"]).read_text())
        config = {key: value for key, value in config.items() if not key.startswith("research_")}
        config.update(strategy=strategy, dry_run_wallet=1000, max_open_trades=1)
        config["exchange"]["pair_whitelist"] = pairs
        path = HERE / (strategy + ".json")
        save(path, config)
        candidates.append(
            {
                "name": strategy,
                "strategy": strategy,
                "capital": 1000,
                "config": ref(path),
                "source_files": source_files,
            }
        )
    plan.update(
        schema="freqtrade.native.oi-confirmation.v1",
        registered_at=datetime.now(UTC).isoformat(),
        output_root=str(OUT),
        datadir=str(OUT / "candles"),
        data_receipts=ref(OUT / "data_receipts.json"),
        pairs=pairs,
        candidates=candidates,
        marks={pairs[0]: parent["marks"][pairs[0]]},
        initial_equity_usdt=1000,
        allowed_leverage=1.0,
        total_cost_bps_per_side=[15, 8],
        runner_sha256=sha(HERE / "native_runner.py"),
        memory_guard_bytes=3 * 1024**3,
        wall_guard_seconds=3600,
        additional_source_seals=[
            ref(path)
            for path in [
                ROOT / "research/crypto/band_trend_native_20261113_v1/observe_clock.py",
                ROOT / "research/crypto/skew_vol_native_20261113_v1/clock_fix.py",
                HERE / "protocol.json",
                HERE / "input_admission.json",
                HERE / "run.py",
            ]
        ],
        source_commit="37a8ed8682b79ba29b6bfbc67fff2a696e388826",
        economic_parameter_changes_to_reference=False,
        missingness_adaptation="ffill=False; six consecutive actual hourly base-OI values required. Controls isolate mask and OI effect.",
        new_effective_alpha_claim=False,
        orders_enabled=False,
        production_eligible=False,
    )
    save(HERE / "plan.json", plan)
    print(
        json.dumps(
            {
                "registered_accounts": 6,
                "input_contract": "native OPEN_INTEREST",
                "orders_enabled": False,
            }
        )
    )


if __name__ == "__main__":
    main()
