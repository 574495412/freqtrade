"""Additional frozen capital stress; never reselect or change the primary experiment."""

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.research.data import read, save, sha
from research.crypto.trend_mainline.__main__ import account, verify
from research.crypto.trend_mainline.data import load


def stress(parent, output):
    plan = verify(parent)
    primary = read(parent / "evaluation.json")
    output.mkdir(parents=True, exist_ok=False)
    plan = dict(
        plan,
        study_kind="capital_stress_only",
        capacity_capitals_usdt=[1000000, 10000000],
        primary_run=str(parent),
        primary_evaluation_sha256=sha(parent / "evaluation.json"),
        registered_utc=datetime.now(UTC).isoformat(),
        primary_selection=primary["selected_candidate"],
    )
    source = Path(__file__)
    plan["source_files"] = {**plan["source_files"], str(source.resolve()): sha(source)}
    save(output / "plan.json", plan)
    save(
        output / "registration.json",
        {
            "plan_sha256": sha(output / "plan.json"),
            "purpose": "fixed larger capitals only; no selection or rule revision",
        },
    )
    for path in (output / "source_snapshot", output / "data"):
        path.mkdir()
    for name in plan["source_files"]:
        shutil.copyfile(name, output / "source_snapshot" / Path(name).name)
    for path in (parent / "data").iterdir():
        shutil.copyfile(path, output / "data" / path.name)
    shutil.copyfile(parent / "selection.json", output / "selection.json")
    data = load(output / "data", plan)
    rows = []
    for candidate in plan["candidates"] + ["LongOnlyControl"]:
        for capital in plan["capacity_capitals_usdt"]:
            for cost in plan["extra_cost_bps_per_side"]:
                rows.append(
                    account(
                        output,
                        data,
                        plan,
                        candidate,
                        "continuous",
                        capital,
                        cost,
                        plan["validation_start"],
                        plan["end"],
                    )
                )
    save(
        output / "evaluation.json",
        {
            "schema": "crypto-trend-capital-stress.v1",
            "selected_candidate": primary["selected_candidate"],
            "accounts": rows,
            "plan_sha256": sha(output / "plan.json"),
            "selection_sha256": sha(output / "selection.json"),
            "primary_evaluation_sha256": plan["primary_evaluation_sha256"],
            "primary_selection_unchanged": True,
            "independent_oos": False,
            "production_eligible": False,
            "orders_enabled": False,
            "expansion_allowed": False,
        },
    )
    save(
        output / "completion.json",
        {"accounts": len(rows), "evaluation_sha256": sha(output / "evaluation.json")},
    )
    return {"accounts": len(rows), "primary_selection_unchanged": True}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parent", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(stress(a.parent.resolve(), a.output.resolve())))
