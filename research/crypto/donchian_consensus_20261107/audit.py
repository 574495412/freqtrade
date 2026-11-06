"""Independently verify ensemble decisions and reconcile funded spot ledgers."""

from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.donchian_consensus_20261107 import run as study
from research.crypto.spot_perp_flow_gap_20261107.audit import check_account


HERE = Path(__file__).resolve().parent


def independent_features(close, horizons):
    result = {}
    for symbol, series in close.items():
        prices = series.to_numpy()
        count = np.zeros(len(prices), dtype=np.int64)
        for n in horizons:
            windows = np.lib.stride_tricks.sliding_window_view(prices, n)
            high = windows.max(axis=1)
            mid = (high + windows.min(axis=1)) / 2
            active = False
            level = 0.0
            for i, ceiling in enumerate(high):
                j = i + n - 1
                activate = prices[j] == ceiling
                if active and not activate and prices[j] <= level:
                    active, level = False, 0.0
                if activate or active:
                    active = True
                    level = max(level, mid[i])
                count[j] += int(active)
        simple_returns = prices[1:] / prices[:-1] - 1
        volatility = np.r_[
            np.full(90, np.nan),
            np.std(np.lib.stride_tricks.sliding_window_view(simple_returns, 90), ddof=1, axis=1)
            * 365**0.5,
        ]
        result[symbol] = (count, volatility)
    return result


def independent_schedule(plan, close, quotes, features, case, window):
    begin = pd.Timestamp(window["start"], tz="UTC")
    end = pd.Timestamp(window["end"], tz="UTC")
    cutoff = end - pd.Timedelta(hours=1)
    symbols = plan["symbols"]
    daily_returns = close.to_numpy()[1:] / close.to_numpy()[:-1] - 1
    ret_abs = pd.DataFrame(np.abs(daily_returns), index=close.index[1:], columns=symbols)
    old = dict.fromkeys(symbols, -1)
    accepted = dict.fromkeys(symbols, 0.0)
    active = set()
    rows = {}
    for day in pd.date_range(begin, end, freq="D", inclusive="left"):
        previous = day - pd.Timedelta(days=1)
        start30 = day - pd.Timedelta(days=30)
        liquidity = quotes.loc[start30:previous].median()
        movement = ret_abs.loc[start30:previous].median()
        j = close.index.get_loc(previous)
        if day.day == 1:
            start_month = previous.replace(day=1)
            rank = quotes.loc[start_month:previous].median()
            names = [s for s in symbols if liquidity[s] >= 2_000_000 and j + 1 >= 365]
            active = set(sorted(names, key=lambda s: (-rank[s], s))[:20])
        active = {s for s in active if liquidity[s] >= 1_000_000 and movement[s] >= 0.005}
        raw = {}
        for s in symbols:
            count, vol = (arr[j] for arr in features[s])
            target = {"Source25": 0.25, "Fixed50": 0.50, "Consensus": 0.75 if count >= 6 else 0.25}[
                case
            ]
            scale = min(target / vol, 2.0) if np.isfinite(vol) and vol > 0 else 0.0
            if day.day == 1 or old[s] != count or abs(scale - accepted[s]) >= 0.2 * accepted[s]:
                accepted[s] = scale
            old[s] = count
            raw[s] = count * accepted[s] / (len(plan["horizons"]) * 20) if s in active else 0.0
        limit = plan["model"]["gross"]
        divisor = max(limit, sum(raw.values()))
        rows[day + pd.Timedelta(hours=1)] = [raw[s] / divisor for s in symbols]
    rows[cutoff] = [0.0] * len(symbols)
    begin = pd.Timestamp(window["start"], tz="UTC")
    grid = pd.date_range(begin, end, freq="h", inclusive="left")
    target = pd.DataFrame.from_dict(rows, orient="index", columns=["target_" + s for s in symbols])
    active = grid.isin(target.index)
    return target.reindex(grid, fill_value=0).assign(rebalance=active, risk_scale=1.0)


def cash_ledger(path, plan, row):
    events = read(path / "events.json")
    nav = pd.read_parquet(path / "nav.parquet")
    balance = plan["model"]["capital"]
    positions = dict.fromkeys(plan["symbols"], 0.0)
    min_cash = balance
    for event in events:
        kind, symbol, quantity = (event[k] for k in ("type", "symbol", "quantity"))
        if event["side"] != 1 or kind not in ("entry", "exit"):
            raise ValueError("Unexpected short, borrow, or funding in spot ledger")
        direction = 1 if kind == "entry" else -1
        balance -= direction * quantity * event["price"] + event["fee"]
        positions[symbol] += direction * quantity
        min_cash = min(min_cash, balance)
        if balance < -1e-7 or positions[symbol] < -1e-12:
            raise ValueError("Spot fill used absent cash or inventory")
    implied_terminal_cash = nav.equity.iloc[-1] - nav.gross_exposure.iloc[-1] * (
        1 - row["cost_bps"] / 10_000
    )
    if abs(balance - implied_terminal_cash) > 1e-7:
        raise ValueError("Quote-only spot wallet did not reconcile")
    return {"minimum_spendable_cash_usdt": min_cash, "terminal_cash_usdt": balance}


def main():
    plan = study.checked_plan()
    result = read(study.RESULT)
    if result["registration_sha256"] != sha(study.PLAN) or result["admission_sha256"] != sha(
        study.OUT / "admission.json"
    ):
        raise ValueError("Frozen result identity differs")
    market, close, quotes = study.inputs(plan)
    data = {
        s: {"bars": bars, "funding": pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC"))}
        for s, bars in market.items()
    }
    features = independent_features(close, plan["horizons"])
    target_cache = {}
    books = {}
    for name, row in result["accounts"].items():
        window = next(w for w in plan["windows"] if w["name"] == row["window"])
        key = (row["window"], row["case"])
        if key not in target_cache:
            target_cache[key] = independent_schedule(
                plan, close, quotes, features, row["case"], window
            )
        expected = target_cache[key]
        path = study.OUT / name
        for file, digest in row["hashes"].items():
            if sha(path / file) != digest:
                raise ValueError("Frozen account output differs")
        observed = pd.read_parquet(path / "targets.parquet")
        pd.testing.assert_frame_equal(expected, observed, check_freq=False, atol=1e-10, rtol=1e-10)
        books[name] = {
            "target_decision_hours_checked": len(expected),
            **cash_ledger(path, plan, row),
            **check_account(plan, data, path, row),
        }
        print("verified", name, books[name]["minimum_spendable_cash_usdt"], flush=True)
    save(
        HERE / "verification.json",
        {
            "result_sha256": sha(study.RESULT),
            "registration_sha256": sha(study.PLAN),
            "accounts": books,
            "authenticated_execution": False,
        },
    )


if __name__ == "__main__":
    main()
