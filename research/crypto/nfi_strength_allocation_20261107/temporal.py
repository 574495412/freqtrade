"""Run the unchanged fixed sizing control in a separate native-account window."""

import math
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read, size_audit
from research.crypto.nfi_strength_allocation_20261107.register import (
    DATA,
    HERE,
    PARENT,
    PLAN,
    record,
)
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save


REGISTRATION = HERE / "temporal_registration.json"
OUT = DATA / "nfi_flat_temporal_20261107_v001"
SOURCE = PARENT / "plan-post-source.json"
RUNNER = PARENT / "run_wide_comparison.py"
START, END = "2026-09-01T00:00:00Z", "2026-11-05T00:00:00Z"
BOUNDARY, LAST = "2026-10-23T00:00:00Z", "2026-11-04T23:00:00Z"


def register():
    if REGISTRATION.exists() or OUT.exists():
        raise FileExistsError("Fixed allocation temporal comparison already registered")
    plan = read(SOURCE)
    previous = read(PLAN)
    flat = next(c for c in previous["candidates"] if c["name"] == "FlatTopBoost")
    (base,) = plan["candidates"]
    if plan["pairs"] != previous["pairs"] or plan["engine_commit"] != previous["engine_commit"]:
        raise ValueError("Source account pair ordering or engine changed")
    baseline_config = read(checked(base["config"]))
    flat_config = read(checked(flat["config"]))
    if {**flat_config, "strategy": base["strategy"]} != baseline_config:
        raise ValueError("Flat candidate changed the native contract")
    save(
        REGISTRATION,
        {
            **plan,
            "schema": "freqtrade.research.nfi-fixed-allocation-temporal.v1",
            "registered_at": datetime.now(UTC).isoformat(),
            "candidate_window_economics_observed": False,
            "known_history": (
                "Both 2025–August 2026 accounts were known, along with the 13-day original "
                "X8 base-cost account. Full September-to-November paired economics were unknown. "
                "Retrospective and reset to cash."
            ),
            "start": START,
            "end": END,
            "comparison_end": LAST,
            "candidates": [base, flat],
            "total_cost_bps_per_side": [8, 15],
            "periods": [
                {"name": "continuous", "start": START, "end": LAST},
                {"name": "before-source-release-cutoff", "start": START, "end": BOUNDARY},
                {"name": "after-source-release-cutoff", "start": BOUNDARY, "end": LAST},
            ],
            "temporal_sources": [
                record(SOURCE),
                record(PLAN),
                record(HERE / "result.json"),
                record(HERE / "audit.py"),
                record(Path(__file__)),
            ],
            "decision": (
                "At each cost require positive full-window flat net return greater than original, "
                "positive paired excess in both slices, full-window drawdown within one percentage "
                "point of original, at least 20 natural closes full-window and 10 filled pure Top "
                "Coins campaigns. Record research-only and preserve A04 outcomes."
            ),
            "orders_enabled": False,
        },
    )


def audit_one(plan, case, cost, directory):
    if read(directory / "registered_plan.json") != plan:
        raise ValueError("Native account plan differs")
    for name, source in case["source_files"].items():
        checked({**source, "path": str(directory / "strategies" / name)})
    trades, summary = native_trades(plan, directory, case["strategy"], cost)
    if case["name"] == "FlatTopBoost":
        summary.update(size_audit(plan, directory, case["name"], trades))
    result = read(directory / "result.json")
    curve = pd.read_parquet(directory / "hourly_equity.parquet").set_index("date")
    for window in result["periods"]:
        start, end = (pd.Timestamp(window[key]) for key in ("start", "end"))
        nav = curve.loc[start:end, "hourly_mark_equity"]
        if not nav.index.equals(pd.date_range(start, end, freq="h")) or (nav <= 0).any():
            raise ValueError("Native marked account has missing or invalid clocks")
        net = float(nav.iloc[-1] / nav.iloc[0] - 1)
        dd = float((1 - nav / nav.cummax()).max())
        if not (
            math.isclose(net, window["net_return"], abs_tol=1e-10)
            and math.isclose(dd, window["hourly_mark_drawdown"], abs_tol=1e-10)
        ):
            raise ValueError("Economic result disagrees with audited NAV")
    summary["periods"] = {p["name"]: p for p in result["periods"]}
    summary["native_result"] = record(directory / "result.json")
    summary["hourly_equity"] = record(directory / "hourly_equity.parquet")
    return summary


def run():
    plan = read(REGISTRATION)
    for source in plan["temporal_sources"]:
        checked(source)
    OUT.mkdir(exist_ok=True)
    reports = {}
    for cost in (15, 8):
        for case in plan["candidates"]:
            key = f"{case['name']}_{cost}bps"
            directory = OUT / key
            if not directory.exists():
                with (OUT / f"{key}.log").open("x") as log:
                    subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            str(RUNNER),
                            "--plan",
                            str(REGISTRATION),
                            "--candidate",
                            case["name"],
                            "--cost-bps",
                            str(cost),
                            "--output",
                            str(directory),
                        ],
                        check=True,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
            reports[key] = audit_one(plan, case, cost, directory)
            save(directory / "temporal-audit.json", reports[key])
            cache = directory / "lossless_cache"
            if cache.is_dir() and not cache.is_symlink():
                shutil.rmtree(cache)
            print(key, reports[key]["periods"]["continuous"]["net_return"], flush=True)
    failed = {}
    for cost in (15, 8):
        original = reports[f"nfi-x8-1x_{cost}bps"]["periods"]
        fixed = reports[f"FlatTopBoost_{cost}bps"]
        full = fixed["periods"]["continuous"]
        checks = {
            "positive_full": full["net_return"] > 0,
            "paired_excess": all(
                w["net_return"] > original[name]["net_return"]
                for name, w in fixed["periods"].items()
            ),
            "drawdown_budget": (
                full["hourly_mark_drawdown"] - original["continuous"]["hourly_mark_drawdown"]
                <= 0.01
            ),
            "sample": full["completed_campaigns"] >= 20
            and fixed["matched_filled_top_coin_campaigns"] >= 10,
        }
        failed[str(cost)] = [name for name, passed in checks.items() if not passed]
    save(
        HERE / "temporal_result.json",
        {
            "registration": record(REGISTRATION),
            "accounts": reports,
            "failed_gates": failed,
            "historical_screen_passed": not any(failed.values()),
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
