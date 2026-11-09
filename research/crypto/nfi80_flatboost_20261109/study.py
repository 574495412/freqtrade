"""Fixed FlatTopBoost comparison through the previously validated native runner."""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from research.crypto.nfi80_capacity_20261109.study import read, ref, save, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / "research/crypto/nfi80_capacity_20261109"
OUT = ROOT / "user_data/crypto_quant/nfi80_flatboost_20261109_v001"
PROTOCOL = HERE / "protocol.json"
CASES = ("L3_S6", "L3_S12")
RUNNER = BASE / "native_runner.py"
WRAPPER = ROOT / "research/crypto/nfi_strength_allocation_20261107/StrengthAllocation.py"


def freeze():
    if PROTOCOL.exists() or OUT.exists():
        raise FileExistsError("Preserve already registered experiment")
    parent = read(BASE / "protocol.json")
    prior = read(ROOT / "research/crypto/selection_correction_20261109.json")
    OUT.mkdir(parents=True)
    plans = {}
    for name in CASES:
        plan = read(BASE / f"{name}-v003.json")
        config = read(BASE / f"{name}-config.json")
        config["strategy"] = "FlatTopBoost"
        config["bot_name"] = "research-nfi80-flatboost-" + name
        config_path = HERE / f"{name}-config.json"
        save(config_path, config)
        candidate = plan["candidates"][0]
        candidate["strategy"] = "FlatTopBoost"
        candidate["config"] = ref(config_path)
        candidate["source_files"]["StrengthAllocation.py"] = ref(WRAPPER)
        candidate["source_and_risk_behavior"] = (
            "Existing local FlatTopBoost: pure Top Coins long initial stake x1.25; "
            "native entry/exit/DCA/leverage callbacks otherwise inherited unchanged."
        )
        plan.update(
            schema="freqtrade.research.nfi80-flatboost-native.v1",
            registered_at=datetime.now(UTC).isoformat(),
            comparison_purpose=(
                "Fixed existing FlatTopBoost against known original 80-pair accounts"
            ),
            parent_plan=ref(BASE / f"{name}-v003.json"),
            economic_results_observed_at_registration=False,
            original_control_economics_known=True,
            previous_flatboost_contract_economics_known=True,
            technical_only_revision=False,
            financial_hypothesis_unchanged=False,
            parameter_experiment="Previously frozen x1.25 initial stake; no new multiplier search",
            registration_note="New comparison contract; prior failures remain failed",
        )
        plans[name] = HERE / f"{name}-plan.json"
        save(plans[name], plan)
    save(
        PROTOCOL,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "hypothesis": (
                "Existing fixed x1.25 Top Coins initial stake may improve original net returns"
            ),
            "variant": "FlatTopBoost, unchanged local allocation extension; not upstream NFI logic",
            "owner_workflow": (
                "Native Freqtrade Backtesting and supported custom_stake_amount callback"
            ),
            "engine_commit": read(plans[CASES[0]])["engine_commit"],
            "upstream_nfi_commit": "1af55971d3a64703ec449078e09fdec257d0d3c6",
            "parent_protocol": ref(BASE / "protocol.json"),
            "baseline_comparison": ref(BASE / "comparison.json"),
            "previous_ranking_and_failure": ref(
                ROOT / "research/crypto/selection_correction_20261109.json"
            ),
            "previous_best": prior["higher_historical_return_candidate"],
            "old_2024_incremental_drawdown_failure_remains_failed": True,
            "plans": {k: ref(v) for k, v in plans.items()},
            "source": ref(Path(__file__)),
            "runner": ref(RUNNER),
            "wrapper": ref(WRAPPER),
            "cases": list(CASES),
            "costs_bps_per_side": [15, 8],
            "start": parent["start"],
            "comparison_end": parent["comparison_end"],
            "pairs": parent["pairs"],
            "initial_equity_usdt": 1000,
            "research_drawdown_limit": 0.50,
            "selection_rule": (
                "Report all outcomes; rank net returns per cost with <=50% 5m DD and "
                "zero modeled liquidations; no automatic production promotion"
            ),
            "frozen_multiplier": 1.25,
            "no_parameter_retuning": True,
            "maximum_financial_workers": 1,
            "limitations": parent["limitations"],
            "baseline_economics_known": True,
            "new_variant_results_known": False,
            "orders_enabled": False,
            "production_eligible": False,
        },
    )


def checked_hash(item):
    if sha(Path(item["path"])) != item["sha256"]:
        raise ValueError("Frozen orchestration/source/plan changed")


def run():
    protocol = read(PROTOCOL)
    for item in (protocol["source"], protocol["runner"], protocol["wrapper"]):
        checked_hash(item)
    completed = []
    for cost in protocol["costs_bps_per_side"]:
        for name in protocol["cases"]:
            plan = protocol["plans"][name]
            checked_hash(plan)
            key = f"{name}_{cost}bps"
            folder = OUT / key
            if not (folder / "result.json").exists():
                if folder.exists():
                    raise FileExistsError("Retain failed/partial native account")
                if shutil.disk_usage(OUT).free < 5 * 1024**3:
                    raise OSError("Insufficient disk for native replay")
                with (OUT / (key + ".log")).open("x") as log:
                    child = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            str(RUNNER),
                            "--plan",
                            plan["path"],
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
                        "returncode": child.returncode,
                        "finished_at_utc": datetime.now(UTC).isoformat(),
                        "orders_enabled": False,
                    },
                )
                if child.returncode:
                    raise RuntimeError(f"Native account failed: {key}")
            if read(folder / "completion.json")["returncode"] != 0:
                raise ValueError("Cannot accept nonzero native exit")
            if not read(folder / "fill_cash_audit.json")["passed"]:
                raise ValueError("Native fills failed cash reconciliation")
            result = read(folder / "result.json")
            completed.append(key)
            save(OUT / "progress.json", {"completed": completed, "total": 4})
            cache = folder / "lossless_cache"
            if cache.is_dir() and not cache.is_symlink():
                size = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
                shutil.rmtree(cache)
                save(
                    folder / "generated-cache-cleanup.json",
                    {
                        "bytes_removed": size,
                        "scope": "Only successful account generated cache; all evidence retained",
                    },
                )
            print(
                json.dumps(
                    {
                        "completed": key,
                        "net_return": result["periods"][0]["net_return"],
                        "seconds": result["elapsed_seconds"],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "run"])
    globals()[parser.parse_args().action]()
