"""Audit the market input seams needed by a continuous 2024–2026 wallet."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save

from .inputs import HERE, OUT


INTERVALS = {
    "5m-futures": "5min", "15m-futures": "15min", "1h-futures": "h", "4h-futures": "4h",
    "1d-futures": "D", "5m-mark": "5min", "1h-mark": "h", "1h-funding_rate": None,
}
MARKET_FIELDS = ("id", "symbol", "settle", "swap", "linear", "inverse", "contractSize",
                 "precision", "limits", "active")
WEEK = pd.Timedelta(days=7)


def open_inputs():
    alignment = read(HERE.parent / "nfi80_online_baseline_20261125_v1/baseline_alignment.json")
    paths = {
        "2024": OUT / "annual_2024/registrations/fixed_boost_12-plan.json",
        "2025": OUT / "annual_2025/registrations/fixed_boost_12-plan.json",
        "2026": checked(alignment["native_historical_reference"]["plan"]),
    }
    refs = {year: receipt(path) for year, path in paths.items()}
    entries = {year: read(checked(item)) for year, item in refs.items()}
    for plan in entries.values():
        case, = plan["candidates"]
        assert case["strategy"] == "FlatTopBoost" and case["max_open_trades"] == 12
        assert plan["observation_frequency"] == "5min" and plan["allowed_leverage"] == 3
        assert plan["initial_equity_usdt"] == 1000 and not plan["orders_enabled"]
    assert len({plan["engine_commit"] for plan in entries.values()}) == 1
    return refs, entries


def year_markets(left, right, pairs):
    common = set(pairs)
    relevant = read(checked(left["market_snapshot"])), read(checked(right["market_snapshot"]))
    rule_updates, tiers = {}, []
    for pair in sorted(common):
        before, after = (descriptor["markets"][pair] for descriptor in relevant)
        changed = [key for key in MARKET_FIELDS if before.get(key) != after.get(key)]
        if changed:
            rule_updates[pair] = changed
        if relevant[0]["leverage_tiers"][pair] != relevant[1]["leverage_tiers"][pair]:
            tiers.append(pair)
    return {"matching_fields": MARKET_FIELDS, "field_differences": rule_updates,
            "leverage_tier_differences": tiers, "common_symbols": len(common)}


def load_frame(plan, directory, key):
    path = checked({"path": str(Path(plan["datadir"]) / key), "sha256": directory[key]})
    frame = pd.read_feather(path).set_index("date")
    assert frame.index.is_monotonic_increasing and frame.index.is_unique and str(frame.index.tz) == "UTC"
    return frame


def compare_file(pair, file, kind, left, right, left_files, right_files, boundary):
    frames = [load_frame(p, files, file) for p, files in ((left, left_files), (right, right_files))]
    before = [x.loc[(x.index >= boundary - WEEK) & (x.index < boundary)] for x in frames]
    a, b = before
    common = a.index.intersection(b.index)
    follow = frames[1].loc[(frames[1].index >= boundary) & (frames[1].index < boundary + WEEK)]
    assert list(a.columns) == list(b.columns)
    ax, bx = a.loc[common].to_numpy(), b.loc[common].to_numpy()
    unequal = ~np.isclose(ax, bx, equal_nan=True, rtol=0, atol=0).all(axis=1)
    step = INTERVALS[kind]
    expected = pd.date_range(boundary, boundary + WEEK, freq=step, inclusive="left") if step else None
    return {
        "pair": pair, "kind": kind, "overlap_rows": len(common), "value_differences": int(unequal.sum()),
        "mismatch_sample": [str(clock) for clock in common[unequal][:3]],
        "earlier_only_times": len(a.index.difference(b.index)), "later_only_times": len(b.index.difference(a.index)),
        "first_later_time": str(follow.index[0]) if len(follow) else None,
        "missing_later_interval_rows": len(expected.difference(follow.index)) if step else None,
    }


def audit_transition(left, right):
    seam = pd.Timestamp(right["start"])
    assert pd.Timestamp(left["end"]) == seam
    earlier, later = (read(checked(item["data_receipts"])) for item in (left, right))
    overlap = sorted(set(left["pairs"]) & set(right["pairs"]))
    left_schedule, right_schedule = (read(checked(item["historical_schedule"]))["days"]
                                     for item in (left, right))
    previous_day = (seam - pd.Timedelta(days=1)).date().isoformat()
    next_day = seam.date().isoformat()
    assert len(set(left_schedule[previous_day]["pairs"])) == len(set(right_schedule[next_day]["pairs"])) == 80
    assert pd.Timestamp(left_schedule[previous_day]["latest_input_close"]) < pd.Timestamp(previous_day, tz="UTC")
    assert pd.Timestamp(right_schedule[next_day]["latest_input_close"]) < seam
    tasks = []
    for pair in overlap:
        symbol = pair.replace("/", "_").replace(":", "_")
        for kind in INTERVALS:
            name = f"futures/{symbol}-{kind}.feather"
            assert name in earlier and name in later
            tasks.append((pair, name, kind, left, right, earlier, later, seam))
    totals, gaps = {kind: Counter() for kind in INTERVALS}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for detail in pool.map(lambda row: compare_file(*row), tasks):
            total = totals[detail["kind"]]
            total["files"] += 1
            for key in ("overlap_rows", "value_differences", "earlier_only_times", "later_only_times",
                        "missing_later_interval_rows"):
                total[key] += detail[key] or 0
            if (not detail["overlap_rows"] or detail["value_differences"] or detail["earlier_only_times"]
                    or detail["later_only_times"] or detail["missing_later_interval_rows"]):
                gaps.append(detail)
    prior = set(left_schedule[previous_day]["pairs"])
    following = set(right_schedule[next_day]["pairs"])
    return {
        "boundary": seam.isoformat(), "days_sampled_each_side": WEEK.days, "by_series": totals,
        "input_exceptions": gaps, "market_rules": year_markets(left, right, overlap),
        "previous_day_pool_missing_later": sorted(prior - set(right["pairs"])),
        "next_day_pool_absent_earlier": sorted(following - set(left["pairs"])),
    }


def open_at_2025_end(right):
    source = read(HERE / "boost14_2025_15bp_frozen_basis.json")
    evidence = {}
    next_inputs = read(checked(right["data_receipts"]))
    for role, row in source["accounts"].items():
        market_archive, = (ref for ref in row["receipts"] if Path(ref["path"]).suffix == ".zip")
        trades = stats_from(checked(market_archive))["trades"]
        opens = [t for t in trades if t["exit_reason"] == "force_exit"]
        evidence[role] = []
        for trade in opens:
            pair = trade["pair"]
            symbol = pair.replace("/", "_").replace(":", "_")
            keys = [f"futures/{symbol}-{kind}.feather" for kind in INTERVALS]
            evidence[role].append({"pair": pair, "open_date": trade["open_date"],
                                   "missing_later_series": [key for key in keys if key not in next_inputs],
                                   "recorded_boundary_exit": trade["close_date"]})
    return evidence


def main():
    refs, plans = open_inputs()
    result = {}
    for first, next_ in (("2024", "2025"), ("2025", "2026")):
        seam = audit_transition(plans[first], plans[next_])
        result[f"{first}_to_{next_}"] = seam
        print(seam["boundary"], "symbols", seam["market_rules"]["common_symbols"],
              "files with input exceptions", len(seam["input_exceptions"]), flush=True)
    carry = open_at_2025_end(plans["2026"])
    save(HERE / "continuous_native_input_seams.json", {
        "plans": refs, "engine_commit": plans["2024"]["engine_commit"], "market_transition_probe": result,
        "known_funded_carry_at_2025_boundary": carry, "source": receipt(Path(__file__)),
        "probe_scope": "Strict seven-day clock, data and market metadata audit. No merged input or continuous account.",
        "continuous_wallets_completed": 0, "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
