"""Independently rebuild spot pre-bar cash and equity from native filled orders.

This is a 5m candle-OPEN reference with estimated closing fees, not real fills,
intrabar worst drawdown or a replay of the order-matching engine. All orders at
a bar's timestamp are applied after that bar's snapshot.
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main(root, name, cost):
    folder = root / f"{name}-cost{cost}"
    plan = json.loads((root / "registered_plan.json").read_text())
    result = json.loads((folder / "result.json").read_text())
    existing_audit = json.loads((folder / "independent-account-audit.json").read_text())
    if sha(root / "registered_plan.json") != existing_audit["plan_sha256"]:
        raise ValueError("Registered plan differs from independent audit")
    if json.loads((folder / "registered_plan.json").read_text()) != plan:
        raise ValueError("Account plan differs from root registration")
    if sha(folder / "hourly_equity.parquet") != existing_audit["curve_sha256"]:
        raise ValueError("Hourly curve differs from independent audit")
    archive, = (folder / "native_results").glob("backtest-result-*.zip")
    if sha(archive) != existing_audit["archive_sha256"] or sha(folder / "result.json") != existing_audit["result_sha256"]:
        raise ValueError("Results changed since independent audit")
    data_receipts = Path(plan["data_receipts"]["path"])
    if sha(data_receipts) != plan["data_receipts"]["sha256"]:
        raise ValueError("Frozen dataset receipt changed")
    receipts = json.loads(data_receipts.read_text())
    with zipfile.ZipFile(archive) as z:
        filename, = [n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
        stats = json.loads(z.read(filename))["strategy"][result["strategy"]]
    if stats["starting_balance"] != plan["initial_equity_usdt"] or stats["pairlist"] != plan["pairs"]:
        raise ValueError("Account and plan differ")
    start, end = pd.Timestamp(plan["start"]), pd.Timestamp(plan["comparison_end"])
    times = pd.date_range(start, end, freq="5min")
    # Native order times are milliseconds. Search with explicit UTC datetimes to
    # avoid assuming the datetime64 unit used by a particular pandas version.
    time_ms = (times.asi8 // 1_000_000 if times.asi8[-1] > 10**16 else times.asi8 // 1000)
    if abs(int(time_ms[0]) - int(start.timestamp()*1000)) > 1:
        raise ValueError("Datetime unit conversion failed")
    n = len(times)
    cash_delta = np.zeros(n + 1)
    events = {pair: [] for pair in plan["pairs"]}
    fee = cost / 10000
    count = 0
    for trade in stats["trades"]:
        if trade["is_short"] or trade["funding_fees"] not in (0, 0.0) or trade["leverage"] != 1 or trade["pair"] not in events:
            raise ValueError("Unexpected non-spot trade")
        if not np.isclose(trade["fee_open"], fee) or not np.isclose(trade["fee_close"], fee):
            raise ValueError("Fee differs from frozen account")
        for order in trade["orders"]:
            ts = int(order["order_filled_timestamp"])
            if ts < time_ms[0]:
                raise ValueError("Unknown order before starting wallet")
            index = int(np.searchsorted(time_ms, ts, side="right"))
            if index >= n:
                continue
            amount, price = float(order["amount"]), float(order["safe_price"])
            if amount <= 0 or price <= 0:
                raise ValueError("Invalid order")
            qty = amount if order["ft_is_entry"] else -amount
            cash_delta[index] += -amount * price * (1 + fee) if qty > 0 else amount * price * (1 - fee)
            events[trade["pair"]].append((index, qty))
            count += 1
    cash = float(plan["initial_equity_usdt"]) + np.cumsum(cash_delta[:n])
    gross = np.zeros(n)
    for pair, fills in events.items():
        if not fills:
            continue
        p = Path(plan["datadir"]) / f"{pair.replace('/', '_')}-5m.feather"
        if sha(p) != receipts[p.name]:
            raise ValueError("Spot OHLC data changed")
        prices = pd.read_feather(p).set_index("date").loc[start:end, "open"]
        if not prices.index.equals(times) or not np.isfinite(prices).all() or (prices <= 0).any():
            raise ValueError("Spot reference missing prices")
        delta = np.zeros(n)
        for index, qty in fills:
            delta[index] += qty
        holding = np.cumsum(delta)
        if holding.min() < -1e-7:
            raise ValueError("Sold more than held")
        gross += holding * prices.to_numpy(dtype=float)
    equity = cash + gross * (1-fee)
    if not np.isfinite(equity).all() or (equity <= 0).any():
        raise ValueError("Invalid portfolio equity")
    observed = pd.read_parquet(folder / "hourly_equity.parquet").set_index("date")["hourly_mark_equity"]
    hourly = pd.Series(equity, index=times).loc[observed.index]
    error = float(np.max(np.abs(hourly.to_numpy() - observed.to_numpy())))
    if error > 1e-5:
        raise ValueError(f"Independent 5m ledger does not reproduce hourly portfolio: {error} USDT")
    reference = pd.DataFrame({"date":times, "cash":cash, "spot_gross":gross, "equity_after_reserved_exit_fee":equity, "gross_to_equity":gross/equity})
    out = folder / "independent-5m-order-equity-v3.parquet"
    if out.exists():
        raise FileExistsError(out)
    reference.to_parquet(out, index=False)
    intervals = []
    for period in plan["periods"]:
        part = reference.loc[reference.date.between(pd.Timestamp(period["start"]), pd.Timestamp(period["end"]))]
        values = part.equity_after_reserved_exit_fee
        intervals.append({"period":period["name"], "max_5m_open_reference_drawdown":float((1-values/values.cummax()).max()),
                          "max_5m_open_gross_to_equity":float(part.gross_to_equity.max()), "minimum_total_cash_reference_usdt":float(part.cash.min())})
    receipt = {"strategy":name, "cost_bps_per_side":cost, "native_archive_sha256":sha(archive),
               "account_audit_sha256":sha(folder / "independent-account-audit.json"), "plan_sha256":sha(root / "registered_plan.json"),
               "method_source_sha256":sha(Path(__file__)), "saved_5m_reference_sha256":sha(out),
               "eligible_filled_orders":count, "max_hourly_equity_difference_usdt":error, "intervals":intervals,
               "definition":"Before each 5m bar, cash from earlier fills plus held units valued at candle OPEN net of assumed exit fee; after-snapshot fills applied to next snapshot.",
               "limitations":["Not intrabar worst drawdown", "Native filled orders are historical engine assumptions, not actual exchange fills", "5m OHLCV omits order book and queue", "Gross excludes pending open orders", "Cash is total ledger cash and does not subtract cash reserved for pending orders"]}
    (folder / "independent-5m-order-equity-v3.json").write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps(receipt, indent=2))

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("candidate")
    p.add_argument("cost", type=int)
    a = p.parse_args()
    main(a.root.resolve(), a.candidate, a.cost)
