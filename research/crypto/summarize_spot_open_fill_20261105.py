"""Summarize fixed 5-minute candle/open price relationships, not execution proof."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main(root,name,cost):
    folder=root/f"{name}-cost{cost}"
    audit=json.loads((folder/"independent-account-audit.json").read_text())
    evidence=json.loads((folder/"fill-candle-evidence-v2.json").read_text())
    source=folder/"fill-candle-evidence-v2.parquet"
    if sha(source)!=evidence["fill_candle_evidence_sha256"] or evidence["native_archive_sha256"]!=audit["archive_sha256"]:
        raise ValueError("Fill evidence mismatch")
    f=pd.read_parquet(source)
    at_open=np.isclose(f.fill_price,f.bar_open,rtol=1e-10,atol=1e-10)
    value={"candidate":name,"cost_bps_per_side":cost,"input_sha256":sha(source),"native_archive_sha256":audit["archive_sha256"],
           "fills":len(f),"fills_at_open_with_float_tolerance":int(at_open.sum()),
           "tolerance":"relative 1e-10, absolute 1e-10", "filled_orders_above_1pct_of_entire_bar_volume":int((f.amount_to_full_bar_volume>0.01).sum()),
           "limitations":["Includes terminal forced exits in the native archive", "Entire bar volume includes post-fill trades", "A candle open print is not necessarily achievable by our order", "No inference of queue, actual spread or market depth"]}
    out=folder/"open-fill-statistics.json"
    if out.exists(): raise FileExistsError(out)
    out.write_text(json.dumps(value,indent=2)+"\n"); print(json.dumps(value,indent=2))

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("root",type=Path);p.add_argument("candidate");p.add_argument("cost",type=int)
    a=p.parse_args();main(a.root.resolve(),a.candidate,a.cost)
