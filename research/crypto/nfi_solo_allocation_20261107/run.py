"""Run and reconcile a registered account-inventory allocation against X8 controls."""

import math
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.nfi_strength_allocation_20261107.audit import TOP_COINS, read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, PARENT, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
OUT = DATA / "nfi_solo_allocation_20261107_v001"
A04 = HERE.parent / "nfi_strength_allocation_20261107"
REGISTRATION = HERE / "protocol.json"
SHORT_PLAN = HERE / "temporal_native.json"
LONG_PLAN = HERE / "long_native.json"
RESULT = HERE / "result.json"
STRATEGY = "SoloTopBoost"


def register():
    if REGISTRATION.exists() or OUT.exists():
        raise FileExistsError("A08 already registered")
    old_short, old_long = read(A04 / "temporal_registration.json"), read(A04 / "registration.json")
    prior = next(c for c in old_short["candidates"] if c["name"] == "FlatTopBoost")
    config = read(checked(prior["config"]))
    config["strategy"] = STRATEGY
    config_path = HERE / "config.json"
    save(config_path, config)
    candidate = {
        **prior,
        "name": STRATEGY,
        "strategy": STRATEGY,
        "config": record(config_path),
        "source_files": {
            **prior["source_files"],
            "SoloTopBoost.py": record(HERE / "SoloTopBoost.py"),
        },
        "source_and_risk_behavior": (
            "Unmodified X8 with first-stake boost conditional on current long inventory."
        ),
    }
    for parent, target in ((old_short, SHORT_PLAN), (old_long, LONG_PLAN)):
        save(
            target,
            {
                **parent,
                "schema": "freqtrade.research.nfi-inventory-allocation.v1",
                "registered_at": datetime.now(UTC).isoformat(),
                "candidates": [candidate],
                "candidate_economics_observed": False,
                "total_cost_bps_per_side": [8, 15],
                "orders_enabled": False,
            },
        )
    sources = (
        Path(__file__),
        HERE / "SoloTopBoost.py",
        SHORT_PLAN,
        LONG_PLAN,
        A04 / "registration.json",
        A04 / "result.json",
        A04 / "temporal_registration.json",
        A04 / "temporal_result.json",
        A04 / "mark_result.json",
        Path(temporal.__file__),
        Path(mark_valuation.__file__),
        mark_valuation.ORACLE,
        A04 / "audit.py",
    )
    save(
        REGISTRATION,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "sources": [record(path) for path in sources],
            "hypothesis": (
                "A larger first stake on a Top Coins long when no other long is occupying the "
                "wallet can preserve earnings while reducing correlated exposure versus boosting "
                "every Top Coins long."
            ),
            "rule": (
                "Use 1.5x first stake for pure native Top Coins long tags only when the native "
                "open trade list contains no long. Pending long orders consume a slot. Otherwise "
                "use 1x. Existing parent entry, adjustment, wallet, exit and leverage rules stay. "
                "Order of simultaneous entries is the original native order."
            ),
            "gate": (
                "First run the previously observed temporal window at both costs. Require at each "
                "cost positive full net return greater than original, max 5m mark drawdown within "
                "1pp of original and smaller than fixed boost, return divided by 5m drawdown above "
                "the fixed boost, at least 20 complete campaigns and 10 filled Top Coins. "
                "Only after passing both costs run the long window. Long window requires full and "
                "both slice net returns greater than original, full return/drawdown above flat "
                "boost on native hourly NAV, with drawdown within 1pp of original. Preserve all."
            ),
            "known_history": (
                "Original and fixed-boost results were known in both windows before this "
                "hypothesis. The independent fee contracts are paired historical comparisons."
            ),
            "orders_enabled": False,
        },
    )


def jsonl(path):
    import json

    return [json.loads(line) for line in path.read_text().splitlines()]


def scope(trade):
    tags = set((trade["enter_tag"] or "").split())
    return not trade["is_short"] and bool(tags) and tags <= TOP_COINS


def audit_inventory(path, trades):
    traces = jsonl(path / "long-inventory.jsonl")
    proposals = jsonl(path / "stake-decisions.jsonl")
    open_long = [t for t in trades if not t["is_short"]]
    observed = Counter()
    tied = pending = 0
    for state, order in zip(traces, proposals, strict=True):
        if (state["time"], state["pair"]) != (order["time"], order["pair"]):
            raise ValueError("Inventory and proposal clocks diverged")
        clock = pd.Timestamp(state["time"]).value // 1_000_000
        certain = {
            (t["pair"], t["open_timestamp"])
            for t in open_long
            if t["open_timestamp"] < clock < t["close_timestamp"]
            and min(o["order_filled_timestamp"] for o in t["orders"] if o["ft_is_entry"]) < clock
        }
        possible = {
            (t["pair"], t["open_timestamp"])
            for t in open_long
            if t["open_timestamp"] <= clock <= t["close_timestamp"]
        }
        actual = {
            (t["pair"], pd.Timestamp(t["opened"]).value // 1_000_000)
            for t in state["longs"]
            if t["quantity"] > 0
        }
        if not certain <= actual <= possible:
            raise ValueError(f"Inventory conflicts with native fills: {state}")
        tied += len(actual - certain)
        pending += sum(t["quantity"] <= 0 for t in state["longs"])
        ratio = 1.0 if state["longs"] else 1.5
        stake = min(order["max_stake"], max(order["min_stake"] or 0, order["native_stake"] * ratio))
        if order["multiplier"] != ratio or not math.isclose(
            stake, order["requested_stake"], abs_tol=1e-9
        ):
            raise ValueError("Occupancy sizing differs")
        observed[(state["pair"], clock, order["tag"].strip(), ratio)] += 1
    filled = [t for t in trades if scope(t)]
    filled_by_size = Counter()
    for trade in filled:
        key = (trade["pair"], trade["open_timestamp"], trade["enter_tag"].strip())
        ratios = {k[3] for k in observed if k[:3] == key}
        if len(ratios) != 1:
            raise ValueError(f"Missing or ambiguous native first entry trace: {key}")
        filled_by_size[str(ratios.pop())] += 1
    return {
        "matched_filled_top_coin_campaigns": len(filled),
        "filled_multipliers": dict(filled_by_size),
        "attempted_multipliers": dict(
            Counter({str(r): sum(v for k, v in observed.items() if k[3] == r) for r in (1.0, 1.5)})
        ),
        "same_clock_occupancy_items": tied,
        "pending_inventory_items": pending,
        "inventory_sha256": sha(path / "long-inventory.jsonl"),
        "sizing_sha256": sha(path / "stake-decisions.jsonl"),
    }


def account(plan_path, window, cost):
    plan = read(plan_path)
    (case,) = plan["candidates"]
    folder = OUT / f"{window}_{cost}bps"
    with folder.with_suffix(".log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(PARENT / "run_wide_comparison.py"),
                "--plan",
                str(plan_path),
                "--candidate",
                STRATEGY,
                "--cost-bps",
                str(cost),
                "--output",
                str(folder),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    summary = temporal.audit_one(plan, case, cost, folder)
    from research.crypto.nfi_strength_allocation_20261107.audit import native_trades

    trades, _ = native_trades(plan, folder, STRATEGY, cost)
    summary.update(audit_inventory(folder, trades))
    save(folder / "audit.json", summary)
    cache = folder / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(
        window,
        cost,
        summary["periods"]["continuous"]["net_return"],
        summary["filled_multipliers"],
        flush=True,
    )
    return summary


def main():
    if sys.argv[1] == "register":
        register()
    elif sys.argv[1] == "temporal":
        for source in read(REGISTRATION)["sources"]:
            checked(source)
        OUT.mkdir()
        receipts = {str(cost): account(SHORT_PLAN, "temporal", cost) for cost in (15, 8)}
        save(OUT / "temporal_audit.json", receipts)
    else:
        raise SystemExit("Use register or temporal")


if __name__ == "__main__":
    main()
