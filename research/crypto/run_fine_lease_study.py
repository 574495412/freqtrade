"""Three frozen minute-detail policies; stress only after the registered primary mechanism gate."""

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.audit_fine_lease import campaign_audit
from research.crypto.performance_scorecard import card


def run(plan_path, output):
    plan = read(plan_path)
    protocol = Path(plan["metric_contract"]["path"])
    if sha(protocol) != plan["metric_contract"]["sha256"]:
        raise ValueError("Metric contract changed")
    if (
        sha(Path(__file__).with_name("performance_scorecard.py"))
        != read(protocol)["metric_source_sha256"]
    ):
        raise ValueError("Metric implementation changed")
    prefix = read(output / "causality_checks.json")
    if not prefix["passed"] or prefix["plan_sha256"] != sha(plan_path):
        raise ValueError("Native signal prefix checks missing")
    if (output / "summary.json").exists():
        raise ValueError("Preserve prior study")
    identity = source_identity()
    if identity != read(Path(plan["native_source_reference"]["path"]))["source_identity"]:
        raise ValueError("Native source changed")
    accounts, skipped = [], []
    for candidate in plan["candidates"]:
        costs = [3]
        for cost in costs:
            name = candidate["name"]
            folder = output / "train" / name / f"cost_{cost}bps"
            log = output / f"{name}_cost_{cost}bps.log"
            if folder.exists() or log.exists():
                raise ValueError("Existing incomplete account requires versioned disposition")
            print(
                {"started_utc": datetime.now(UTC).isoformat(), "candidate": name, "cost": cost},
                flush=True,
            )
            with log.open("x") as stream:
                subprocess.run(
                    [
                        sys.executable,
                        "-u",
                        "-m",
                        "research.crypto.run_fine_lease_account",
                        "--plan",
                        str(plan_path),
                        "--output",
                        str(folder),
                        "--candidate",
                        name,
                        "--phase",
                        "train",
                        "--cost",
                        str(cost),
                    ],
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            audit = campaign_audit(folder, name)
            score, daily = card(folder)
            save(folder / "official_metric_scorecard.json", score)
            daily.to_parquet(folder / "daily_marked_returns.parquet", index=False)
            result = read(folder / "result.json")
            accounts.append({"result": result, "campaign_audit": audit, "scorecard": score})
            save(
                output / "completed_account_checkpoint.json",
                {
                    "accounts": accounts,
                    "accounts_completed": len(accounts),
                    "conditional_stress_skipped": skipped,
                    "registered_primary_accounts": 3,
                    "maximum_accounts_with_stress": 9,
                    "orders_enabled": False,
                },
            )
            print(
                {
                    "completed_utc": datetime.now(UTC).isoformat(),
                    "candidate": name,
                    "cost": cost,
                    "net_return": result["net_return"],
                    "win": result["win_rate"],
                    "sharpe_proxy": score["marked_account"]["binance_no_transfer_sharpe_proxy"],
                    "sleeves": audit["sleeves"],
                },
                flush=True,
            )
            if cost == 3:
                small, core = audit["sleeves"]["small"], audit["sleeves"]["core"]
                checks = {
                    "primary_economic_screen": result["economic_audit"]["passed"],
                    "both_sleeves_net_positive": small["net_profit_usdt"] > 0
                    and core["net_profit_usdt"] > 0,
                    "core_major_profit": core["net_profit_usdt"] > small["net_profit_usdt"],
                    "small_frequency": small["closed_positions"]
                    / score["marked_account"]["calendar_days"]
                    >= 3,
                }
                if all(checks.values()):
                    costs.extend([0, 10])
                else:
                    skipped.append(
                        {
                            "candidate": name,
                            "costs_not_admitted": [0, 10],
                            "preregistered_mechanism_checks": checks,
                        }
                    )
    if source_identity() != identity:
        raise ValueError("Native source changed during study")
    for item in plan["runtime_sources"] + plan["strategy_sources"]:
        if sha(Path(item["path"])) != item["sha256"]:
            raise ValueError("Frozen study implementation changed")
    save(
        output / "summary.json",
        {
            "accounts": accounts,
            "native_accounts": len(accounts),
            "registered_primary_accounts": 3,
            "maximum_accounts_with_conditional_stress": 9,
            "conditional_stress_not_admitted": skipped,
            "all_registered_accounts_terminal": True,
            "all_campaign_risk_audits_passed": True,
            "plan_sha256": sha(plan_path),
            "metric_contract_sha256": plan["metric_contract"]["sha256"],
            "native_source_files_unchanged": len(identity),
            "qualified_candidates": [],
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print(
        {
            "study_terminal": True,
            "computed_accounts": len(accounts),
            "skipped_stress": len(skipped),
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan.absolute(), args.output.absolute())
