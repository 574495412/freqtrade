"""Bounded native TradingAgents review of a retrospective complete-account portfolio."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from freqtrade.research.data import read, save, sha


def review(run, ta_python, ta_root):
    if (run / "agent_attempt.json").exists() or (run / "selection.json").exists():
        raise ValueError("Portfolio review already attempted")
    if any((run / phase).exists() for phase in ("validation", "holdout", "continuous")):
        raise ValueError("Portfolio summaries must follow the training review")
    plan = read(run / "plan.json")
    rows = read(run / "training.json")
    if len(rows) != 3 or not all(row["economic_audit"]["passed"] for row in rows):
        save(
            run / "selection.json",
            {
                "agent": None,
                "control": "cash",
                "agent_review_status": "NOT_RUN_NO_TRAINING_ELIGIBLE",
                "future_results_included": False,
            },
        )
        return
    evidence = {
        "phase": "train",
        "future_results_included": False,
        "candidate_names": [plan["candidate"]],
        "training": rows,
        "protocol": {
            key: plan[key]
            for key in (
                "initial_equity_usdt",
                "maximum_open_positions",
                "fee_per_side",
                "extra_cost_bps_per_side",
                "screening_targets",
                "allocation",
                "rationale",
                "all_component_future_outcomes_already_observed",
                "independent_oos",
                "historical_evidence_status",
                "individual_components_passed_all_screens",
                "constraints",
            )
        },
    }
    body = json.dumps(evidence, ensure_ascii=False, allow_nan=False)
    if len(body.encode()) > 24000:
        raise ValueError("Portfolio evidence exceeds the bounded review budget")
    save(run / "agent_input.json", evidence)
    worker = Path(__file__).with_name("review_candidate.py").resolve()
    save(
        run / "agent_attempt.json",
        {
            "status": "STARTED",
            "input_sha256": sha(run / "agent_input.json"),
            "worker_sha256": sha(worker),
            "manager_sha256": sha(Path(__file__)),
        },
    )
    allowed = {
        "PATH",
        "HOME",
        "CODEX_HOME",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["PYTHONPATH"] = str(ta_root)
    process = subprocess.run(
        [str(ta_python), str(worker), "--worker"],
        input=body,
        capture_output=True,
        text=True,
        env=env,
        cwd=run,
        timeout=570,
        check=False,
    )
    if process.returncode:
        (run / "agent_failure.log").write_text(process.stderr)
        save(run / "agent_attempt.json", {"status": "FAILED", "returncode": process.returncode})
        raise RuntimeError("Native portfolio review failed; evidence preserved")
    response = json.loads(process.stdout)
    chosen = response["selection"]["candidate"]
    if chosen not in {plan["candidate"], "cash"} or len(response["calls"]) != 3:
        raise ValueError("Portfolio review exceeded its candidate or call budget")
    save(run / "agent_response.json", response)
    save(
        run / "selection.json",
        {
            "agent": chosen,
            "control": plan["candidate"],
            "agent_review_status": "COMPLETED",
            "input_sha256": sha(run / "agent_input.json"),
            "response_sha256": sha(run / "agent_response.json"),
            "future_results_included": False,
            "designer_component_future_history_already_seen": True,
            "orders_enabled": False,
        },
    )
    save(run / "agent_attempt.json", {"status": "COMPLETED", "calls": 3})
    print(read(run / "selection.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--tradingagents-python", required=True, type=Path)
    parser.add_argument("--tradingagents-root", required=True, type=Path)
    args = parser.parse_args()
    review(
        args.run.resolve(), args.tradingagents_python.absolute(), args.tradingagents_root.resolve()
    )
