"""Record a pre-run, fixed BTC/USDT spot buy-and-hold comparator.

The account is a fractional-quantity research reference, not executable fills.
The start snapshot is before a buy at that bar's open; each later hourly point
reserves the assumed exit cost.  No cash flows, leverage, or funding are used.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
RUN_PLAN = ROOT / "research/crypto/spot_baseline_20261105_v003/plan-long.json"
OUT = ROOT / "user_data/crypto_quant/spot_btc_hold_long_20261105_v001"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")

def main():
    plan = json.loads(RUN_PLAN.read_text())
    source = Path(plan["marks"]["BTC/USDT"]["path"])
    if sha(source) != plan["marks"]["BTC/USDT"]["sha256"]:
        raise ValueError("BTC spot source differs from frozen long plan")
    OUT.mkdir(parents=True, exist_ok=False)
    receipt = {"registered_at_utc":datetime.now(timezone.utc).isoformat(),
               "purpose":"simple market reference, not candidate selection or executable BTC account",
               "protocol":"start with 1000 USDT; use BTC spot open at start; fractional quantity includes entry cost; reserve exit cost on subsequent hourly spot opens; initial snapshot before entry",
               "capital_usdt":plan["initial_equity_usdt"], "costs_bps_per_side":plan["total_cost_bps_per_side"],
               "periods":plan["periods"], "input_sha256":sha(source), "input_path":str(source),
               "strategy_plan_sha256":sha(RUN_PLAN), "fractional_quantity":True,
               "not_modeled":["historical symbol filters", "bid-ask", "queue priority", "slippage beyond fee-equivalent proxy", "tax"],
               "note":"No account return result was used to choose this benchmark; prior X7/X8 short period results already known."}
    save(OUT / "registration.json", receipt)
    f = pd.read_feather(source).set_index("date").loc[plan["start"]:plan["comparison_end"], "open"]
    if not f.index.equals(pd.date_range(plan["start"], plan["comparison_end"], freq="h")) or not np.isfinite(f).all() or (f <= 0).any():
        raise ValueError("BTC spot reference has incomplete/invalid hours")
    rows = []
    for cost in plan["total_cost_bps_per_side"]:
        fee = cost / 10000
        quantity = float(plan["initial_equity_usdt"]) / (float(f.iloc[0]) * (1 + fee))
        equity = quantity * f * (1 - fee)
        equity.iloc[0] = float(plan["initial_equity_usdt"])
        for period in plan["periods"]:
            series = equity.loc[period["start"]:period["end"]]
            rows.append({"cost_bps_per_side":cost, "period":period["name"], "initial_equity":float(series.iloc[0]),
                         "ending_equity":float(series.iloc[-1]), "net_return":float(series.iloc[-1]/series.iloc[0]-1),
                         "hourly_open_drawdown":float((1-series/series.cummax()).max()), "btc_quantity":quantity})
    save(OUT / "result.json", {"registration_sha256":sha(OUT / "registration.json"), "rows":rows})
    print(json.dumps(rows, indent=2))

if __name__ == "__main__":
    main()
