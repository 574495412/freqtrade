"""Compute 5m OPEN valuation for the previously registered BTC reference."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
PLAN=ROOT/"research/crypto/spot_baseline_20261105_v003/plan-long.json"
BASE=ROOT/"user_data/crypto_quant/spot_btc_hold_long_20261105_v001"

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    plan=json.loads(PLAN.read_text()); registration=json.loads((BASE/"registration.json").read_text())
    if sha(PLAN)!=registration["strategy_plan_sha256"]: raise ValueError("Plan changed")
    data_receipt=Path(plan["data_receipts"]["path"])
    if sha(data_receipt)!=plan["data_receipts"]["sha256"]: raise ValueError("Data receipt changed")
    p=Path(plan["datadir"])/"BTC_USDT-5m.feather"
    if sha(p)!=json.loads(data_receipt.read_text())[p.name]: raise ValueError("5m source changed")
    prices=pd.read_feather(p).set_index("date").loc[plan["start"]:plan["comparison_end"],"open"]
    if not prices.index.equals(pd.date_range(plan["start"],plan["comparison_end"],freq="5min")) or not np.isfinite(prices).all() or (prices<=0).any(): raise ValueError("Invalid 5m data")
    rows=[]
    for cost in plan["total_cost_bps_per_side"]:
        fee=cost/10000; qty=float(plan["initial_equity_usdt"])/(float(prices.iloc[0])*(1+fee))
        equity=qty*prices*(1-fee);equity.iloc[0]=float(plan["initial_equity_usdt"])
        for period in plan["periods"]:
            x=equity.loc[period["start"]:period["end"]]
            rows.append({"cost_bps_per_side":cost,"period":period["name"],"return":float(x.iloc[-1]/x.iloc[0]-1),"five_min_open_drawdown":float((1-x/x.cummax()).max())})
    result={"registration_sha256":sha(BASE/"registration.json"),"source_sha256":sha(p),"rows":rows,"limitation":"fractional BTC and 5m observed OPEN are not actual fills or intrabar worst"}
    out=BASE/"result-5m-reference.json"
    if out.exists(): raise FileExistsError(out)
    out.write_text(json.dumps(result,indent=2)+"\n");print(json.dumps(rows,indent=2))

if __name__=="__main__": main()
