"""Freeze and run same-bar native signal concentration sizing."""

import math
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.crypto.nfi_btc_leverage_20261107 import evaluate as marks
from research.crypto.nfi_btc_leverage_20261107 import study as leverage
from research.crypto.nfi_solo_allocation_20261107.run import jsonl
from research.crypto.nfi_strength_allocation_20261107 import temporal
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.nfi_strength_allocation_20261107.register import DATA, PARENT, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


HERE = Path(__file__).resolve().parent
OUT = DATA / "nfi_signal_cluster_20261107_v001"
POLICY = HERE / "registration.json"
RESULT = HERE / "result.json"
COSTS = (15, 8)


def register():
    if POLICY.exists() or OUT.exists():
        raise FileExistsError("Study is already registered")
    parent = leverage.check()["2024_Native2x"]
    baseline = read(parent)
    template = baseline["candidates"][0]
    config = read(checked(template["config"]))
    cases = {}
    for name in ("SignalCluster", "Flat75"):
        cfg = HERE / f"{name}.json"
        plan = HERE / f"native_{name}.json"
        save(cfg, {**config, "strategy": name})
        case = {
            **template,
            "strategy": name,
            "name": name,
            "config": record(cfg),
            "source_files": {
                **template["source_files"],
                "SignalCluster.py": record(HERE / "SignalCluster.py"),
            },
            "source_and_risk_behavior": "Native 2x with registered first-stake scaling.",
        }
        save(
            plan,
            {
                **baseline,
                "schema": "freqtrade.research.nfi-signal-cluster.v1",
                "registered_at": datetime.now(UTC).isoformat(),
                "candidates": [case],
            },
        )
        cases[name] = record(plan)
    save(
        POLICY,
        {
            "registered_at": datetime.now(UTC).isoformat(),
            "plans": cases,
            "sources": [
                record(path)
                for path in (
                    Path(__file__),
                    HERE / "SignalCluster.py",
                    parent,
                    leverage.PROTOCOL,
                    leverage.RESULT,
                    Path(marks.__file__),
                    Path(temporal.__file__),
                    leverage.a13.OUT / "5m/admission.json",
                )
            ],
            "known_history": (
                "The prior original 1x and uniform 2x accounts and Apr 2024 drawdown attribution "
                "were known. Both new 2024 variants were unknown. This is in-sample adaptation "
                "to a known failure mechanism, not an untouched confirmation."
            ),
            "sizing": (
                "For each accepted signal row before engine execution, count the exact prior "
                "5m bar's unconflicted original entry signals of the same side across 40 pairs. "
                "Use full signal breadth before trading, even for occupied pairs or blocked slots. "
                "First stake = original 2x margin stake times max(0.5, 1/sqrt(N)), bounded by "
                "available stake, with zero when below minimum. Flat75 is identical except its "
                "multiplier is always 0.75. No changes to leverage, exits or adjustment callbacks. "
                "Signal multiplier is independent of processing order; the native sequential "
                "wallet, fill, protection and slot rules still determine the actual path."
            ),
            "gate": (
                "Run adaptive at both costs. Both costs must have 5m drawdown <=25%, no native "
                "liquidation, >=60 natural closes and >=20 reduced filled first stakes. "
                "Require lower 5m drawdown than uniform 2x, return/drawdown >= both 1x and 2x, "
                "positive excess over 1x in both calendar halves, and retain >=60% of uniform "
                "2x's incremental full net return above 1x. If adaptive passes, run Flat75 "
                "at both costs and require adaptive to beat its full return and return/drawdown "
                "at each cost. Passing only qualifies for separate later temporal research."
            ),
            "orders_enabled": False,
        },
    )


def audit(folder, plan, name, trades):
    feed = {}
    receipts = {}
    for pair in plan["pairs"]:
        slug = pair.replace("/", "_").replace(":", "_")
        path = folder / "signals" / f"{slug}.parquet"
        raw = pd.read_parquet(path)
        if raw.date.duplicated().any():
            raise ValueError("Duplicate per-pair signal")
        feed[pair] = {(r.date, r.side): r.enter_tag for r in raw.itertuples(index=False)}
        receipts[pair] = record(path)
    counts = Counter(key for table in feed.values() for key in table)
    entries = {}
    for row in jsonl(folder / "stake_decisions.jsonl"):
        at = pd.Timestamp(row["decision_time"])
        prior = at - pd.Timedelta(minutes=5)
        pair, side = row["pair"], row["side"]
        key = prior, side
        if not (
            row["complete"]
            and row["leverage"] == 2
            and key in feed[pair]
            and row["tag"] == feed[pair][key]
            and prior == pd.Timestamp(row["signal_time"])
            and row["count"] == counts[key]
        ):
            raise ValueError(f"Signal timing or breadth differs: {pair} {at}")
        factor = 0.75 if name == "Flat75" else max(0.5, math.sqrt(1 / counts[key]))
        expected = min(row["native_stake"] * factor, row["max_stake"])
        if expected < (row["min_stake"] or 0):
            expected = 0
        if not (
            math.isclose(row["factor"], factor, abs_tol=1e-12)
            and math.isclose(row["requested_stake"], expected, abs_tol=1e-9)
        ):
            raise ValueError("First-stake callback differs from frozen formula")
        entries.setdefault((pair, side, at.value // 1_000_000), []).append(row)
    first_counts = Counter()
    for trade in trades:
        side = "short" if trade["is_short"] else "long"
        key = trade["pair"], side, trade["open_timestamp"]
        first = next(o for o in trade["orders"] if o["ft_is_entry"])
        actual = float(first["amount"] * first["safe_price"] / 2)
        rows = entries.get(key, [])
        matched = [r for r in rows if 0 < actual <= r["requested_stake"] + 1e-8]
        if not matched:
            raise ValueError(f"Native first fill lacks a bounded decision: {key}")
        first_counts[matched[0]["count"]] += 1
    return {
        "first_fills_by_same_side_count": dict(sorted(first_counts.items())),
        "reduced_first_fills": sum(
            v for k, v in first_counts.items() if k >= 2 or name == "Flat75"
        ),
        "stake_decisions": record(folder / "stake_decisions.jsonl"),
        "signal_feeds": receipts,
    }


def run_account(path, name, cost):
    plan = read(path)
    (case,) = plan["candidates"]
    key = f"2024_{name}_{cost}bps"
    folder = OUT / key
    with folder.with_suffix(".log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(PARENT / "run_wide_comparison.py"),
                "--plan",
                str(path),
                "--candidate",
                name,
                "--cost-bps",
                str(cost),
                "--output",
                str(folder),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    summary = temporal.audit_one(plan, case, cost, folder)
    trades, _ = native_trades(plan, folder, name, cost)
    summary.update(audit(folder, plan, name, trades))
    summary["native_liquidations"] = sum(t["exit_reason"] == "liquidation" for t in trades)
    save(folder / "audit.json", summary)
    cache = folder / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(key, summary["periods"]["continuous"]["net_return"], flush=True)
    return key, summary


def assess(accounts, risk):
    parent = read(leverage.RESULT)["stages"]["2024"]["comparison"]
    compare = {}
    for cost in COSTS:
        one, two = (parent[str(cost)][name] for name in ("Original1x", "Native2x"))
        key = f"2024_SignalCluster_{cost}bps"
        periods = accounts[key]["periods"]
        full = periods["continuous"]
        net, dd = full["net_return"], risk[key]["drawdown"]
        checks = {
            "budget": dd <= 0.25 and dd < two["drawdown"],
            "retention": net >= one["return"] + 0.6 * (two["return"] - one["return"]),
            "risk_adjusted": all(net * r["drawdown"] >= r["return"] * dd for r in (one, two)),
            "sample": full["completed_campaigns"] >= 60
            and accounts[key]["reduced_first_fills"] >= 20,
            "no_liquidation": accounts[key]["native_liquidations"] == 0,
            "halves": all(
                periods[h]["net_return"] > one["period_returns"][h] for h in ("H1", "H2")
            ),
        }
        row = {
            "original_1x": one,
            "uniform_2x": two,
            "return": net,
            "drawdown": dd,
            "checks": checks,
        }
        flat = f"2024_Flat75_{cost}bps"
        if flat in accounts:
            flat_return = accounts[flat]["periods"]["continuous"]["net_return"]
            flat_dd = risk[flat]["drawdown"]
            row.update(flat_return=flat_return, flat_drawdown=flat_dd)
            checks.update(
                flat_return=net > flat_return, flat_ratio=net * flat_dd > flat_return * dd
            )
        compare[str(cost)] = row
    return compare


def run():
    registration = read(POLICY)
    for source in registration["sources"]:
        checked(source)
    plans = {k: checked(v) for k, v in registration["plans"].items()}
    OUT.mkdir()
    # Reuse the same independent 2024 5m mark and hourly parity methodology as A19.
    marks.study.OUT = OUT
    accounts = {}
    risk = {}
    receipts = {}
    for name in ("SignalCluster", "Flat75"):
        path = plans[name]
        batch = dict(run_account(path, name, cost) for cost in COSTS)
        marked, refs = marks.value(read(path), "2024", batch)
        accounts.update(batch)
        risk.update(marked)
        receipts.update(refs)
        comparison = assess(accounts, risk)
        passed = all(all(row["checks"].values()) for row in comparison.values())
        save(
            RESULT,
            {
                "registration_sha256": sha(POLICY),
                "accounts": accounts,
                "mark_risk": risk,
                "mark_sources": receipts,
                "comparison": comparison,
                "passed_screen": passed,
                "flat_control_ran": name == "Flat75",
                "paper_eligible": False,
            },
        )
        print(name, comparison, flush=True)
        if not passed:
            break


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
