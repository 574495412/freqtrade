"""Summarize native spot fills against the exchange 5m source candle.

Historical candle touches and full candle volume cannot establish a real fill,
available liquidity, actual order placement time, or intrabar price ordering.
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
    audit = json.loads((folder / "independent-account-audit.json").read_text())
    if sha(root / "registered_plan.json") != audit["plan_sha256"] or json.loads((folder / "registered_plan.json").read_text()) != plan:
        raise ValueError("Registered plan differs from independent audit")
    if sha(folder / "result.json") != audit["result_sha256"]:
        raise ValueError("Result differs from independent audit")
    archive, = (folder / "native_results").glob("backtest-result-*.zip")
    if sha(archive) != audit["archive_sha256"]:
        raise ValueError("Archive changed")
    with zipfile.ZipFile(archive) as z:
        filename, = [n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
        stats = json.loads(z.read(filename))["strategy"][result["strategy"]]
    receipt = Path(plan["data_receipts"]["path"])
    if sha(receipt) != plan["data_receipts"]["sha256"]:
        raise ValueError("Data receipt changed")
    frozen = json.loads(receipt.read_text())
    grouped = {}
    for trade in stats["trades"]:
        if trade["is_short"]:
            raise ValueError("Unexpected short")
        for order in trade["orders"]:
            row = {"pair":trade["pair"], "side":order["ft_order_side"], "entry":order["ft_is_entry"],
                   "timestamp":pd.Timestamp(order["order_filled_timestamp"], unit="ms", tz="UTC"),
                   "amount":float(order["amount"]), "fill_price":float(order["safe_price"]),
                   "native_exit_reason":trade["exit_reason"], "order_tag":order.get("ft_order_tag")}
            grouped.setdefault(trade["pair"], []).append(row)
    records = []
    for pair, orders in grouped.items():
        path = Path(plan["datadir"]) / f"{pair.replace('/', '_')}-5m.feather"
        if sha(path) != frozen[path.name]:
            raise ValueError("Source candle changed")
        candle = pd.read_feather(path).set_index("date")
        for order in orders:
            t = order["timestamp"].floor("5min")
            if t not in candle.index:
                raise ValueError("Missing candle at filled order timestamp")
            bar = candle.loc[t]
            lo, hi, op, volume = (float(bar[k]) for k in ("low", "high", "open", "volume"))
            px = order["fill_price"]
            records.append({**order, "bar_open":op, "bar_low":lo, "bar_high":hi, "bar_volume":volume,
                            "range_to_open":(hi-lo)/op, "fill_to_open":(px-op)/op,
                            "amount_to_full_bar_volume":order["amount"]/volume if volume > 0 else None,
                            "inside_bar":lo-1e-10 <= px <= hi+1e-10,
                            "at_bar_low":abs(px-lo) <= max(1e-10,abs(lo)*1e-10),
                            "at_bar_high":abs(px-hi) <= max(1e-10,abs(hi)*1e-10)})
    frame = pd.DataFrame(records)
    if frame.empty:
        raise ValueError("No fills")
    out = folder / "fill-candle-evidence-v2.parquet"
    if out.exists():
        raise FileExistsError(out)
    frame.to_parquet(out,index=False)
    flash = frame.loc[(frame.timestamp >= pd.Timestamp("2025-10-10 20:00Z")) & (frame.timestamp < pd.Timestamp("2025-10-11 00:00Z"))]
    worst = frame.sort_values("range_to_open", ascending=False).head(8).copy()
    worst["timestamp"] = worst.timestamp.astype(str)
    exposure = frame["amount_to_full_bar_volume"].dropna()
    report = {"candidate":name, "cost_bps_per_side":cost, "native_archive_sha256":sha(archive),
              "fill_candle_evidence_sha256":sha(out), "rows":len(frame),
              "orders_outside_recorded_fill_bar":int((~frame.inside_bar).sum()),
              "fills_at_bar_low":int(frame.at_bar_low.sum()), "fills_at_bar_high":int(frame.at_bar_high.sum()),
              "bars_range_over_5pct":int((frame.range_to_open > .05).sum()),
              "bars_range_over_20pct":int((frame.range_to_open > .20).sum()),
              "max_single_order_amount_over_entire_candle_volume":float(exposure.max()) if len(exposure) else None,
              "orders_oct10_2025_20_to_24_utc":int(len(flash)),
              "avax_orders_oct10_2025_20_to_24_utc":int((flash.pair == "AVAX/USDT").sum()),
              "largest_range_filled_bars":worst.to_dict("records"),
              "limitations":["An OHLC touch does not certify order book availability or queue position", "Actual order placement time and intrabar path unavailable in this export", "Full-bar volume includes prints after a simulated fill and is not available depth", "Fixed fee proxies do not recreate crash slippage, gaps, or exchange outages"]}
    (folder / "fill-candle-evidence-v2.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k not in ("largest_range_filled_bars", "limitations")},indent=2))

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("candidate")
    p.add_argument("cost", type=int)
    a=p.parse_args()
    main(a.root.resolve(),a.candidate,a.cost)
