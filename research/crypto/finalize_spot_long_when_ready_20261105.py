"""Complete the registered comparison after the serial account and audit jobs."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ACCOUNTS=ROOT/"user_data/crypto_quant/spot_baseline_long_run_20261105_v001"
OUT=ROOT/"user_data/crypto_quant/spot_baseline_long_analysis_20261105_v001/comparison.json"

def main():
    while not ((ACCOUNTS/"completion.json").is_file() and (ACCOUNTS/"additional-audits-complete.json").is_file()):
        state=json.loads((ACCOUNTS/"process.json").read_text())
        if state["state"] in ("FAILED","AUDIT_FAILED"):
            raise RuntimeError("Account matrix failed: "+json.dumps(state))
        time.sleep(10)
    if OUT.exists():
        print("Comparison already exists",flush=True)
        return
    proc=subprocess.run([sys.executable,str(ROOT/"research/crypto/compare_spot_long_20261105.py")])
    if proc.returncode: raise SystemExit(proc.returncode)

if __name__=="__main__": main()
