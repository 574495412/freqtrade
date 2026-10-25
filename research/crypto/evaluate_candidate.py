"""Evaluate frozen selections across all registered future windows and costs."""

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity


def evaluate(run: Path):
    plan = read(run / "plan.json")
    selection = read(run / "selection.json")
    if selection["future_results_included"] is not False:
        raise ValueError("Selection did not exclude future evaluation")
    if (run / "evaluation_attempt.json").exists():
        raise ValueError("Evaluation already attempted; preserve its evidence")
    names = {c["name"] for c in plan["candidates"]}
    candidates = sorted({selection["agent"], selection["control"]} - {None, "cash"})
    if not set(candidates).issubset(names):
        raise ValueError("Selection contains an unregistered candidate")
    runner = Path(__file__).with_name("run_candidate.py")
    observer = Path(__file__).with_name("run_nfi.py")
    identity = source_identity()
    for name in candidates:
        for cost in plan["extra_cost_bps_per_side"]:
            folder = run / "train" / name / f"cost_{cost}bps"
            frozen = read(folder / "freeze.json")
            if (
                read(folder / "plan.json") != plan
                or not read(folder / "result.json")["economic_audit"]["passed"]
                or frozen["source_identity"] != identity
                or frozen["runner_sha256"] != sha(runner)
                or frozen["observer_sha256"] != sha(observer)
            ):
                raise ValueError("Training eligibility or source identity changed")
    phases = ["validation", "holdout", "continuous"]
    if any((run / phase).exists() for phase in phases):
        raise ValueError("Future evaluation folders already exist")
    receipt = {
        "status": "STARTED",
        "selection_sha256": sha(run / "selection.json"),
        "plan_sha256": sha(run / "plan.json"),
        "evaluator_sha256": sha(Path(__file__)),
        "maximum_concurrency": 2,
        "orders_enabled": False,
    }
    save(run / "evaluation_attempt.json", receipt)

    def account(job):
        name, phase, cost = job
        folder = run / phase / name / f"cost_{cost}bps"
        folder.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "research.crypto.run_candidate",
            "--plan",
            str(run / "plan.json"),
            "--output",
            str(folder),
            "--candidate",
            name,
            "--phase",
            phase,
            "--cost",
            str(cost),
        ]
        with folder.with_suffix(".log").open("x") as log:
            process = subprocess.run(command, stdout=log, stderr=log, check=False)
        status = {"command": command, "returncode": process.returncode}
        save(folder.with_suffix(".process.json"), status)
        if process.returncode:
            raise RuntimeError(f"Native evaluation failed: {folder}")
        row = read(folder / "result.json")
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return dict(row, result_sha256=sha(folder / "result.json"))

    jobs = [
        (name, phase, cost)
        for name in candidates
        for phase in phases
        for cost in plan["extra_cost_bps_per_side"]
    ]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(account, jobs))
        chosen = selection["agent"]
        selected = [r for r in rows if r["candidate"] == chosen]
        passed = bool(selected) and all(r["economic_audit"]["passed"] for r in selected)
        result = {
            "schema": "crypto-quant-registered-evaluation.v1",
            "selection_sha256": receipt["selection_sha256"],
            "selected_candidate": chosen,
            "selected_historical_screen_passed": passed,
            "rows": rows,
            "independent_oos": False,
            "limitations": plan.get("scope_limitations", []),
            "historical_evidence_status": plan["historical_evidence_status"],
            "orders_enabled": False,
            "production_eligible": False,
        }
        save(run / "evaluation.json", result)
        save(run / "evaluation_attempt.json", dict(receipt, status="COMPLETED"))
        return result
    except BaseException:
        save(run / "evaluation_attempt.json", dict(receipt, status="FAILED"))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.run.resolve()), ensure_ascii=False, indent=2))
