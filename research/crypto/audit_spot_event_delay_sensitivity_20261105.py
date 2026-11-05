"""Post-hoc fixed-quantity repricing at the following bar OPEN, not a rerun.

Only trades opened and closed within a previously identified flash-crash UTC
window are included. Moving fills changes cash, decisions and order survival,
so this is an arithmetic exposure diagnostic, not an account counterfactual.
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import pandas as pd

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main(root,name,cost):
    folder=root/f"{name}-cost{cost}"
    plan=json.loads((root/"registered_plan.json").read_text())
    result=json.loads((folder/"result.json").read_text())
    audit=json.loads((folder/"independent-account-audit.json").read_text())
    if sha(root/"registered_plan.json") != audit["plan_sha256"] or json.loads((folder/"registered_plan.json").read_text()) != plan:
        raise ValueError("Plan does not match independent audit")
    archive,=(folder/"native_results").glob("backtest-result-*.zip")
    if sha(archive)!=audit["archive_sha256"]:
        raise ValueError("Archive changed")
    with zipfile.ZipFile(archive) as z:
        filename,=[n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
        trades=json.loads(z.read(filename))["strategy"][result["strategy"]]["trades"]
    receipt=Path(plan["data_receipts"]["path"])
    if sha(receipt)!=plan["data_receipts"]["sha256"]:
        raise ValueError("Receipt changed")
    frozen=json.loads(receipt.read_text())
    start,end=pd.Timestamp("2025-10-10T20:00:00Z"),pd.Timestamp("2025-10-11T00:00:00Z")
    eligible=[t for t in trades if start<=pd.Timestamp(t["open_date"])<end and start<=pd.Timestamp(t["close_date"])<end and t["exit_reason"]!="force_exit"]
    candles={}
    rows=[]
    for trade in eligible:
        pair=trade["pair"]
        if trade["is_short"] or trade["funding_fees"] not in (0,0.0):
            raise ValueError("Not spot long")
        if pair not in candles:
            path=Path(plan["datadir"])/f"{pair.replace('/', '_')}-5m.feather"
            if sha(path)!=frozen[path.name]: raise ValueError("Source changed")
            candles[pair]=pd.read_feather(path).set_index("date").open
        delayed_pnl=0
        details=[]
        for order in trade["orders"]:
            t=pd.Timestamp(order["order_filled_timestamp"],unit="ms",tz="UTC")
            next_t=t.floor("5min")+pd.Timedelta("5min")
            if next_t not in candles[pair].index: raise ValueError("Missing next candle")
            price=float(candles[pair].loc[next_t]); qty=float(order["amount"])
            fee=float(trade["fee_open"] if order["ft_is_entry"] else trade["fee_close"])
            delayed_pnl += -qty*price*(1+fee) if order["ft_is_entry"] else qty*price*(1-fee)
            details.append({"original_fill_time":str(t),"original_price":order["safe_price"],"next_5m_open_time":str(next_t),"next_5m_open":price,"amount":qty,"entry":order["ft_is_entry"]})
        rows.append({"pair":pair,"open_date":trade["open_date"],"close_date":trade["close_date"],"original_realized_pnl_usdt":trade["profit_abs"],"fixed_quantity_repriced_pnl_usdt":delayed_pnl,"orders":details})
    report={"candidate":name,"cost_bps_per_side":cost,"native_archive_sha256":sha(archive),
            "scope":"Trades both opened and closed 2025-10-10 20:00 to 2025-10-11 00:00 UTC; selected post-result due to observed extreme event",
            "scenario":"Every originally filled order repriced to the following 5-minute bar open with original quantity and fee; no rerun, no changed signals, no account rescaling",
            "trades":len(rows),"original_realized_pnl_usdt":sum(r["original_realized_pnl_usdt"] for r in rows),
            "fixed_quantity_repriced_pnl_usdt":sum(r["fixed_quantity_repriced_pnl_usdt"] for r in rows),"details":rows,
            "limitations":["Hypothetical fixed quantities and alternate prices are not actual or simulated fills", "No account rerun: strategy decisions, order survival, cash, capacity and later positions would change", "Five-minute delay is an arbitrary scenario, not a measurement of real latency", "K-line open is a print, not guaranteed bid/ask or liquidity"]}
    out=folder/"extreme-event-5m-delay-fixed-quantity-sensitivity.json"
    if out.exists(): raise FileExistsError(out)
    out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k not in ("details","limitations")},indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("root",type=Path);p.add_argument("candidate");p.add_argument("cost",type=int)
    a=p.parse_args();main(a.root.resolve(),a.candidate,a.cost)
