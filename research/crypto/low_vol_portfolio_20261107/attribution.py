"""Post-result decomposition and independent review of failed order batches."""

from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.low_vol_portfolio_20261107 import account, study
from research.crypto.nfi_strength_allocation_20261107.audit import read
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


def funding_from_receipt(receipt, symbol):
    archive = pd.read_parquet(checked(receipt["archive"]))
    rows = [r for source in receipt["raw"] for r in read(checked(source["response"]))]
    if any(r["symbol"] != symbol for r in rows) or len(rows) != receipt["events"]:
        raise ValueError("Original funding manifest differs")
    clocks = pd.to_datetime([r["fundingTime"] for r in rows], unit="ms", utc=True)
    buckets = clocks.ceil("5min")
    amounts = [float(r["markPrice"]) * float(r["fundingRate"]) for r in rows]
    events = pd.Series(amounts, index=buckets).groupby(level=0).sum()
    intervals = archive.set_index("calc_time").funding_interval_hours
    gaps = []
    for previous, current in pairwise(clocks):
        step = pd.Timedelta(hours=int(intervals.at[current.value // 1_000_000]))
        t = current.round("h") - step
        while step > pd.Timedelta(0) and t > previous + pd.Timedelta(minutes=1):
            gaps.append(t)
            t -= step
    if len(gaps) != receipt["expected_missing"] or receipt["invalid_marks"]:
        raise ValueError("Funding-gap review differs")
    return events, gaps, receipt


def describe(key, row, grid, data, rules, ranks, symbols):
    curve = pd.read_parquet(checked(row["curve"]))
    ledger = pd.read_parquet(checked(row["ledger"]))
    if not pd.DatetimeIndex(curve.time).equals(grid):
        raise ValueError("Portfolio calendar changed")
    fee = int(key.rsplit("_", 1)[1][:-3]) / 10000
    gross = int(key.split("_")[-2][:-1])
    change = np.zeros_like(data["price"])
    change[ledger.row, ledger.column] = ledger.delta
    quantity = change.cumsum(axis=0)
    held = np.vstack((np.zeros((1, quantity.shape[1])), quantity[:-1]))
    mark = data["mark"]
    movement = held * np.diff(mark, axis=0, prepend=mark[:1])
    funding = -held * data["fund"]
    fees = (np.abs(change) * data["price"]).sum(axis=1) * fee
    bridge = (change * (mark - data["price"])).sum()
    components = {
        "long_price": float(movement[held > 0].sum()),
        "short_price": float(movement[held < 0].sum()),
        "funding": float(funding.sum()),
        "fill_vs_mark": float(bridge),
        "fees": -float(fees.sum()),
    }
    if abs(sum(components.values()) - account.CAPITAL * row["net"]) > 1e-7:
        raise ValueError("Sleeve attribution failed terminal cash parity")
    bad = [
        {"symbol": symbols[j], "time": grid[i].isoformat()}
        for i, j in np.argwhere(data["unknown"] & (np.abs(held) > 1e-10))
    ]
    if len(bad) != row["missing_held_funding"]:
        raise ValueError("Held funding gap count differs")

    fills = (change * data["price"]).sum(axis=1) + fees
    paid_before = np.r_[0, fills.cumsum()[:-1]]
    cash_before = account.CAPITAL + funding.sum(axis=1).cumsum() - paid_before
    exposure_pre = (held * mark).sum(axis=1)
    nav_pre = cash_before + exposure_pre - fee * (np.abs(held) * mark).sum(axis=1)
    peak = np.maximum.accumulate(np.r_[account.CAPITAL, curve.equity.to_numpy()[:-1]])
    last = int(ledger.row.max()) if row["halted"] else len(grid) - 1
    rejected = []
    for i in range(last):
        at = grid[i]
        if at not in ranks or nav_pre[i] < 0.7 * max(peak[i], nav_pre[i]):
            continue
        price = data["price"][i]
        old = held[i]
        equity = cash_before[i] + old @ price
        wanted = account.post_cost_target(equity, old * price, gross * ranks[at], price, fee)
        lots = rules["lot"]
        desired = np.sign(wanted) * np.floor(np.abs(wanted) / lots + 1e-10) * lots
        small = (np.abs(desired - old) * price < rules["minimum"]) & (desired != 0)
        desired[small] = old[small]
        delta = np.abs(desired - old)
        too_large = delta > np.minimum(data["capacity"][i], rules["market_max"]) + 1e-9
        excess = float(np.abs(desired) @ price / 2 - equity + delta @ price * fee)
        if too_large.any() or excess > 1e-9:
            rejected.append(
                {
                    "time": at.isoformat(),
                    "capacity_symbols": [symbols[j] for j in np.flatnonzero(too_large)],
                    "positive_margin_shortfall_usdt": max(0, excess),
                }
            )
    if len(rejected) != row["rejected_batches"]:
        raise ValueError(f"Rejected batch count differs: {key}")
    return {
        "components_usdt": components,
        "unknown_held_funding": bad,
        "rejected_batches": rejected,
        "flatten_after_halt": grid[last].isoformat() if row["halted"] else None,
    }


def main():
    p, result = read(study.POLICY), read(study.RESULT)
    if result["protocol_sha256"] != sha(study.POLICY):
        raise ValueError("Results do not match registration")
    plan = read(checked(p["parent"]))
    symbols = p["symbols"]
    raw = {s: study.candles(plan, s) for s in symbols}
    ranks = study.weights(raw)
    rules = account.snapshot(
        read(checked(plan["market_snapshot"])), [study.symbol_pair(s) for s in symbols]
    )
    receipts = read(checked(result["funding_receipts"]))
    funding = {s: funding_from_receipt(receipts[s], s) for s in symbols}
    output = {}
    for name, (a, b) in study.WINDOWS.items():
        root = study.leverage.a13.OUT / "5m" if name == "2024" else study.long_mark.OUT
        admission = read(root / "admission.json")["marks"]
        grid = pd.date_range(study.clock(a), study.clock(b), freq="5min")
        data = account.make_inputs(grid, raw, funding, admission, study.symbol_pair)
        for key, row in result["windows"][name].items():
            output[key] = describe(key, row, grid, data, rules, ranks, symbols)
    save(
        Path(__file__).with_name("attribution.json"),
        {
            "source_sha256": sha(Path(__file__)),
            "result_sha256": sha(study.RESULT),
            "accounts": output,
        },
    )


if __name__ == "__main__":
    main()
