"""Bounded sequential native fixtures; retain every child terminal receipt."""

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime

from research.crypto.joint_rules_native_20261117_v1.prepare import HERE, OUT, ROOT, save, sha


def main():
    plan = json.loads((HERE / "fixture_plan.json").read_text())
    assert not (HERE / "execution_complete.json").exists()
    rows = []
    for case in plan["cases"]:
        for path, digest in plan["source_hashes"].items():
            assert sha(path) == digest, path
        assert shutil.disk_usage(ROOT).free >= 1024**3
        name = case["name"]
        log = HERE / f"native_{name}.log"
        started = datetime.now(UTC).isoformat()
        command = [
            sys.executable,
            "-B",
            "-m",
            "research.crypto.joint_rules_native_20261117_v1.run_native",
            "--case",
            name,
        ]
        with log.open("x") as f:
            process = subprocess.run(
                command, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, timeout=180, check=False
            )
        record = dict(
            case=name,
            started_at=started,
            ended_at=datetime.now(UTC).isoformat(),
            command=command,
            returncode=process.returncode,
            log_sha256=sha(log),
            expected=case["expected"],
            producer_plan_sha256=sha(HERE / "fixture_plan.json"),
        )
        save(OUT / "runs" / name / "process_receipt.json", record)
        rows.append(record)
        if (process.returncode == 0) != (case["expected"] == "completed"):
            save(
                HERE / "execution_failure.json",
                dict(cases=rows, error="Unexpected native terminal status"),
            )
            raise RuntimeError(f"Unexpected fixture terminal {name}: {process.returncode}")
        print("TERMINAL", name, process.returncode, flush=True)
    save(
        HERE / "execution_complete.json",
        dict(
            cases=rows,
            synthetic_only=True,
            completed_native_cases=2,
            expected_rejected_cases=3,
            financial_economic_claim=False,
            new_historical_strategy_accounts=0,
            orders_enabled=False,
            goal_complete=False,
        ),
    )


if __name__ == "__main__":
    main()
