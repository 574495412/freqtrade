"""Reject tampered frozen inputs before loading source candles or writing output."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/"user_data/crypto_quant/spot_baseline_long_run_20261105_v001"
NAME="nfi-x8-spot"
COST=13
SCRIPT=ROOT/"research/crypto/audit_spot_5m_order_equity_20261105_v3.py"

def main():
    original=SOURCE/f"{NAME}-cost{COST}"
    expectations={"root_plan":"Registered plan differs from independent audit", "account_plan":"Account plan differs from root registration", "hourly_curve":"Hourly curve differs from independent audit"}
    results=[]
    with tempfile.TemporaryDirectory(prefix="spot-5m-v3-negative-") as raw:
        parent=Path(raw)
        for case,expected in expectations.items():
            root=parent/case; root.mkdir()
            shutil.copyfile(SOURCE/"registered_plan.json",root/"registered_plan.json")
            folder=root/f"{NAME}-cost{COST}";folder.mkdir()
            for filename in ("registered_plan.json","result.json","independent-account-audit.json","hourly_equity.parquet"):
                shutil.copyfile(original/filename,folder/filename)
            if case in ("root_plan","account_plan"):
                path=root/"registered_plan.json" if case=="root_plan" else folder/"registered_plan.json"
                plan=json.loads(path.read_text());plan["initial_equity_usdt"]+=1;path.write_text(json.dumps(plan)+"\n")
            else:
                with (folder/"hourly_equity.parquet").open("ab") as f: f.write(b"tamper-test")
            p=subprocess.run([sys.executable,str(SCRIPT),str(root),NAME,str(COST)],text=True,capture_output=True)
            if p.returncode==0 or expected not in p.stderr:
                raise AssertionError(f"Guard {case} failed: return={p.returncode}, stderr={p.stderr}")
            if (folder/"independent-5m-order-equity-v3.parquet").exists() or (folder/"independent-5m-order-equity-v3.json").exists():
                raise AssertionError("Tampered input caused output")
            # No candle or native archive exists inside temporary account; the
            # expected error proves the guard ran before any archive access.
            results.append({"case":case,"rejected":True,"expected_error":expected,"output_written":False})
    print(json.dumps(results,indent=2))

if __name__=="__main__": main()
