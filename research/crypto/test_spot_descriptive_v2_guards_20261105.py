"""Tamper-check descriptive audit lineage in disposable temporary copies."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/"user_data/crypto_quant/spot_baseline_long_run_20261105_v001"
SCRIPTS=["audit_spot_fill_candles_20261105_v2.py", "audit_spot_extreme_event_20261105_v2.py", "audit_spot_event_delay_sensitivity_20261105_v2.py"]

def main():
    results=[]
    with tempfile.TemporaryDirectory(prefix="spot-descriptive-v2-negative-") as raw:
        for script in SCRIPTS:
            for case in ("root_plan","account_plan","result"):
                root=Path(raw)/(script+case);root.mkdir()
                shutil.copyfile(SOURCE/"registered_plan.json",root/"registered_plan.json")
                source=SOURCE/"nfi-x8-spot-cost13";target=root/source.name;target.mkdir()
                for filename in ("registered_plan.json","result.json","independent-account-audit.json"):
                    shutil.copyfile(source/filename,target/filename)
                p=root/"registered_plan.json" if case=="root_plan" else target/("registered_plan.json" if case=="account_plan" else "result.json")
                data=json.loads(p.read_text());data["tampered_test_field"]=True;p.write_text(json.dumps(data)+"\n")
                cmd=[sys.executable,str(ROOT/"research/crypto"/script),str(root),"nfi-x8-spot","13"]
                run=subprocess.run(cmd,capture_output=True,text=True)
                expected="Result differs from independent audit" if case=="result" else ("Plan does not match independent audit" if script.startswith("audit_spot_event_delay") else "Registered plan differs from independent audit")
                if run.returncode==0 or expected not in run.stderr:
                    raise AssertionError(f"{script}, {case}: {run.stderr}")
                if set(x.name for x in target.iterdir())!={"registered_plan.json","result.json","independent-account-audit.json"}:
                    raise AssertionError("Guard wrote an output")
                results.append({"script":script,"tamper":case,"rejected":True,"no_output":True})
    print(json.dumps(results,indent=2))

if __name__=="__main__": main()
