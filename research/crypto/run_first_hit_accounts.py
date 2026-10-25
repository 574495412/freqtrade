"""Run frozen first-hit forecasts in three native accounts, including a no-signal control."""

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.audit_first_hit_account import campaign_audit
from research.crypto.performance_scorecard import card


def run(plan_path, output):
    plan = read(plan_path)
    causal = read(output / "causality_checks.json")
    if not causal["passed"] or causal["plan_sha256"] != sha(plan_path):
        raise ValueError("Frozen protocol has no matching actual-data prefix evidence")
    protocol = Path(plan["metric_contract"]["path"])
    if sha(protocol) != plan["metric_contract"]["sha256"]:
        raise ValueError("Metric contract changed")
    metrics = Path(__file__).with_name("performance_scorecard.py")
    if sha(metrics) != read(protocol)["metric_source_sha256"]:
        raise ValueError("Metric implementation changed after registration")
    summary_path = output / "summary.json"
    if summary_path.exists():
        raise ValueError("Preserve previous completed study")
    identity, results = source_identity(), []
    reference = plan["native_source_reference"]
    if (
        sha(Path(reference["path"])) != reference["sha256"]
        or read(Path(reference["path"]))["source_identity"] != identity
    ):
        raise ValueError("Frozen native engine reference changed")
    for candidate in plan["candidates"]:
        name = candidate["name"]
        folder = output / "train" / name / "cost_3bps"
        log = output / f"{name}.log"
        if log.exists() or folder.exists():
            raise ValueError("Existing partial account requires explicit disposition")
        print({"started_utc": datetime.now(UTC).isoformat(), "candidate": name}, flush=True)
        with log.open("w") as stream:
            subprocess.run(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "research.crypto.run_first_hit_account",
                    "--plan",
                    str(plan_path),
                    "--output",
                    str(folder),
                    "--candidate",
                    name,
                    "--phase",
                    "train",
                    "--cost",
                    "3",
                ],
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
            )
        audit = campaign_audit(folder, name)
        if not audit["campaign_risk_audit_passed"]:
            raise ValueError("Actual campaign risk failed independent reconstruction")
        record, daily = card(folder)
        save(folder / "official_metric_scorecard.json", record)
        daily.to_parquet(folder / "daily_marked_returns.parquet", index=False)
        result = read(folder / "result.json")
        results.append({"result": result, "campaign_audit": audit, "scorecard": record})
        save(
            output / "completed_account_checkpoint.json",
            {
                "accounts_completed": len(results),
                "accounts": results,
                "registered_accounts": len(plan["candidates"]),
                "orders_enabled": False,
            },
        )
        print(
            {
                "completed_utc": datetime.now(UTC).isoformat(),
                "candidate": name,
                "net_return": result["net_return"],
                "trades": result["closed_trades"],
                "net_winrate": result["win_rate"],
                "sharpe_proxy": record["marked_account"]["binance_no_transfer_sharpe_proxy"],
                "sleeves": audit["sleeves"],
            },
            flush=True,
        )
    if identity != source_identity():
        raise ValueError("Native engine changed during study")
    summary = {
        "accounts": results,
        "native_accounts": len(results),
        "all_campaign_risk_audits_passed": True,
        "all_registered_accounts_terminal": True,
        "plan_sha256": sha(plan_path),
        "metric_contract_sha256": sha(protocol),
        "metric_source_sha256": sha(metrics),
        "study_runner_sha256": sha(Path(__file__)),
        "native_source_files_unchanged": len(identity),
        "qualified_candidates": [],
        "production_eligible": False,
        "orders_enabled": False,
        "joint_target_decision": (
            "Separately registered native-capacity diagnostic; "
            "first-hit joint unit screen remains failed"
        ),
    }
    save(summary_path, summary)
    print({"study_accounts_completed": len(results), "orders_enabled": False}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.plan.absolute(), args.output.absolute())
