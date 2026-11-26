"""Freeze and run the earlier annual transfer of the existing fourteen-slot cap."""

import argparse
import os
from pathlib import Path
from time import sleep

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt
from research.crypto.nfi80_online_baseline_20261125_v1.audit import audit_short_rebuy_cap

from .accounts import write_once
from .annual_compare import result
from .annual_trade_scale import describe, load_account
from .inputs import HERE
from .low_disk_run import BLOCK_RESEARCH, execute, register
from .short_rebuy_14_wallet import STRATEGY, native_preflight


COST = 15
FOLDER = BLOCK_RESEARCH / "annual_2024_boost_14_short_rebuy_15bp"
EARLIER = HERE / "annual_2024_boost14_15bp_verified.json"
RECENT = HERE / "short_rebuy_14_wallet_preflight.json"


def source_file(rows, suffix):
    value, = [ref for ref in rows if Path(ref["path"]).name.endswith(suffix)]
    return value


def register_annual():
    annual_ref, current_ref = receipt(EARLIER), receipt(RECENT)
    verified = read(checked(annual_ref))
    annual = verified["accounts"]["boost_14"]
    effective = read(checked(source_file(annual["receipts"], "registered_plan.json")))
    registration = read(checked(effective["storage_registration"]))
    source = read(checked(registration["parent"]))
    recent_plan = read(checked(read(checked(current_ref))["by_cost"][str(COST)]["plan"]))
    control, = source["candidates"]
    capped, = recent_plan["candidates"]
    assert (source["start"], source["end"], source["engine_commit"]) == (
        "2024-01-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00", recent_plan["engine_commit"]
    )
    assert source["initial_equity_usdt"] == recent_plan["initial_equity_usdt"] == 1000
    assert source["allowed_leverage"] == recent_plan["allowed_leverage"] == 3
    assert control["strategy"] == "FlatBoostFourteenSlots"
    assert control["max_open_trades"] == capped["max_open_trades"] == 14 and capped["strategy"] == STRATEGY
    for filename, ref in control["source_files"].items():
        assert checked(ref).read_bytes() == checked(capped["source_files"][filename]).read_bytes()
    cfg = read(checked(control["config"]))
    scope = receipt(HERE / "short_rebuy_cap_2024_protocol.json")
    FOLDER.mkdir(parents=True, exist_ok=True)
    config = write_once(FOLDER / "candidate-config.json", {**cfg, "strategy": STRATEGY})
    candidate = {
        **control, "name": STRATEGY, "strategy": STRATEGY, "config": config,
        "source_files": capped["source_files"], "source_and_risk_behavior": read(checked(scope)),
    }
    parent = write_once(FOLDER / "candidate-parent-plan.json", {
        **source, "candidates": [candidate], "stress_protocol": scope,
        "matched_uncapped_year": annual_ref, "reused_recent_cap_preflight": current_ref,
        "capped_annual_results_observed_at_registration": False,
        "orders_enabled": False, "production_eligible": False,
    })
    runner, plan = register(checked(parent), FOLDER / "registration", "blocks")
    assert runner["sha256"] == effective["runner_sha256"]
    anchor = write_once(HERE / "short_rebuy_cap_2024_preflight.json", {
        "protocol": scope, "controls": annual_ref, "recent_cap_preflight": current_ref,
        "parent": parent, "runner": runner, "plan": plan, "engine_witness": native_preflight(plan),
        "source": receipt(Path(__file__)), "orders_enabled": False,
    })
    print("2024 annual account registered", plan["sha256"], flush=True)
    return anchor


def wait_for_observed_queue(pid):
    print(f"Waiting for the registered recent native cap queue {pid}", flush=True)
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        sleep(20)
    proof = read(HERE / "short_rebuy_14_wallet_comparison.json")
    assert set(proof["cases"]) == {"15", "8"}
    for link in proof["cases"].values():
        recent = read(checked(link))
        assert read(checked(recent["proof"]))["five_minute_nav_reconciliation"]["passed"]


def gmt(book):
    return [
        {"opened_utc": pd.Timestamp(t["open_timestamp"], unit="ms", tz="UTC").isoformat(),
         "tag": t["enter_tag"], "net_usdt": t["profit_abs"], "exit_reason": t["exit_reason"],
         "adds": sum(o["ft_is_entry"] for o in t["orders"]) - 1}
        for t in book if t["pair"] == "GMT/USDT:USDT" and t["is_short"]
    ]


def execute_annual(anchor):
    registered = read(checked(anchor))
    assert read(checked(registered["plan"]))["orders_enabled"] is False
    output = execute(checked(registered["parent"]), FOLDER, COST, "blocks")
    candidate, contract = result(FOLDER, COST, STRATEGY, year=2024)
    controls = read(checked(registered["controls"]))
    assert controls["account_contract"] == contract
    reference = controls["accounts"]["boost_14"]
    base_config = read(checked(source_file(reference["receipts"], "effective_config.json")))
    assert invariant_config(base_config) == invariant_config(read(output / "effective_config.json"))
    after, before = (describe(load_account(row).values()) for row in (candidate, reference))
    candidate_trades, baseline_trades = (
        stats_from(checked(source_file(row["receipts"], ".zip")))["trades"] for row in (candidate, reference)
    )
    budget = audit_short_rebuy_cap(output, candidate_trades)
    account = read(FOLDER / "account_reconciled.json")
    flags = {
        "higher_return": candidate["net_return"] > reference["net_return"],
        "no_higher_drawdown": candidate["drawdown"] <= reference["drawdown"],
        "no_fewer_large_winners": after["wins_at_least_1pct_of_equity"] >= before["wins_at_least_1pct_of_equity"],
        "cap_acted": budget["blocked_adjustment_requests"] > 0,
        "no_liquidations": account["liquidations"] == 0,
    }
    write_once(HERE / "short_rebuy_cap_2024_verified.json", {
        "preflight": anchor, "candidate": candidate, "control_14": reference,
        "control_12": controls["accounts"]["fixed_boost_12"], "contract": contract,
        "natural_capped": after, "natural_uncapped": before, "cap_budget": budget, "checks": flags,
        "gmt_shorts_capped": gmt(candidate_trades), "gmt_shorts_uncapped": gmt(baseline_trades),
        "passed_against_uncapped_14": all(flags.values()),
        "max_drawdown_no_worse_than_12": candidate["drawdown"] <= controls["accounts"]["fixed_boost_12"]["drawdown"],
        "orders_enabled": False, "production_eligible": False,
    })
    print("2024 annual cap check", flags, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "run"))
    parser.add_argument("--after-pid", type=int)
    args = parser.parse_args()
    anchor = register_annual()
    if args.mode == "run":
        if args.after_pid is not None:
            wait_for_observed_queue(args.after_pid)
        execute_annual(anchor)


if __name__ == "__main__":
    main()
