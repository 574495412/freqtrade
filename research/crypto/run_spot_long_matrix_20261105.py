"""Run and audit frozen spot accounts serially; release only audited disposable cache."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def now():
    return datetime.now(timezone.utc).isoformat()

def save(path, value):
    tmp = path.with_suffix(path.suffix + ".writing")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main(plan_path, output):
    plan = json.loads(plan_path.read_text())
    output.mkdir(parents=True, exist_ok=False)
    (output / "registered_plan.json").write_bytes(plan_path.read_bytes())
    jobs = [(c["name"], cost) for c in plan["candidates"] for cost in plan["total_cost_bps_per_side"]]
    for i, (name, cost) in enumerate(jobs, 1):
        label = f"{name}-cost{cost}"
        folder = output / label
        cmd = [sys.executable, str(plan_path.parent / "run_spot_comparison.py"), "--plan", str(plan_path), "--candidate", name, "--cost-bps", str(cost), "--output", str(folder)]
        with (output / (label + ".log")).open("x") as log:
            p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            state = {"index":i, "total":len(jobs), "candidate":name, "cost":cost, "pid":p.pid, "command":cmd, "state":"RUNNING", "started_at":now()}
            save(output / "process.json", state)
            print(json.dumps(state), flush=True)
            code = p.wait()
        if code:
            state.update(state="FAILED", exit_code=code, finished_at=now())
            save(output / "process.json", state)
            raise SystemExit(code)
        audit_cmd = [sys.executable, str(ROOT / "research/crypto/audit_spot_accounts_20261105.py"), str(output), name, str(cost)]
        with (output / (label + ".audit.log")).open("x") as log:
            checked = subprocess.run(audit_cmd, stdout=log, stderr=subprocess.STDOUT)
        if checked.returncode:
            state.update(state="AUDIT_FAILED", exit_code=checked.returncode, finished_at=now())
            save(output / "process.json", state)
            raise SystemExit(checked.returncode)
        audit = json.loads((folder / "independent-account-audit.json").read_text())
        archive, = (folder / "native_results").glob("backtest-result-*.zip")
        for path, key in ((archive,"archive_sha256"),(folder / "result.json","result_sha256"),(folder / "hourly_equity.parquet","curve_sha256")):
            if sha(path) != audit[key]:
                raise ValueError("Audited artifact changed before cache cleanup")
        cache = folder / "lossless_cache"
        if not cache.is_dir() or cache.is_symlink():
            raise ValueError("Unexpected cache path")
        size = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
        shutil.rmtree(cache)
        save(folder / "cache-cleanup-receipt.json", {"removed_exact_path":str(cache), "bytes":size, "at_utc":now(), "verified_audit":str(folder / "independent-account-audit.json"), "retained":["native archive", "result", "hourly NAV", "independent audit", "logs"]})
        state.update(state="COMPLETED_AND_AUDITED", exit_code=0, finished_at=now())
        save(output / "process.json", state)
        print(json.dumps(state), flush=True)
    save(output / "completion.json", {"accounts_completed_and_audited":len(jobs), "orders_enabled":False, "production_eligible":False})

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    main(a.plan.resolve(), a.output.resolve())
