"""Freeze four native wallets and matched controls before local outcomes."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
PARENT = HERE.with_name("universal_allocation_native_20261116_v1")


def record(p):
    p = Path(p)
    return dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def main():
    assert not (HERE / "plan.json").exists(), "Preserve existing registration"
    inputs = json.loads((HERE / "input_receipts.json").read_text())
    assert record(inputs["signal_prices"]["path"])["sha256"] == inputs["signal_prices"]["sha256"]
    plan = json.loads((PARENT / "plan.json").read_text())
    control = next(c for c in plan["candidates"] if c["mode"] == "spot")
    output = ROOT / "user_data/crypto_quant" / HERE.name
    plan.update(
        registered_at=datetime.now(UTC).isoformat(),
        schema="trendcore-nooi-native-v1",
        output_root=str(output),
        datadir=str(output / "candles"),
        runner_module="research.crypto." + HERE.name + ".native_runner",
        runner_sha256=record(HERE / "native_runner.py")["sha256"],
        data_receipts=record(HERE / "data_receipts.json"),
    )
    plan["candidates"] = []
    sources = {
        "TrendCoreSpot.py": record(HERE / "TrendCoreSpot.py"),
        "TrendCoreSignals.py": record(ROOT / "freqtrade/data/trendcore_signals.py"),
        "NativeAllocations.py": record(HERE / "NativeAllocations.py"),
    }
    for capital in [1000, 500]:
        for scenario, cost in [("conservative", 20), ("lower", 13)]:
            name = f"trendcore_{capital}_{scenario}"
            config = json.loads(Path(control["config"]["path"]).read_text())
            for key in [
                "research_universal_allocation",
                "allocation_start_day",
                "allocation_end_day",
            ]:
                config.pop(key, None)
            config.update(
                strategy="TrendCoreSpot",
                dry_run_wallet=capital,
                fee=cost / 10000,
                bot_name=name,
                research_trendcore_nooi=True,
                signal_prices=inputs["signal_prices"]["path"],
            )
            path = HERE / (name + ".json")
            path.write_text(json.dumps(config, indent=2) + "\n")
            row = copy.deepcopy(control)
            row.update(
                name=name,
                strategy="TrendCoreSpot",
                capital=capital,
                scenario=scenario,
                cost_bps=cost,
                config=record(path),
                source_files=sources,
            )
            plan["candidates"].append(row)
    extra = [
        HERE / n
        for n in [
            "protocol.json",
            "variant_freeze.json",
            "input_receipts.json",
            "warmup_contract_amendment.json",
            "warmup_gap_review.json",
            "warmup_gap_followup.json",
            "selected_source_receipts.json",
            "financial_gate_freeze.json",
            "test_signals.py",
            "run.py",
            "preflight.py",
            "CaptureRules.py",
            "prepare_plan.py",
        ]
    ]
    extra += [
        Path(inputs["signal_prices"]["path"]),
        ROOT / "research/crypto/band_trend_native_20261113_v1/observe_clock.py",
        ROOT / "research/crypto/skew_vol_native_20261113_v1/clock_fix.py",
    ]
    extra += [
        Path(r["local_path"])
        for r in json.loads((HERE / "selected_source_receipts.json").read_text())["records"]
    ]
    plan["additional_source_seals"] = [record(p) for p in extra]
    plan["orders_enabled"] = plan["production_eligible"] = False
    (HERE / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    (HERE / "implementation_freeze.json").write_text(
        json.dumps(
            {
                str(p): record(p)["sha256"]
                for p in [
                    HERE / "plan.json",
                    HERE / "native_runner.py",
                    *extra,
                    ROOT / "freqtrade/data/trendcore_signals.py",
                    HERE / "TrendCoreSpot.py",
                    HERE / "NativeAllocations.py",
                ]
            },
            indent=2,
        )
        + "\n"
    )
    print("Frozen4actualnativewallets;0financialstarts")


if __name__ == "__main__":
    main()
