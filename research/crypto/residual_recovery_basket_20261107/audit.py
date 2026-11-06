"""Recreate decision clocks and validate the shared-wallet execution independently."""

import math
from fractions import Fraction
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.nfi_residual_recovery_20261107.study import IndependentFactor
from research.crypto.residual_recovery_basket_20261107 import run as study
from research.crypto.spot_perp_flow_gap_20261107.audit import check_account


HERE = Path(__file__).resolve().parent


def independent_features(plan, close):
    source = IndependentFactor(read(study.DAILY))
    begin = pd.Timestamp(plan["windows"][0]["start"], tz="UTC") - pd.Timedelta(days=2)
    end = pd.Timestamp(plan["windows"][-1]["end"], tz="UTC")
    result = {}
    for day in pd.date_range(begin, end, freq="D", inclusive="left"):
        available = day + pd.Timedelta(days=1)
        prices = close.loc[day - pd.Timedelta(days=7) : day]
        if len(prices) != 8:
            raise ValueError("Exact absolute-price comparison window missing")
        row = {}
        for s in plan["symbols"]:
            pair = f"{s.removesuffix('USDT')}/USDT:USDT"
            _, (_, residual, latest) = source.factor(pair, available)
            first, prior, last = (Fraction(str(prices[s].iloc[i])) for i in (0, -2, -1))
            row[s] = (residual, latest, (last - first) / first, (last - prior) / prior)
        result[day] = row
    return result


def independent_schedule(plan, window, case, history):
    start, end = (pd.Timestamp(window[k], tz="UTC") for k in ("start", "end"))
    symbols = plan["symbols"]
    threshold = Fraction(-3, 100) if case == "AbsoluteRecovery" else math.log(0.97)
    clock = pd.date_range(start, end, freq="h", inclusive="left")
    assets = {}
    rows = {}
    eligible_prior = set()
    for at in pd.date_range(start - pd.Timedelta(days=1), end, freq="D", inclusive="left"):
        day = at - pd.Timedelta(days=1)
        values = history[day]
        ordered = []
        current = set()
        for name in symbols:
            if name == "BTCUSDT":
                continue
            residual, latest, raw, last = values[name]
            score = raw if case == "AbsoluteRecovery" else residual
            confirm = last if case == "AbsoluteRecovery" else latest
            if name in assets and (at >= assets[name] or score >= 0):
                del assets[name]
            if score is not None and score <= threshold and (case == "ResidualDrop" or confirm > 0):
                current.add(name)
                if at >= start and name not in eligible_prior and name not in assets:
                    ordered.append((score, name))
        previous = set(assets)
        for _, name in sorted(ordered)[: 2 - len(assets)]:
            assets[name] = at + pd.Timedelta(days=5)
        if at >= start:
            rows[at + pd.Timedelta(hours=1)] = {s: 0.5 for s in assets}
        eligible_prior = current
        if not previous.issubset(symbols):
            raise ValueError("Portfolio admitted a foreign asset")
    entries = {}
    last_book = {}
    for at, holdings in rows.items():
        if last_book != holdings:
            entries[at] = holdings
        last_book = holdings
    entries[clock[-1]] = {}
    frame = pd.DataFrame(
        [[entries.get(t, {}).get(s, 0.0) for s in symbols] for t in clock],
        columns=["target_" + s for s in symbols],
        index=clock,
    )
    return frame.assign(rebalance=clock.isin(entries), risk_scale=1.0)


def main():
    plan = study.checked_plan()
    result = read(study.RESULT)
    if result["registration_sha256"] != sha(study.PLAN) or result[
        "funding_admission_sha256"
    ] != sha(study.OUT / "funding_admission.json"):
        raise ValueError("Result and original intake mismatch")
    data, close = study.load(plan)
    feature = independent_features(plan, close)
    targets, books = {}, {}
    for name, row in result["accounts"].items():
        key = (row["window"], row["case"])
        if key not in targets:
            window = next(w for w in plan["windows"] if w["name"] == row["window"])
            targets[key] = independent_schedule(plan, window, row["case"], feature)
        path = study.OUT / name
        for file, digest in row["hashes"].items():
            if sha(path / file) != digest:
                raise ValueError("Sealed output differs")
        actual = pd.read_parquet(path / "targets.parquet")
        pd.testing.assert_frame_equal(actual, targets[key], check_freq=False)
        books[name] = {
            "decision_hours": len(actual),
            **check_account(plan, data, path, row),
        }
        print("independently checked", name, flush=True)
    save(
        HERE / "verification.json",
        {
            "result_sha256": sha(study.RESULT),
            "registration_sha256": sha(study.PLAN),
            "accounts": books,
            "orders_enabled": False,
        },
    )


if __name__ == "__main__":
    main()
