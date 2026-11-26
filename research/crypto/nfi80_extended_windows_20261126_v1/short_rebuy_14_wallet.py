"""Register and run the uninterrupted native fourteen-slot cap comparison."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt
from research.crypto.nfi80_online_baseline_20261125_v1.audit import audit_short_rebuy_cap

from .accounts import COSTS, write_once
from .continuous_run import launch
from .inputs import HERE
from .recent_two_year_attribution import ledger
from .recent_two_year_run import GATE, RUNS


STRATEGY = "FlatBoostFourteenShortRebuyCap"
ROOT = RUNS / "short_rebuy_cap_14"


def register_accounts():
    evidence = receipt(HERE / "short_rebuy_transfer_audit.json")
    observed = read(checked(evidence))
    if not observed["admit_14_slot_calculation"]:
        raise ValueError("The transfer diagnostic did not admit a full account replay")
    case = read(checked(observed["freeze"]))
    scope = receipt(HERE / "short_rebuy_14_wallet_protocol.json")
    base = receipt(GATE)
    controls = read(checked(base))["plans"]
    registered = {}
    for cost in COSTS:
        control = controls[f"14_{cost}bp"]
        plan = read(checked(control))
        baseline, = plan["candidates"]
        if (baseline["strategy"], baseline["max_open_trades"]) != ("FlatBoostFourteenSlots", 14):
            raise ValueError("Wrong original fourteen-slot contract")
        runner = {"path": str(checked(control).with_name("runner.py")),
                  "sha256": plan["runner_sha256"]}
        checked(runner)
        folder = ROOT / f"{cost}bp/registration"
        folder.mkdir(parents=True, exist_ok=True)
        config = read(checked(baseline["config"]))
        config = write_once(folder / "config.json", {**config, "strategy": STRATEGY})
        sources = {**baseline["source_files"], "FlatBoostShortRebuyCap.py": case["frozen_cap"],
                   f"{STRATEGY}.py": receipt(HERE / f"{STRATEGY}.py")}
        for source in sources.values():
            checked(source)
        amended = {**baseline, "name": STRATEGY, "strategy": STRATEGY, "config": config,
                   "source_files": sources, "source_and_risk_behavior": read(checked(scope))}
        plan = write_once(folder / "plan.json", {
            **plan, "candidates": [amended], "matched_uncapped_plan": control,
            "short_rebuy_admission": evidence, "short_rebuy_wallet_protocol": scope,
            "orders_enabled": False, "production_eligible": False,
        })
        registered[cost] = (runner, plan)
    return registered


def strategy_preflight(plan):
    from freqtrade import __file__ as engine_file
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver

    registration = read(checked(plan))
    engine = Path(registration["engine_source"])
    if Path(engine_file).resolve() != engine / "freqtrade/__init__.py":
        raise ValueError("Wrong Freqtrade engine during strategy preflight")
    candidate, = registration["candidates"]
    with TemporaryDirectory(prefix="nfi-short-rebuy-14-") as name:
        folder = Path(name)
        for file, source in candidate["source_files"].items():
            shutil.copyfile(checked(source), folder / file)
        config = read(checked(candidate["config"]))
        config.update(strategy_path=name, user_data_dir=folder, runmode=RunMode.BACKTEST)
        strategy = StrategyResolver.load_strategy(config)
        modes = {cls.__name__: cls for cls in type(strategy).__mro__}
        parent = modes["FlatTopBoost"]
        sizing = modes["FlatBoostFourteenSlots"]
        risk = modes["FlatBoostShortRebuyCap"]
        if (strategy.custom_stake_amount.__func__ is not sizing.custom_stake_amount
                or strategy.adjust_trade_position.__func__ is not risk.adjust_trade_position):
            raise ValueError("Composition lost one of the registered native callbacks")
        with patch.object(parent, "bot_start") as started:
            strategy.bot_start()
            started.assert_called_once_with(strategy)
        short = SimpleNamespace(
            is_short=True, enter_tag="562", pair="EXAMPLE/USDT:USDT", entry_side="sell",
            select_filled_orders=lambda side: [SimpleNamespace(stake_amount_filled=20.0)],
        )
        clock = pd.Timestamp("2025-01-01T00:00:00Z")
        with patch.object(parent, "adjust_trade_position", return_value=(21.0, "rebuy")):
            rejected = strategy.adjust_trade_position(short, clock)
        with patch.object(parent, "adjust_trade_position", return_value=(20.0, "rebuy")):
            accepted = strategy.adjust_trade_position(short, clock)
        if rejected is not None or accepted != (20.0, "rebuy"):
            raise ValueError("Inherited cap callback failed its native-resolved smoke check")
        if len(strategy.blocked_adjustments.read_text().splitlines()) != 1:
            raise ValueError("Unexpected number of blocked adjustments in smoke check")
    return {"resolved_class": STRATEGY, "inherited_sizing": True, "inherited_cap": True,
            "cap_smoke_check": True, "plan": plan, "engine_commit": registration["engine_commit"],
            "orders_enabled": False}


def native_preflight(plan):
    engine = read(checked(plan))["engine_source"]
    bootstrap = (
        "import json,sys; sys.path.insert(0,sys.argv[1]);"
        "from research.crypto.nfi80_extended_windows_20261126_v1.short_rebuy_14_wallet "
        "import strategy_preflight;"
        "print(json.dumps(strategy_preflight(json.loads(sys.argv[2]))))"
    )
    command = [sys.executable, "-B", "-c", bootstrap, engine, json.dumps(plan)]
    return json.loads(subprocess.check_output(command, text=True))


def compare_account(cost, registration):
    _, plan_reference = registration
    cost_name = f"{cost}bp"
    original = receipt(HERE / f"recent_two_year_{cost}bp_14_verified.json")
    control = read(checked(original))["account"]
    plan = read(checked(plan_reference))
    if control["registered_plan"] != plan["matched_uncapped_plan"]:
        raise ValueError("The verified control differs from the registered comparator")
    target = ROOT / cost_name
    output = launch(registration, target, cost)
    proof = receipt(target / "account_reconciled.json")
    reconciled = read(checked(proof))
    wallet = ledger(reconciled)
    reference = ledger(read(checked(control["proof"])))
    config = read(output / "effective_config.json")
    baseline = read(checked(control["effective_config"]))
    if (config["max_open_trades"], baseline["max_open_trades"]) != (14, 14) or (
        invariant_config(config) != invariant_config(baseline)
    ):
        raise ValueError("Effective control and candidate wallet parameters differ")
    if not wallet["curve"].index.equals(reference["curve"].index):
        raise ValueError("Candidate and control do not have identical marks")
    risk = audit_short_rebuy_cap(output, stats_from(checked(wallet["refs"][".zip"]))["trades"])
    candidate = reconciled["periods"]["continuous"]
    incumbent = control["periods"]["continuous"]
    winners = "wins_at_least_1pct_of_equity"
    checks = {
        "higher_return": candidate["net_return"] > incumbent["net_return"],
        "no_higher_drawdown": candidate["drawdown"] <= incumbent["drawdown"],
        "no_fewer_large_winners": (
            reconciled["natural_trade_size"][winners] >= control["large_trades"][winners]
        ),
        "no_liquidations": reconciled["liquidations"] == 0,
        "cap_acted": risk["blocked_adjustment_requests"] > 0,
    }
    return {
        "proof": proof, "control": original, "periods": reconciled["periods"],
        "natural_trade_size": reconciled["natural_trade_size"], "verified_budget": risk,
        "checks": checks, "passed": all(checks.values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "run"))
    args = parser.parse_args()
    accounts = register_accounts()
    preflight = native_preflight(accounts[15][1])
    write_once(HERE / "short_rebuy_14_wallet_preflight.json", {
        "by_cost": {str(cost): {"runner": runner, "plan": plan}
                    for cost, (runner, plan) in accounts.items()},
        "strategy_preflight": preflight,
        "entrypoint": receipt(Path(__file__)),
        "orders_enabled": False,
    })
    if args.mode == "register":
        return
    cases = {}
    for cost in (15, 8):
        result = compare_account(cost, accounts[cost])
        record = write_once(HERE / f"short_rebuy_14_wallet_{cost}bp_verified.json", result)
        cases[str(cost)] = record
        print(cost, result["checks"], flush=True)
    write_once(HERE / "short_rebuy_14_wallet_comparison.json", {
        "cases": cases,
        "both_costs_passed": all(read(checked(row))["passed"] for row in cases.values()),
        "orders_enabled": False,
        "production_eligible": False,
    })


if __name__ == "__main__":
    main()
