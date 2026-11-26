"""Prioritize the recent two-year comparison on the existing native runner."""

import argparse
import json
import subprocess
import sys

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .continuous_november_run import ROOT as ORIGINAL, verify_native_seam
from .continuous_run import ROOT_RUN, SLOTS, frozen_roles, launch, register, report_continuous
from .inputs import HERE, OUT


PROTOCOL = HERE / "recent_two_year_protocol.json"
GATE = HERE / "recent_two_year_native_preflight.json"
INPUTS = OUT / "recent_two_year_inputs_20261126"
RUNS = OUT / "recent_two_year_native_20261126"
PROGRESS = HERE / "recent_two_year_wallet_progress.json"


def register_accounts():
    previous = read(HERE / "continuous_november_source_gate.json")
    assert previous["checked_full_native_runner_and_sources"]
    manifest = read(checked(previous["manifest"]))
    assert checked(previous["manifest"]) == ORIGINAL / "shards.json"
    contract = read(PROTOCOL)
    window = contract["window"]
    start, stop = (pd.Timestamp(window[name]) for name in ("start_utc", "end_exclusive_utc"))
    assert manifest["stop_exclusive"] == window["end_exclusive_utc"]
    same = read(checked(manifest["schedule"]))
    days = {day.strftime("%Y-%m-%d"): same["days"][day.strftime("%Y-%m-%d")]
            for day in pd.date_range(start, stop, freq="D", inclusive="left")}
    union = sorted({pair for row in days.values() for pair in row["pairs"]})
    assert len(days) == window["pairlist_days"] and all(len(set(row["pairs"])) == 80 for row in days.values())
    INPUTS.mkdir(parents=True, exist_ok=True)
    schedule = write_once(INPUTS / "schedule.json", {"days": days, "union": union})
    inputs = write_once(INPUTS / "shards.json", {**manifest, "first_time": window["start_utc"], "schedule": schedule,
        "daily_decisions": len(days), "five_minute_marks_expected": window["five_minute_marks"],
        "full_history_manifest": previous["manifest"], "orders_enabled": False})
    roles = frozen_roles()
    costs = (contract["primary_cost_bps_per_side"], contract["secondary_cost_bps_per_side"])
    accounts = {(slots, cost): register(roles[slots], inputs, RUNS / f"full_{slots}_{cost}bp/registration",
                                       is_witness=False, wallet_protocol=PROTOCOL)
                for cost in costs for slots in SLOTS}
    for runner, config in accounts.values():
        plan = read(checked(config))
        assert [row["name"] for row in plan["periods"]] == ["continuous", "2025", "2026"]
        assert all(pd.Timestamp(row["start"]) < pd.Timestamp(row["end"]) for row in plan["periods"])
        checked(runner)
    return inputs, accounts, costs


def preflight(inputs, accounts, cost):
    runner, configuration = accounts[(12, cost)]
    plan = read(checked(configuration))
    function = verify_native_seam.__module__
    bootstrap = ("import json,sys; sys.path.insert(0,sys.argv[1]);"
                 f"from {function} import verify_native_seam;"
                 "print(json.dumps(verify_native_seam(*sys.argv[2:])))")
    command = [sys.executable, "-B", "-c", bootstrap, plan["engine_source"],
               str(checked(runner)), str(checked(configuration))]
    verified = json.loads(subprocess.check_output(command, text=True))
    new = read(checked(inputs))
    write_once(GATE, {"manifest": inputs, "protocol": receipt(PROTOCOL), "native_boundary_reads": verified,
                      "verified_captured_days": new["daily_decisions"],
                      "expected_five_minute_marks": new["five_minute_marks_expected"],
                      "plans": {f"{slots}_{fee}bp": plan for (slots, fee), (_, plan) in accounts.items()},
                      "strategy_inputs_bound_to_native_engine": True, "orders_enabled": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("primary", "sensitivity", "report"))
    options = parser.parse_args()
    if options.mode == "report":
        report_continuous(INPUTS, RUNS, PROGRESS)
        return
    proof = read(ROOT_RUN / "witness/matched.json")
    assert proof["exact_fills"] and proof["exact_full_marked_wallet"]
    inputs, accounts, (primary, secondary) = register_accounts()
    preflight(inputs, accounts, primary)
    cost = primary if options.mode == "primary" else secondary
    for slots in SLOTS:
        launch(accounts[(slots, cost)], RUNS / f"full_{slots}_{cost}bp", cost)
        report_continuous(INPUTS, RUNS, PROGRESS)


if __name__ == "__main__":
    main()
