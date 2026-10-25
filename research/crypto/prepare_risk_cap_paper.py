"""Package the frozen risk/execution intervention for engineering forward validation only."""

import argparse
import shutil
from pathlib import Path

from freqtrade.research.data import read, save, sha
from freqtrade.research.engine import configuration
from freqtrade.research.workflow import source_identity
from research.crypto.prepare_candidate_paper import LAUNCHER, runtime_identity
from research.crypto.prepare_ensemble_paper import MASTER, STOPPER


def checked_reference(run, name):
    plan = read(run / "plan.json")
    ref = run / "train" / name / "cost_3bps"
    frozen = read(ref / "freeze.json")
    if frozen["source_identity"] != source_identity() or frozen["plan_sha256"] != sha(
        run / "plan.json"
    ):
        raise ValueError("Native source or registered experiment changed")
    adapted = dict(
        plan,
        candidates=[name],
        stake_usdt=plan["stake_amount"],
        trading_mode="futures",
        margin_mode="isolated",
        exchange="binance",
    )
    settings = configuration(
        adapted, Path(plan["dataset"]), ref, plan["train_start"], plan["train_end"], 3
    )
    settings.pop("strategy_list")
    settings.update(strategy_path=str(ref / "strategies"), **plan.get("settings", {}))
    if settings != read(ref / "settings.json"):
        raise ValueError("Native registered settings changed")
    for source in plan["strategy_sources"]:
        if sha(ref / "strategies" / Path(source["path"]).name) != source["sha256"]:
            raise ValueError("Native strategy snapshot changed")
    order = settings["order_types"]
    if not (
        order["stoploss"] == "market"
        and order["stoploss_on_exchange"] is True
        and order["stoploss_price_type"] == "mark"
        and order["exit"] == "market"
    ):
        raise ValueError("Both components require the reviewed protective market-stop profile")
    return ref, settings


def prepare(run, trend_run, output):
    checks = [("train", 3), ("continuous", 3), ("continuous", 10)]
    reports = [
        run / "portfolios" / phase / "NFIRiskCapOneX" / f"cost_{cost}bps/result.json"
        for phase, cost in checks
    ]
    for path in reports:
        r = read(path)
        if not (
            r["historical_economic_screen_passed"]
            and r["nfi_fill_risk_audit"]["passed"]
            and r["nfi_fill_risk_audit"]["aggregate_fill_price_bounds_confirmed"]
        ):
            raise ValueError("Registered portfolio economic or filled-risk checks did not pass")
        for source in r["source_files"]:
            if sha(Path(source["path"])) != source["sha256"]:
                raise ValueError("Reviewed historical evidence changed")
    parity = [
        run / "train_execution_parity.json",
        run / "continuous_execution_parity.json",
        trend_run / "train_execution_parity.json",
        trend_run / "continuous_3_execution_parity.json",
        trend_run / "continuous_10_execution_parity.json",
    ]
    if any(not read(path)["passed"] for path in parity):
        raise ValueError("Execution overlay differs from the frozen economic account")
    references = [
        checked_reference(run, "NFIRiskCapOneX"),
        checked_reference(trend_run, "Donchian20HalfRisk"),
    ]
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "mode": "paper_fixed_rule_control",
        "candidate": "NFIRiskCapOneX_Donchian",
        "purpose": "Bounded-risk engineering and new prospective account evidence",
        "initial_equity_usdt": 2000,
        "maximum_open_positions": 12,
        "registered_coarse_historical_screens_passed": True,
        "uncomputed_historical_scenarios": ["cost0 and fresh-start2025/2026 native accounts"],
        "prior_agent_recommendation": "cash",
        "agent_endorsed": False,
        "independent_oos": False,
        "production_eligible": False,
        "real_orders_enabled": False,
        "production_activation_authorized": False,
        "live_review_limitations": [
            "Risk restriction reduces returns and Sharpe; superiority is not established",
            "2025 marked calendar subperiod is negative in the continuous account",
            "No independent forward profitability for this risk version",
            "No live venue protective-order acknowledgment, amendment or outage evidence",
            "Per-decision funding, partial fills and intrahour risk need further validation",
        ],
        "historical_evidence_sha256": {str(p): sha(p) for p in reports + parity},
        "components": [],
        "capital_rebalanced": False,
    }
    for name, (ref, settings) in zip(["nfi", "trend"], references, strict=True):
        folder = output / name
        folder.mkdir()
        shutil.copytree(
            ref / "strategies",
            folder / "strategies",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copyfile(ref / "requirements.lock.txt", folder / "requirements.lock.txt")
        for key in [
            "strategy_list",
            "timerange",
            "backtest_cache",
            "export",
            "exportdirectory",
            "datadir",
        ]:
            settings.pop(key, None)
        settings.update(
            dry_run=True,
            user_data_dir=str(folder),
            strategy_path=str(folder / "strategies"),
            db_url="sqlite:///" + str(folder / "paper.sqlite"),
            initial_state="running",
            bot_name="bounded_risk_" + name,
            force_entry_enable=False,
            cancel_open_orders_on_exit=False,
        )
        for key in ["key", "secret", "password", "uid"]:
            settings["exchange"][key] = ""
        save(folder / "settings.json", settings)
        (folder / "run_paper.py").write_text(LAUNCHER)
        child = {
            "candidate": settings["strategy"],
            "dry_run": True,
            "real_orders_enabled": False,
            "production_eligible": False,
            "settings_sha256": sha(folder / "settings.json"),
            "native_source_identity": source_identity(),
            "runtime": runtime_identity(ref),
            "strategy_files": {p.name: sha(p) for p in (folder / "strategies").glob("*.py")},
            "launcher_sha256": sha(folder / "run_paper.py"),
        }
        save(folder / "package.json", child)
        manifest["components"].append(
            {
                "directory": name,
                "candidate": settings["strategy"],
                "settings_sha256": sha(folder / "settings.json"),
                "package_sha256": sha(folder / "package.json"),
                "launcher_sha256": sha(folder / "run_paper.py"),
            }
        )
    (output / "run_portfolio_paper.py").write_text(MASTER)
    (output / "stop_portfolio_paper.py").write_text(STOPPER)
    manifest["master_sha256"] = sha(output / "run_portfolio_paper.py")
    python = str(Path(__file__).resolve().parents[2] / ".venv/bin/python")
    manifest["command"] = [python, str(output / "run_portfolio_paper.py")]
    manifest["stop_command"] = [python, str(output / "stop_portfolio_paper.py")]
    save(output / "package.json", manifest)
    save(
        output / "forward_protocol.json",
        {
            "scope": "New risk/execution cold-start account; old control is not merged",
            "source_sha256": sha(Path(__file__)),
            "old_agent_cash_preserved": True,
            "engineer_now": [
                "Persistent stop and entry counters",
                "No add after partial exit",
                "Native market-stop payload and rejection",
                "Fresh marked NAV and funding",
            ],
            "natural_forward_evidence_required": [
                "Completed campaigns and all filled-order costs",
                "Nonempty restart parity",
                "Partial fills and stop-order lifecycle",
                "Venue protective-order acks, trigger type and outage/reconnect coverage",
            ],
            "review_after_new_completed_campaigns": 5,
            "profitability_review_minimum_closed_campaigns": 30,
            "a_fixed_wait_does_not_establish_qualification": True,
            "calendar_loss_and_weak_sharpe_remain_open": True,
            "production_eligible": False,
            "real_orders_enabled": False,
        },
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--trend-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.run.resolve(), args.trend_run.resolve(), args.output.resolve())
    print({"candidate": result["candidate"], "real_orders_enabled": False})
