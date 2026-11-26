"""Reconcile each native fill clock with its archived trade-volume bar."""

from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE


ATTRIBUTION = HERE / "recent_two_year_trade_attribution.json"
RUN_ROOT = HERE.parent.parent.parent
FEATURE_GAPS = RUN_ROOT.parent / "Vibe-Trading/agent/research/crypto_extreme_bar_skew_20261126_v1/coverage_gap_diagnostic.json"


def group_fills(report):
    by_tape = defaultdict(list)
    plans = {}
    for cost, groups in report["comparisons"].items():
        for period, group in groups.items():
            for role, references in group["native_receipts"].items():
                archive = checked(references[".zip"])
                plan_ref = receipt(archive.parents[1] / "registered_plan.json")
                plan = read(checked(plan_ref))
                plan_market = read(checked(plan["data_receipts"]))
                cohort = f"{period}_{role}_{cost}bp"
                plans[cohort] = {"registered_plan": plan_ref, "native": references[".zip"]}
                trades = stats_from(archive)["trades"]
                for trade in trades:
                    pair = trade["pair"]
                    stem = pair.replace("/", "_").replace(":", "_")
                    name = f"futures/{stem}-5m-futures.feather"
                    observed = (Path(plan["datadir"]) / name, plan_market[name])
                    for order in trade["orders"]:
                        stamp = order.get("order_filled_timestamp")
                        assert stamp and order["amount"] > 0 and order["safe_price"] > 0
                        time = pd.Timestamp(stamp, unit="ms", tz="UTC")
                        assert time == time.floor("5min")
                        by_tape[observed].append({"cohort": cohort, "pair": pair, "time": time,
                                                 "role": "entry" if order["ft_is_entry"] else "exit",
                                                 "price": order["safe_price"], "amount": order["amount"],
                                                 "reason": trade["exit_reason"]})
    return by_tape, plans


def audit(by_tape, known_gaps):
    states = defaultdict(Counter)
    exceptions = []
    for (path, digest), orders in sorted(by_tape.items()):
        frame = pd.read_feather(checked({"path": path, "sha256": digest}), columns=["date", "volume"]).set_index("date")
        assert frame.index.is_monotonic_increasing and frame.index.is_unique
        tape = frame.volume
        for fill in orders:
            time, cohort, pair = (fill[field] for field in ("time", "cohort", "pair"))
            value = tape.get(time)
            if value is None or not np.isfinite(value):
                kind = "missing_raw_trade_bar"
            else:
                assert value >= 0
                kind = "positive_volume" if value > 0 else "zero_volume"
            states[cohort][kind] += 1
            known_window = (pair, time) in known_gaps
            if known_window:
                assert kind == "zero_volume"
                states[cohort]["fills_in_confirmed_common_zero_volume_windows"] += 1
            if kind != "positive_volume":
                exceptions.append({**fill, "time": time.isoformat(), "status": kind,
                                   "raw_volume": None if value is None or not np.isfinite(value) else float(value),
                                   "market_tape": {"path": str(path), "sha256": digest},
                                   "belongs_to_common_zero_volume_window": known_window})
    return states, exceptions


def main():
    source = receipt(ATTRIBUTION)
    gaps = receipt(FEATURE_GAPS)
    gap_rows = read(checked(gaps))["days"]
    seen = {(pair, pd.Timestamp(time)) for row in gap_rows.values() for pair, item in row["affected_pairs"].items()
            for time in item["zero_volume_bars"]}
    tapes, plans = group_fills(read(checked(source)))
    scopes, exceptions = audit(tapes, seen)
    counts = {name: dict(scopes[name]) for name in sorted(scopes)}
    assert len(counts) == 12 and sum(sum(v.get(key, 0) for key in ("positive_volume", "zero_volume", "missing_raw_trade_bar"))
                                     for v in counts.values()) == sum(map(len, tapes.values()))
    result = {"source": source, "gap_diagnostic": gaps, "account_receipts": plans, "tapes_verified": len(tapes),
              "cohorts": counts, "exceptions": exceptions, "orders_enabled": False,
              "scope": "Read-only native fill timestamp audit against recorded 5m trade volume. Positive interval volume establishes an observable market bar."}
    write_once(HERE / "native_fill_volume_audit.json", result)
    print(f"{len(tapes)} tapes, {sum(map(len, tapes.values()))} fills, {len(exceptions)} exceptions", flush=True)
    print(Counter(row["status"] for row in exceptions), flush=True)


if __name__ == "__main__":
    main()
