# ruff: noqa: E501, C901
"""Register and execute the actual native historical-universe accounts serially."""

import argparse
import shutil
import subprocess
import sys
from datetime import UTC, datetime

from research.crypto.nfi80_historical_universe_20261109.prepare import (
    HERE,
    OUT,
    PLAN,
    ROOT,
    read,
    ref,
    save,
    sha,
)


def register():
    if (HERE / "plans.json").exists():
        raise FileExistsError("Preserve registered economic contracts")
    selection = read(OUT / "schedule.json")
    admission = read(OUT / "admission.json")
    if set(admission) != set(selection["union"]):
        raise ValueError("Execution data admission incomplete")
    current_inputs = read(OUT / "data-receipts.json")
    old_inputs = read(PLAN["data_receipts"]["path"])
    if any(current_inputs.get(name) != digest for name, digest in old_inputs.items()):
        raise ValueError("Shared fixed80 execution data differs from the frozen baseline")
    summary = read(HERE / "schedule-summary.json")
    if summary["initial_pairs"] != PLAN["pairs"]:
        raise ValueError("Protocol requires additional static control if initial basket differs")
    save(
        HERE / "input-protocol.json",
        {
            "schema": "freqtrade.research.historical-universe-inputs.v1",
            "start": PLAN["start"],
            "end": PLAN["end"],
            "comparison_end": PLAN["comparison_end"],
            "source_datadir": read(ROOT / "research/crypto/nfi80_capacity_20261109/protocol.json")[
                "source_datadir"
            ],
            "pairs": selection["union"],
            "research_drawdown_limit": 0.30,
            "purpose": "Public input preparation and audited observation metrics only",
            "historical_protocol": ref(HERE / "PROTOCOL.md"),
        },
    )
    markets = read(PLAN["original_full_market_snapshot"]["path"])
    selected = selection["union"]
    markets.update(
        markets={p: markets["markets"][p] for p in selected},
        leverage_tiers={p: markets["leverage_tiers"][p] for p in selected},
    )
    save(OUT / "market-rules.json", markets)
    plans = {}
    for name, parent in [
        ("original", ROOT / "research/crypto/nfi80_capacity_20261109/L3_S12-v003.json"),
        ("flatboost", ROOT / "research/crypto/nfi80_flatboost_20261109/L3_S12-plan.json"),
    ]:
        plan = read(parent)
        candidate = plan["candidates"][0]
        config = read(candidate["config"]["path"])
        config["exchange"]["pair_whitelist"] = selected
        config["enable_dynamic_pairlist"] = True
        config["pairlists"] = [
            {
                "method": "HistoricalVolumePairList",
                "schedule": str(OUT / "schedule.json"),
                "sha256": sha(OUT / "schedule.json"),
            }
        ]
        config["bot_name"] = "historical-universe-" + name
        save(HERE / (name + "-config.json"), config)
        candidate.update(name=name, config=ref(HERE / (name + "-config.json")))
        plan.update(
            schema="freqtrade.research.historical-universe.v1",
            registered_at=datetime.now(UTC).isoformat(),
            engine_source=str(OUT / "engine"),
            engine_manifest=ref(HERE / "engine-manifest.json"),
            historical_schedule=ref(OUT / "schedule.json"),
            extension_patch=ref(HERE / "native.patch"),
            row_storage=ref(HERE / "row_storage.py"),
            pairs=selected,
            datadir=str(OUT / "candles"),
            data_receipts=ref(OUT / "data-receipts.json"),
            market_snapshot=ref(OUT / "market-rules.json"),
            marks={
                p: ref(
                    OUT
                    / "candles/futures"
                    / (p.replace("/", "_").replace(":", "_") + "-1h-mark.feather")
                )
                for p in selected
            },
            runner_sha256=sha(HERE / "native_runner.py"),
            parent_plan=ref(parent),
            historical_universe_protocol=ref(HERE / "PROTOCOL.md"),
            research_drawdown_limit=0.30,
            initial_basket_matches_legacy_control=True,
            legacy_static_results_known=True,
            economic_results_observed_at_registration=False,
            local_extension_explicit=True,
            selection_protocol=ref(HERE / "PROTOCOL.md"),
            comparison_purpose="Daily prior-day historical top80 versus identical frozen-start top80",
            orders_enabled=False,
            production_eligible=False,
            known_limitations=plan["known_limitations"]
            + [
                "Local historical pairlist/clock extension, not an upstream VolumePairList backtest",
                "Current author membership/blacklist/metadata remain retrospective; not PIT or independent OOS",
                "Daily UTC prior-day exact turnover differs from stopped forward rolling-ticker refresh clock",
            ],
        )
        save(HERE / (name + "-plan.json"), plan)
        plans[name] = ref(HERE / (name + "-plan.json"))
    parity = read(HERE / "flatboost-plan.json")
    parity["pairs"] = PLAN["pairs"]
    parity["marks"] = {p: parity["marks"][p] for p in PLAN["pairs"]}
    parity["dynamic_universe"] = False
    parity["comparison_purpose"] = (
        "Technical exact parity of lossless native rows against known fixed80 account"
    )
    parity_config = read(ROOT / "research/crypto/nfi80_flatboost_20261109/L3_S12-config.json")
    save(HERE / "parity-config.json", parity_config)
    parity["candidates"][0].update(name="parity", config=ref(HERE / "parity-config.json"))
    if (HERE / "parity-technical-plan.json").exists():
        parity_path = HERE / "parity-technical-plan.json"
    else:
        parity_path = HERE / "parity-plan.json"
        save(parity_path, parity)
    plans = {"parity": ref(parity_path), **plans}
    save(
        HERE / "plans.json",
        {
            "plans": plans,
            "protocol": ref(HERE / "PROTOCOL.md"),
            "runner": ref(HERE / "native_runner.py"),
            "costs": [15, 8],
            "registered_at": datetime.now(UTC).isoformat(),
            "sources": {p.name: ref(p) for p in HERE.glob("*.py")},
            "orders_enabled": False,
        },
    )
    print({"registered": list(plans), "native_accounts": 5, "union": len(selected)}, flush=True)


def run():
    protocol = read(HERE / "plans.json")
    for item in [protocol["runner"], protocol["protocol"], *protocol["sources"].values()]:
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Frozen research source differs: " + item["path"])
    completed = []
    for cost in protocol["costs"]:
        for name, item in protocol["plans"].items():
            if name == "parity" and cost != 15:
                continue
            if sha(item["path"]) != item["sha256"]:
                raise ValueError("Frozen plan differs")
            key = f"{name}_{cost}bps"
            folder = OUT / key
            if not (folder / "result.json").exists():
                if folder.exists():
                    raise FileExistsError("Preserve failed native run " + str(folder))
                if shutil.disk_usage(OUT).free < 4 * 1024**3:
                    raise OSError("Insufficient free disk for native account")
                with (OUT / (key + ".log")).open("x") as log:
                    process = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            str(HERE / "native_runner.py"),
                            "--plan",
                            item["path"],
                            "--candidate",
                            name,
                            "--cost-bps",
                            str(cost),
                            "--output",
                            str(folder),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                save(
                    folder / "completion.json",
                    {
                        "returncode": process.returncode,
                        "finished_at_utc": datetime.now(UTC).isoformat(),
                        "orders_enabled": False,
                    },
                )
                if process.returncode:
                    raise RuntimeError("Native account failed " + key)
            if (
                read(folder / "completion.json")["returncode"] != 0
                or not read(folder / "fill_cash_audit.json")["passed"]
            ):
                raise ValueError("Native account exit/cash audit failed")
            result = read(folder / "result.json")
            if name == "parity":
                import json
                from zipfile import ZipFile

                import pandas as pd

                old = ROOT / "user_data/crypto_quant/nfi80_flatboost_20261109_v001/L3_S12_15bps"
                before = pd.read_parquet(old / "five_minute_equity.parquet")
                after = pd.read_parquet(folder / "five_minute_equity.parquet")
                columns = [
                    "date",
                    "equity",
                    "marked_equity",
                    "closed_realized_pnl",
                    "open_trades",
                    "gross",
                ]
                pd.testing.assert_frame_equal(before[columns], after[columns], check_exact=True)

                def trades(path):
                    archive = next((path / "native_results").glob("*.zip"))
                    with ZipFile(archive) as z:
                        filename = next(
                            n
                            for n in z.namelist()
                            if n.endswith(".json") and not n.endswith("_config.json")
                        )
                        return json.loads(z.read(filename))["strategy"]["FlatTopBoost"]["trades"]

                left, right = trades(old), trades(folder)
                if left != right:
                    raise ValueError("Lossless row parity native trade fields differ")
                save(
                    HERE / "parity-result.json",
                    {
                        "passed": True,
                        "exact_equity_rows": len(before),
                        "exact_native_trade_records": len(left),
                        "maximum_equity_difference": 0,
                        "new_result": ref(folder / "result.json"),
                        "old_result": ref(old / "result.json"),
                    },
                )
            completed.append(key)
            save(OUT / "run-progress.json", {"completed": completed, "total": 5})
            cache = folder / "lossless_cache"
            if cache.is_dir() and not cache.is_symlink():
                size = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
                shutil.rmtree(cache)
                save(
                    folder / "generated-cache-cleanup.json",
                    {
                        "bytes_removed": size,
                        "scope": "Only successful generated cache; all financial evidence retained",
                    },
                )
            print(
                {
                    "completed": key,
                    "periods": result["periods"],
                    "seconds": result["elapsed_seconds"],
                },
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["register", "run"])
    globals()[parser.parse_args().stage]()
