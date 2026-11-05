"""Attach small, independent postprocessing audits to serial spot accounts."""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT_ROOT = ROOT / "user_data/crypto_quant/spot_baseline_long_run_20261105_v001"
SCRIPTS = [
    ("audit_spot_5m_order_equity_20261105_v3.py", "independent-5m-order-equity-v3.json"),
    ("audit_spot_fill_candles_20261105_v2.py", "fill-candle-evidence-v2.json"),
    ("summarize_spot_open_fill_20261105.py", "open-fill-statistics.json"),
    ("audit_spot_extreme_event_20261105_v2.py", "extreme-event-realized-attribution-v2.json"),
    ("audit_spot_event_delay_sensitivity_20261105_v2.py", "extreme-event-5m-delay-fixed-quantity-sensitivity-v2.json"),
]

def main():
    plan = json.loads((ACCOUNT_ROOT / "registered_plan.json").read_text())
    for candidate in plan["candidates"]:
        for cost in plan["total_cost_bps_per_side"]:
            name = candidate["name"]
            folder = ACCOUNT_ROOT / f"{name}-cost{cost}"
            while True:
                try:
                    json.loads((folder / "independent-account-audit.json").read_text())
                    break
                except (FileNotFoundError, json.JSONDecodeError):
                    state = json.loads((ACCOUNT_ROOT / "process.json").read_text())
                    if state["state"] in ("FAILED","AUDIT_FAILED"):
                        raise RuntimeError("Native account matrix stopped: " + json.dumps(state))
                    time.sleep(10)
            for script, output in SCRIPTS:
                if (folder / output).exists():
                    # Existing explicitly audited outputs are immutable and not rewritten.
                    json.loads((folder / output).read_text())
                    continue
                cmd = [sys.executable, str(ROOT / "research/crypto" / script), str(ACCOUNT_ROOT), name, str(cost)]
                with (folder / (script + ".log")).open("x") as log:
                    code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
                if code:
                    raise RuntimeError(f"{script} failed for {name}/{cost}; see its log")
            print(json.dumps({"candidate":name, "cost_bps_per_side":cost, "state":"ADDITIONAL_AUDITS_COMPLETE", "at":datetime.now(timezone.utc).isoformat()}),flush=True)
    (ACCOUNT_ROOT / "additional-audits-complete.json").write_text(json.dumps({"accounts":len(plan["candidates"])*len(plan["total_cost_bps_per_side"]), "finished_at":datetime.now(timezone.utc).isoformat()},indent=2)+"\n")

if __name__ == "__main__":
    main()
