"""Separate historical funding attribution from four native temporal accounts."""

import math
import shutil
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from itertools import pairwise

import numpy as np
import pandas as pd

from research.crypto.nfi_funding_rebuy_20261107 import study
from research.crypto.nfi_solo_allocation_20261107 import long_mark
from research.crypto.nfi_solo_allocation_20261107.run import jsonl
from research.crypto.nfi_strength_allocation_20261107 import mark_valuation, temporal
from research.crypto.nfi_strength_allocation_20261107.audit import native_trades, read
from research.crypto.nfi_strength_allocation_20261107.register import PARENT, record
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import (
    checked,
    load_module,
    save,
    sha,
)


def load():
    protocol = study.checked_registration()
    binding = read(study.HERE / "binding.json")
    if binding["registration_sha256"] != sha(study.REGISTRATION):
        raise ValueError("Native binding differs from registration")
    admission = read(checked(binding["admission"]))
    raw = read(checked(admission["tape"]))
    tape = {
        pair: pd.Series(
            [Decimal(str(r)) for r in rates], index=pd.to_datetime(clocks, unit="ms", utc=True)
        )
        for pair, (clocks, rates) in raw.items()
    }
    return protocol, binding, tape


def score(tape, pair, at):
    cutoff = at - pd.Timedelta(minutes=15)
    start = cutoff - pd.Timedelta(days=1)
    series = tape[pair]
    rows = series.loc[(series.index > start) & (series.index <= cutoff)]
    gaps = [start, *rows.index, cutoff]
    complete = len(rows) >= 3 and all(b - a <= pd.Timedelta(hours=9) for a, b in pairwise(gaps))
    flag = complete and rows.iloc[-1] > 0 and sum(rows) >= Decimal("0.0003")
    return flag, rows, cutoff


def attribute(protocol, tape):
    results = {}
    for window, source in study.PARENTS.items():
        plan = read(source)
        end = pd.Timestamp(plan["comparison_end"]).value // 1_000_000
        for cost in study.COSTS:
            key = f"{window}_{cost}bps"
            ref = checked(protocol["original_archives"][key])
            trades, audit = native_trades(plan, ref.parents[1], "NostalgiaForInfinityX8", cost)
            grouped = {True: [], False: []}
            for t in trades:
                if (
                    not study.scoped(t)
                    or t["exit_reason"] == "force_exit"
                    or t["close_timestamp"] >= end
                ):
                    continue
                at = pd.Timestamp(t["open_timestamp"], unit="ms", tz="UTC")
                flag, _, _ = score(tape, t["pair"], at)
                entry, exit_ = (
                    sum(
                        o["amount"] * o["safe_price"]
                        for o in t["orders"]
                        if o["ft_is_entry"] == entry
                    )
                    for entry in (True, False)
                )
                grouped[flag].append(
                    (entry, entry - exit_, t["profit_abs"], t["funding_fees"], at.date())
                )
            summary = {}
            for flag, rows in grouped.items():
                exposure = sum(r[0] for r in rows)
                summary["flagged" if flag else "other"] = {
                    "count": len(rows),
                    "entry_days": len({r[4] for r in rows}),
                    "entry_notional_usdt": float(exposure),
                    "price_pnl_usdt": float(sum(r[1] for r in rows)),
                    "funding_usdt": float(sum(r[3] for r in rows)),
                    "net_pnl_usdt": float(sum(r[2] for r in rows)),
                    "net_per_entry": float(sum(r[2] for r in rows) / exposure)
                    if exposure
                    else None,
                }
            flagged, other = summary["flagged"], summary["other"]
            passed = (
                flagged["count"] >= 12
                and flagged["price_pnl_usdt"] > 0
                and (flagged["net_per_entry"] > max(0, other["net_per_entry"] or 0))
            )
            results[key] = {
                "groups": summary,
                "passed": passed,
                "archive_sha256": audit["archive_sha256"],
            }
    return results


def audit_stake(path, trades, tape, strategy):
    rows = jsonl(path / "short-stakes.jsonl") if (path / "short-stakes.jsonl").exists() else []
    mapped = {}
    for row in rows:
        at = pd.Timestamp(row["time"])
        flag = True
        if strategy == study.CASES[1]:
            flag, series, cutoff = score(tape, row["pair"], at)
            observed = [[t.value // 1_000_000, float(r)] for t, r in series.items()]
            if observed != row["settled_rates"] or cutoff.value // 1_000_000 != row["cutoff_ms"]:
                raise ValueError("Funding decision clock or events differ")
        multiplier = 2.5 if flag else 1.0
        expected = min(
            row["max_stake"], max(row["min_stake"] or 0, row["native_stake"] * multiplier)
        )
        if row["multiplier"] != multiplier or not math.isclose(
            expected, row["requested_stake"], abs_tol=1e-8
        ):
            raise ValueError("Native stake failed independent calculation")
        mapped[(row["pair"], at.value // 1_000_000, row["tag"].strip())] = multiplier
    fills = Counter(
        str(mapped[(t["pair"], t["open_timestamp"], t["enter_tag"].strip())])
        for t in trades
        if study.scoped(t)
    )
    return {
        "scoped_fills": sum(fills.values()),
        "boosted_fills": fills["2.5"],
        "sizing_callbacks": len(rows),
        "multipliers": dict(fills),
    }


def account(plan_path, stage, cost, strategy, tape):
    plan = read(plan_path)
    case = next(c for c in plan["candidates"] if c["name"] == strategy)
    name = f"{stage}_{strategy}_{cost}bps"
    path = study.OUT / name
    with path.with_suffix(".log").open("x") as log:
        subprocess.run(
            [
                sys.executable,
                "-B",
                str(PARENT / "run_wide_comparison.py"),
                "--plan",
                str(plan_path),
                "--candidate",
                strategy,
                "--cost-bps",
                str(cost),
                "--output",
                str(path),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    summary = temporal.audit_one(plan, case, cost, path)
    trades, _ = native_trades(plan, path, strategy, cost)
    summary.update(audit_stake(path, trades, tape, strategy))
    save(path / "audit.json", summary)
    cache = path / "lossless_cache"
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)
    print(name, summary["periods"]["continuous"]["net_return"], summary["multipliers"], flush=True)
    return name, summary


def revalue(plan, stage, accounts):
    hour, grid = long_mark.grids(plan)
    oracle = load_module("a17_mark_oracle", mark_valuation.ORACLE)
    temporal.OUT = study.OUT
    output = study.OUT / f"{stage}-mark"
    mark_valuation.OUT = output
    trades, markets, errors = mark_valuation.trades_and_checks(
        {"accounts": accounts}, plan, oracle, hour
    )
    if max(errors.values()) > 1e-5:
        raise ValueError("Native hourly valuation failed independent parity")
    if stage == "temporal":
        with ThreadPoolExecutor(max_workers=8) as pool:
            raw = list(pool.map(lambda p: mark_valuation.month_mark(p, grid), markets))
    else:
        admission = read(long_mark.OUT / "admission.json")["marks"]
        raw = [
            (s, pd.read_parquet(checked(admission[s]["frame"])).open, admission[s]) for s in markets
        ]
    prices = {s: x.reindex(grid) for s, x, _ in raw}
    for s, frame in prices.items():
        frozen = markets[s][1].set_index("date").open.reindex(hour)
        if not np.isfinite(frame).all() or not np.allclose(
            frame.reindex(hour), frozen, rtol=1e-10, atol=1e-12
        ):
            raise ValueError(f"Official five-minute marks differ: {s}")
    output.mkdir(exist_ok=True)
    clock = grid.as_unit("ms").asi8
    reports = {}
    for name, ledger in trades.items():
        nav = np.full(len(grid), plan["initial_equity_usdt"], dtype=float)
        for t in ledger:
            s = t["pair"]
            funding, mark = markets[s]
            oracle.contribute(t, clock, prices[s].to_numpy(), funding, mark, nav)
        if not np.isfinite(nav).all() or (nav <= 0).any():
            raise ValueError("Bad account NAV")
        dd = 1 - nav / np.maximum.accumulate(nav)
        dest = output / f"{name}.parquet"
        pd.DataFrame({"date": grid, "equity": nav, "drawdown": dd}).to_parquet(dest, index=False)
        reports[name] = {
            "dd": float(dd.max()),
            "hourly_error_usdt": errors[name],
            "points": len(nav),
            "curve": record(dest),
        }
    return reports, {s: v for s, _, v in raw}


def compare(stage, accounts, risk):
    result = {}
    for cost in study.COSTS:
        key0 = f"nfi-x8-1x_{cost}bps"
        if stage == "temporal":
            r = read(study.A04 / "temporal_result.json")["accounts"][key0]["periods"]
            orig = {k: v["net_return"] for k, v in r.items()}
            orig_dd = read(study.A04 / "mark_result.json")["accounts"][key0]["drawdown_5m_mark"]
        else:
            r = read(study.A04 / "result.json")["accounts"][f"FlatTopBoost_{cost}bps"]["periods"]
            orig = {k: v["baseline_return"] for k, v in r.items()}
            orig_dd = read(long_mark.study.HERE / "long_mark_result.json")["accounts"][
                f"original_{cost}bps"
            ]["drawdown_5m"]
        f_key, c_key = (f"{stage}_{s}_{cost}bps" for s in study.CASES)
        fp, cp = (accounts[k]["periods"] for k in (f_key, c_key))
        flat, candidate = fp["continuous"]["net_return"], cp["continuous"]["net_return"]
        flat_dd, candidate_dd = (risk[k]["dd"] for k in (f_key, c_key))
        count, boosts = (20, 4) if stage == "temporal" else (60, 12)
        result[str(cost)] = {
            "original_return": orig["continuous"],
            "original_drawdown": orig_dd,
            "flat_return": flat,
            "flat_drawdown": flat_dd,
            "candidate_return": candidate,
            "candidate_drawdown": candidate_dd,
            "checks": {
                "return": candidate > max(0, orig["continuous"]),
                "risk": candidate_dd <= orig_dd + 0.01,
                "return_drawdown": candidate * flat_dd > flat * candidate_dd,
                "sample": cp["continuous"]["completed_campaigns"] >= count
                and accounts[c_key]["boosted_fills"] >= boosts,
                "long_slices": stage == "temporal"
                or all(v["net_return"] > orig[k] for k, v in cp.items()),
            },
        }
    return result


def main():
    protocol, binding, tape = load()
    attribution = attribute(protocol, tape)
    save(study.OUT / "attribution.json", attribution)
    valid = all(r["passed"] for k, r in attribution.items() if not k.startswith("temporal"))
    report = {
        "registration_sha256": sha(study.REGISTRATION),
        "binding_sha256": sha(study.HERE / "binding.json"),
        "original_attribution": attribution,
        "attribution_passed": valid,
        "stages": {},
        "paper_eligible": False,
    }
    for stage in ("temporal", "long"):
        plan = checked(binding["plans"][stage])
        accounts = dict(
            account(plan, stage, cost, s, tape) for cost in study.COSTS for s in study.CASES
        )
        risk, sources = revalue(read(plan), stage, accounts)
        result = compare(stage, accounts, risk)
        passed = all(all(r["checks"].values()) for r in result.values())
        report["stages"][stage] = {
            "accounts": accounts,
            "marks": risk,
            "mark_sources": sources,
            "comparison": result,
            "passed": passed,
        }
        save(study.RESULT, report)
        print(stage, result, flush=True)
        if not passed or not valid:
            break


if __name__ == "__main__":
    main()
