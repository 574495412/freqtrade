"""Post-result descriptive attribution of observed 2025-10-10 flash-crash fills.

This is not a counterfactual account rerun: later cash, signals and risk would
change if orders failed or were filled differently. No strategy is retuned.
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
    folder = root / f"{name}-cost{cost}"
    result = json.loads((folder / "result.json").read_text())
    audit = json.loads((folder / "independent-account-audit.json").read_text())
    if sha(root / "registered_plan.json") != audit["plan_sha256"] or json.loads((folder / "registered_plan.json").read_text()) != json.loads((root / "registered_plan.json").read_text()):
        raise ValueError("Registered plan differs from independent audit")
    if sha(folder / "result.json") != audit["result_sha256"]:
        raise ValueError("Result differs from independent audit")
    archive, = (folder / "native_results").glob("backtest-result-*.zip")
    if sha(archive) != audit["archive_sha256"]:
        raise ValueError("Archive mismatch")
    with zipfile.ZipFile(archive) as z:
        filename, = [n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
        stats = json.loads(z.read(filename))["strategy"][result["strategy"]]
    start = pd.Timestamp("2025-10-10T20:00:00Z")
    end = pd.Timestamp("2025-10-11T00:00:00Z")
    trades = [t for t in stats["trades"] if start <= pd.Timestamp(t["open_date"]) < end and start <= pd.Timestamp(t["close_date"]) < end and t["exit_reason"] != "force_exit"]
    pnl = sum(t["profit_abs"] for t in trades)
    total = result["periods"][0]["ending_marked_equity"] - result["periods"][0]["initial_marked_equity"]
    rows = [{k:t[k] for k in ("pair","open_date","close_date","profit_abs","profit_ratio","open_rate","close_rate","exit_reason")} for t in trades]
    value = {"candidate":name, "cost_bps_per_side":cost, "native_archive_sha256":sha(archive),
             "window_start":str(start), "window_end_exclusive":str(end), "window_selected_after_observing_extreme_price_data_and_account_result":True,
             "definition":"sum realized campaign PnL of trades both opened and closed inside this UTC window",
             "completed_trades":len(rows), "campaign_pnl_usdt":pnl, "full_period_nav_change_usdt":total,
             "fraction_of_full_period_nav_change":pnl/total if total else None, "trades":rows,
             "limitations":["attribution from realized original ledger, not a deletion counterfactual", "future sizing, fees and signals could differ if fills changed", "aggregate profit ratio does not prove actual execution or robustness"]}
    out = folder / "extreme-event-realized-attribution-v2.json"
    if out.exists(): raise FileExistsError(out)
    out.write_text(json.dumps(value,indent=2)+"\n")
    print(json.dumps({k:v for k,v in value.items() if k not in ("trades","limitations")},indent=2))

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path); p.add_argument("candidate"); p.add_argument("cost",type=int)
    a=p.parse_args(); main(a.root.resolve(),a.candidate,a.cost)
