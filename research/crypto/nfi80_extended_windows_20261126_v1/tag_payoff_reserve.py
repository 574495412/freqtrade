"""Run the frozen NFI entry-tag multiplier on the matched recent reserve."""

from collections import defaultdict
import json
from math import exp, isclose
from pathlib import Path

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import ROOT, checked, read, receipt, save

from .accounts import COSTS, write_once
from .annual_trade_scale import describe, load_account
from .inputs import HERE, OUT
from .low_disk_run import BLOCK_RESEARCH, execute


ROLE = "tag_payoff_12"
STRATEGY = "NFITagPayoffBoost"
MODEL = (ROOT.parent / "Vibe-Trading/agent/research/nfi_entry_payoff_20261126_v1" / "model.json")


def make_plan():
    output = OUT / "tag_payoff_registration"
    output.mkdir(exist_ok=True)
    controls = {str(cost): receipt(HERE / f"recent_window_{cost}bp_controls.json") for cost in COSTS}
    source = OUT / "registrations/original-plan.json"
    plan = read(source)
    assert str(plan["start"]).startswith("2026-11-10")
    config_entry, = plan["candidates"]
    assert config_entry["name"] == "original" and config_entry["max_open_trades"] == 12
    fit = read(MODEL)
    assert pd.Timestamp(fit["last_training_close"]) < pd.Timestamp("2026-01-01", tz="UTC")
    training = fit["control"]
    probabilities = {
        tag: 1 / (1 + exp(-training["intercept"] - coefficient))
        for tag, coefficient in zip(fit["tag_levels"], training["coef"], strict=True)
    }
    threshold = fit["thresholds"]["core_nfi_tag_payoff"]
    assert 1 / (1 + exp(-training["intercept"])) < threshold
    eligible = sorted(tag for tag, probability in probabilities.items() if probability >= threshold)
    assert eligible
    parameters = write_once(output / "tag_payoff_model.json", {
        "eligible_tags": eligible, "threshold": threshold, "training_model": receipt(MODEL),
        "probabilities": probabilities, "unknown_tag_boosted": False,
    })
    config = read(checked(config_entry["config"]))
    config["strategy"] = STRATEGY
    config["bot_name"] = "nfi80-tag-payoff-reserve"
    source_files = {**config_entry["source_files"], f"{STRATEGY}.py": receipt(HERE / f"{STRATEGY}.py"),
                    "tag_payoff_model.json": parameters}
    case = {**config_entry, "name": ROLE, "strategy": STRATEGY, "source_files": source_files,
            "source_and_risk_behavior": "NFI X8 with only frozen tag-dependent first-stake sizing",
            "config": write_once(output / "config.json", config)}
    registered = write_once(output / "plan.json", {
        **plan, "candidates": [case], "tag_payoff_protocol": receipt(HERE / "tag_payoff_protocol.json"),
        "baseline_plan": receipt(source), "reserve_control_reports": controls,
        "comparison_purpose": "Frozen tag payoff sizing on the 16 day isolated reserve",
        "orders_enabled": False, "production_eligible": False,
    })
    return registered, parameters, controls


def verify_sizing(output, eligible):
    trace = output / "tag-payoff-stake-decisions.jsonl"
    rows = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    decisions = defaultdict(list)
    for row in rows:
        allowed = row["tag"] in eligible
        assert row["eligible"] == allowed
        expected = (min(row["maximum"], max(row["minimum"] or 0, 1.25 * row["native"]))
                    if allowed else row["native"])
        assert isclose(row["requested"], expected, rel_tol=0, abs_tol=1e-10)
        decisions[(row["pair"], pd.Timestamp(row["clock"]), row["tag"])].append(row)
    archive, = (output / "native_results").glob("*.zip")
    trades = stats_from(archive)["trades"]
    applied = 0
    for trade in trades:
        first = next(order for order in trade["orders"] if order["ft_is_entry"])
        tag = ("-1:" if trade["is_short"] else "1:") + ",".join(sorted(trade["enter_tag"].split()))
        clock = pd.Timestamp(first["order_filled_timestamp"], unit="ms", tz="UTC")
        keyed = decisions[(trade["pair"], clock, tag)]
        assert len(keyed) == 1
        event, = keyed
        fill_margin = first["amount"] * first["safe_price"] / trade["leverage"]
        # Native quantity precision can reduce the filled margin from the request.
        if event["eligible"] and event["requested"] > event["native"] and fill_margin > event["native"]:
            applied += 1
    return {"checked_stake_callbacks": len(rows), "first_entries_with_increased_filled_margin": applied,
            "native_entries_with_checked_callback": len(trades),
            "callback_trace": receipt(trace) if trace.exists() else None}


def profile(summary):
    main = summary["periods"]["continuous"]
    marks = {
        "mark_points": main["marks"], "receipts": summary["sources"],
        "natural_closed_trades": summary["profits"]["natural_closes"],
    }
    assert summary["five_minute_nav_reconciliation"]["passed"] and marks["mark_points"] == 4608
    return {**main, **describe(load_account(marks).values()), "liquidations": summary["liquidations"]}


def main():
    registered, parameters, controls = make_plan()
    eligible = set(read(checked(parameters))["eligible_tags"])
    results = {}
    for cost in COSTS:
        folder = BLOCK_RESEARCH / f"reserve_{ROLE}_{cost}bp"
        output = execute(checked(registered), folder, cost, "blocks", check_sizing=False)
        sizing = verify_sizing(output, eligible)
        audited = folder / "account_reconciled.json"
        candidate = profile(read(audited))
        control_ref = controls[str(cost)]
        benchmark = read(checked(control_ref))
        assert benchmark["cost_bps_per_side"] == cost
        baselines = {role: profile(benchmark["accounts"][role]) for role in ("original", "fixed_boost_12")}
        checks = {
            "higher_return": all(candidate["net_return"] > row["net_return"] for row in baselines.values()),
            "no_higher_drawdown": all(candidate["drawdown"] <= row["drawdown"] for row in baselines.values()),
            "no_fewer_large_wins": all(candidate["wins_at_least_1pct_of_equity"] >= row["wins_at_least_1pct_of_equity"]
                                       for row in baselines.values()),
            "observed_boost": sizing["first_entries_with_increased_filled_margin"] > 0,
            "no_modeled_liquidations": candidate["liquidations"] == 0,
        }
        results[str(cost)] = {"candidate": candidate, "controls": baselines, "sizing": sizing,
                              "checks": checks, "account": receipt(audited), "controls_receipt": control_ref}
        save(HERE / "tag_payoff_reserve_results.json", {
            "completed_native_accounts": len(results), "reserve_window": read(checked(registered))["periods"],
            "by_cost": results, "plan": registered, "orders_enabled": False,
            "exploratory_pass": len(results) == len(COSTS) and all(
                all(case["checks"].values()) for case in results.values()),
        })
        print("verified reserve", cost, candidate["net_return"], candidate["drawdown"], checks, flush=True)


if __name__ == "__main__":
    main()
