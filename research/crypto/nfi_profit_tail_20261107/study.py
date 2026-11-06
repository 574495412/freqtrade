"""Freeze native exit extensions and audit their actual callback and fill history."""

import math
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.crypto.nfi_profit_ramp_20261107 import study as a13
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_solo_allocation_20261107.run import jsonl, scope
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, PARENT, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
OUT = DATA / "nfi_profit_tail_20261107_v001"
PROTOCOL = HERE / "protocol.json"
RESULT = HERE / "result.json"
A04 = a13.A04
COSTS = (15, 8)
CASES = ("FlatProfitTail", "TrendProfitTail")
PARENTS = {"temporal": A04 / "temporal_registration.json", "long": A04 / "registration.json"}
PREFIX = "exit_profit_long_tc_"


def register():
    if PROTOCOL.exists() or OUT.exists():
        raise FileExistsError("A18 already registered")
    base = read(PARENTS["temporal"])["candidates"][0]
    raw = read(checked(base["config"]))
    cases, configs = [], []
    for name in CASES:
        path = HERE / f"{name}.json"
        save(path, {**raw, "strategy": name})
        configs.append(path)
        cases.append(
            {
                **base,
                "name": name,
                "strategy": name,
                "config": record(path),
                "source_files": {
                    **base["source_files"],
                    "ProfitTail.py": record(HERE / "ProfitTail.py"),
                },
                "source_and_risk_behavior": "Defers qualifying native profit exits.",
            }
        )
    plans = {}
    for stage, parent in PARENTS.items():
        native = read(parent)
        native.pop("baselines", None)
        path = HERE / f"native_{stage}.json"
        save(
            path,
            {
                **native,
                "schema": "freqtrade.research.nfi-profit-tail.v1",
                "registered_at": datetime.now(UTC).isoformat(),
                "candidates": cases,
                "total_cost_bps_per_side": list(COSTS),
                "orders_enabled": False,
            },
        )
        plans[stage] = record(path)
    sources = (
        Path(__file__),
        HERE / "ProfitTail.py",
        HERE / "evaluate.py",
        *configs,
        *PARENTS.values(),
        A04 / "temporal_result.json",
        A04 / "mark_result.json",
        A04 / "result.json",
        long_mark.study.HERE / "long_mark_result.json",
        long_mark.OUT / "admission.json",
        Path(long_mark.__file__),
        Path(mark_valuation.__file__),
        Path(temporal.__file__),
        mark_valuation.ORACLE,
        A04 / "audit.py",
    )
    save(
        PROTOCOL,
        {
            "registered_at_utc": datetime.now(UTC).isoformat(),
            "plans": plans,
            "sources": [record(path) for path in sources],
            "hypothesis": (
                "Profitable pure Top Coins longs with continued price discovery can "
                "capture positive price tails after native profit taking."
            ),
            "scope": (
                "Only a native custom exit with exit_profit_long_tc_ prefix and >=0.5% native "
                "current profit on a pure Top Coins long with one successful entry, no previous "
                "exit and no open order. FlatProfitTail extends all; TrendProfitTail requires "
                "the last admitted hourly trade close strictly exceeds the preceding 24 closes. "
                "Select the most recent completed 1h candle as of t minus 5m, exact grid and "
                "no filled gaps. Each trade can arm once. Base engine and strategy unchanged "
                "apart from the custom-exit response on eligible lifecycles."
            ),
            "exit": (
                "At arm set peak to native current profit and allowable drawback to half that "
                "profit. On later callbacks update peak from native current 5m open, never highs. "
                "Honor native non-Top Coins-profit custom exits immediately. Otherwise request "
                "exit if position changes, current profit <= peak minus allowance, or 24h "
                "from arm, whichever is first. Keep release sticky if unfilled; priority "
                "native risk, then changed position, drawback, deadline. A gap or native "
                "order path may exceed the allowance. No standing protective order. Base "
                "native position adjustment, stop, ROI, regular exits and 1x leverage unchanged."
            ),
            "gates": (
                "Always run both strategies at both costs in the 65-day comparison. At both "
                "costs TrendProfitTail must have positive full net return above original and "
                "flat deferral, higher full return/5m drawdown than both, within +1pp 5m "
                "drawdown of original, at least 20 natural closes and 4 naturally closed "
                "deferred trades. Run 20 months only if both costs pass; additionally require "
                "above original in both calendar slices and at least 60 closes and 20 "
                "deferred natural closes. Controls in both windows were previously known."
            ),
            "orders_enabled": False,
        },
    )


def check():
    p = read(PROTOCOL)
    for source in p["sources"]:
        checked(source)
    return {key: checked(ref) for key, ref in p["plans"].items()}


def audit(plan, folder, name, trades):  # noqa: C901
    inputs = read(checked(plan["data_receipts"]))
    tapes, opens = {}, {}
    rows = (
        jsonl(folder / "tail-decisions.jsonl") if (folder / "tail-decisions.jsonl").exists() else []
    )
    states = {}
    counts = Counter()
    callbacks = {}

    def values(pair, interval, column, cache):
        if pair not in cache:
            s = pair.replace("/", "_").replace(":", "_")
            key = f"futures/{s}-{interval}-futures.feather"
            path = checked({"path": str(Path(plan["datadir"]) / key), "sha256": inputs[key]})
            cache[pair] = pd.read_feather(path).set_index("date")[column]
        return cache[pair]

    for row in rows:
        at = pd.Timestamp(row["time"])
        pair = row["pair"]
        key = (pair, pd.Timestamp(row["opened"]).value // 1_000_000)
        ms = at.value // 1_000_000
        clock_price = values(pair, "5m", "open", opens).at[at]
        if not math.isclose(row["rate"], clock_price, rel_tol=1e-12):
            raise ValueError("Exit callback rate differs from observed bar open")
        profit = row["profit_ratio"]
        current = states.get(key)
        native_profit = isinstance(row["native"], str) and row["native"].startswith(PREFIX)
        counts[row["action"]] += 1
        if current is None:
            if not native_profit or profit < 0.005 or not row["unchanged_one_fill"]:
                raise ValueError("Deferred an out-of-scope exit")
            passed = True
            if name == "TrendProfitTail":
                end = (at - pd.Timedelta(minutes=5)).floor("h") - pd.Timedelta(hours=1)
                if row["available_hour"] != end.isoformat():
                    raise ValueError("Hourly cutoff differs")
                close = values(pair, "1h", "close", tapes)
                sample = [close.get(end - pd.Timedelta(hours=i), math.nan) for i in range(25)]
                passed = all(math.isfinite(x) and x > 0 for x in sample) and sample[0] > max(
                    sample[1:]
                )
            if passed:
                states[key] = (at, profit, profit / 2, None)
            if row["action"] != ("arm" if passed else "decline"):
                raise ValueError("Independent breakout admission differs")
        else:
            armed, peak, allowance, release = current
            peak = max(peak, profit)
            if row["native"] and not native_profit:
                release = row["native"]
            elif release is None:
                if not row["unchanged_one_fill"]:
                    release = "tail_position_changed"
                elif profit <= peak - allowance:
                    release = "tail_profit_drawback"
                elif at - armed >= pd.Timedelta(hours=24):
                    release = "tail_24h_deadline"
            states[key] = (armed, peak, allowance, release)
            if row["exit"] != release or row["peak"] != peak or row["allowance"] != allowance:
                raise ValueError("Independent trailing state differs")
            if row["action"] != ("release" if release else "hold"):
                raise ValueError("Independent release status differs")
            callbacks[(key, ms)] = release
    natural_deferred = forced_or_other = 0
    delay = []
    for t in trades:
        key = (t["pair"], t["open_timestamp"])
        if key not in states:
            continue
        if not scope(t):
            raise ValueError("Out-of-scope native fill linked to tail")
        armed = states[key][0]
        if t["exit_reason"] == "force_exit":
            forced_or_other += 1
            continue
        end = pd.Timestamp(t["close_timestamp"], unit="ms", tz="UTC")
        if end <= armed:
            forced_or_other += 1
            continue
        if (
            t["exit_reason"].startswith("tail_")
            and callbacks.get((key, t["close_timestamp"])) != t["exit_reason"]
        ):
            raise ValueError("Tail exit missing an exact native callback")
        natural_deferred += 1
        delay.append((end - armed).total_seconds() / 60)
    return {
        "actions": dict(counts),
        "naturally_closed_deferred": natural_deferred,
        "forced_or_other_arms": forced_or_other,
        "mean_elapsed_minutes": sum(delay) / len(delay) if delay else None,
        "max_elapsed_minutes": max(delay) if delay else None,
        "trace_sha256": sha(folder / "tail-decisions.jsonl") if rows else None,
    }


def account(plan_path, stage, name, cost):
    plan = read(plan_path)
    candidate = next(c for c in plan["candidates"] if c["name"] == name)
    key = f"{stage}_{name}_{cost}bps"
    path = OUT / key
    with path.with_suffix(".log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(PARENT / "run_wide_comparison.py"),
                "--plan",
                str(plan_path),
                "--candidate",
                name,
                "--cost-bps",
                str(cost),
                "--output",
                str(path),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    summary = temporal.audit_one(plan, candidate, cost, path)
    trades, _ = native_trades(plan, path, name, cost)
    summary.update(audit(plan, path, name, trades))
    save(path / "audit.json", summary)
    cache = path / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(key, summary["periods"]["continuous"]["net_return"], summary["actions"], flush=True)
    return key, summary


if __name__ == "__main__":
    if sys.argv[1:] != ["register"]:
        raise SystemExit("Use register")
    register()
