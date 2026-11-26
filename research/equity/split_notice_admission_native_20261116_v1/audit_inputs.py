"""Independent input/source inventory; never compute a funded account."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
VIBE = Path("/Users/chrise/GitHub/Vibe-Trading")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, value):
    if name == "protocol.json" and (HERE / name).exists():
        raise FileExistsError("Preserve the original input protocol;use a new study for changes")
    (HERE / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


def main():
    plan_path = HERE.parent / "native_session_admission_20261114_v1/plan.json"
    plan = json.loads(plan_path.read_text())
    save(
        "protocol.json",
        {
            "frozen_at": datetime.now(UTC).isoformat(),
            "scope": (
                "Split notice revision input and source semantics only;not economic preregistration"
            ),
            "original_universe": {"US": 503, "HK": 11},
            "account_window": ["2025-01-01", "2026-08-31"],
            "currencies": ["USD", "HKD"],
            "parent_plan": str(plan_path),
            "parent_plan_sha256": sha(plan_path),
            "no_price_split_correction_or_share_mutation": True,
            "no_strategy_sign_or_weights_adopted": True,
            "source_mechanism": (
                "Anticipation/attention around upcoming split;not effective split bookkeeping"
            ),
            "input_gates": [
                "Both supplied publication and observation clocks required",
                (
                    "Visible publication orders revisions;"
                    "future amendments do not rewrite earlier views"
                ),
                "Old/new price factor is reciprocal of new/old quantity factor",
                "No absence-as-zero,no implicit execution on planned date,no share-class alias",
            ],
            "no_new_accounts": True,
            "no_new_factor_analysis": True,
            "production_enabled": False,
            "goal_complete": False,
        },
    )
    rows = []
    for record in plan["records"]:
        for name in ("source", "staged"):
            assert sha(record[name]) == record[name + "_sha256"], record[name]
        columns = list(pd.read_csv(record["source"], nrows=0).columns)
        rows.append(
            {
                "symbol": record["symbol"],
                "calendar": record["calendar"],
                "source": record["source"],
                "source_sha256": record["source_sha256"],
                "staged": record["staged"],
                "staged_sha256": record["staged_sha256"],
                "columns": columns,
                "notice_columns": [
                    c
                    for c in columns
                    if any(
                        t in c.lower()
                        for t in (
                            "split",
                            "announcement",
                            "published",
                            "observed",
                            "distribution",
                            "corporate",
                        )
                    )
                ],
            }
        )
    assert len(rows) == 514
    example = next(r for r in rows if r["symbol"] == "ORLY.US")
    frame = pd.read_csv(example["source"], float_precision="round_trip")
    save(
        "input_audit.json",
        {
            "original_files": len(rows),
            "original_and_staged_hash_checks": 2 * len(rows),
            "US": sum(r["calendar"] == "XNYS" for r in rows),
            "HK": sum(r["calendar"] == "XHKG" for r in rows),
            "files_with_notice_columns": sum(bool(r["notice_columns"]) for r in rows),
            "no_event_zero_inference": True,
            "source_master_survivorship_certified": False,
            "orly_quote_boundary": frame.loc[
                frame.date.isin(["2025-06-09", "2025-06-10"]), ["date", "open", "close", "volume"]
            ].to_dict("records"),
            "price_unit_change_is_not_account_loss_or_alpha": True,
            "rows": rows,
        },
    )
    owners = [
        "src/skills/factor-research/SKILL.md",
        "src/skills/strategy-dev-manager/SKILL.md",
        "src/factors/registry.py",
        "src/tools/factor_analysis_tool.py",
        "src/tools/backtest_tool.py",
        "src/tools/autopilot_tool.py",
    ]
    old = json.loads(
        (HERE.parent / "short_position_admission_native_20261116_v1/ownership.json").read_text()
    )
    old_hashes = {r["path"]: r["sha256"] for r in old["current_owner_files"]}
    save(
        "ownership.json",
        {
            "vibe_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=VIBE, text=True
            ).strip(),
            "freqtrade_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "current_owner_files": [
                {
                    "path": str(p),
                    "sha256": sha(p),
                    "same_as_previous_receipt": old_hashes.get(str(p)) == sha(p),
                }
                for p in [VIBE / "agent" / name for name in owners]
            ],
            "native_freqtrade_commit": "c051e9065c5b677f03817dd1f83cb980af1ed684",
            "new_modules": (
                "Local native DataProvider extensions;not upstream stock-account support"
            ),
            "pipeline": (
                "Registry→actual factor_analysis→supported signals→"
                "complete native financial account"
            ),
            "ordinary_equity_financial_entry": (
                "Still closed;no registration substitutes for execution"
            ),
            "source_algorithm_ported": False,
            "historical_stock_accounts": 0,
            "portfolios": 0,
            "bench": 0,
        },
    )
    print("Verified", len(rows), "original and", len(rows), "staged inputs;no account")


if __name__ == "__main__":
    main()
