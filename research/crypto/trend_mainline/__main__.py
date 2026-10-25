"""Register, prepare and execute the single crypto trend/momentum research lane."""

import argparse
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from freqtrade.research.data import read, save, sha
from research.crypto.trend_mainline.account import metrics, replay
from research.crypto.trend_mainline.data import load, prepare


def gate(m, targets):
    reasons = []
    for fail, reason in [
        (m["net_return"] <= 0, "nonpositive_net_return"),
        (
            m["closed_trades"] < targets["minimum_complete_positions"],
            "insufficient_complete_positions",
        ),
        (
            m["profit_factor"] is None or m["profit_factor"] < targets["minimum_profit_factor"],
            "profit_factor_below_target_or_undefined",
        ),
        (
            m["daily_sharpe"] is None or m["daily_sharpe"] < targets["minimum_daily_sharpe"],
            "sharpe_below_target_or_undefined",
        ),
        (m["max_drawdown"] > targets["maximum_drawdown"], "drawdown_exceeded"),
        (m["account_halted"], "account_risk_halt"),
    ]:
        if fail:
            reasons.append(reason)
    return {
        "passed": not reasons,
        "reasons": reasons,
        "risk_drawdown": m["max_drawdown"],
        "profit_factor": m["profit_factor"],
    }


def register(root, template):
    root.mkdir(parents=True, exist_ok=False)
    plan = read(template)
    plan["registered_utc"] = datetime.now(UTC).isoformat()
    plan["market_receipts_sha256"] = sha(Path(plan["market_dataset"]) / "receipts.json")
    plan["funding_receipts_sha256"] = sha(Path(plan["funding_dataset"]) / "receipts.json")
    sources = {}
    snapshot = root / "source_snapshot"
    snapshot.mkdir()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        shutil.copyfile(path, snapshot / path.name)
        sources[str(path.resolve())] = sha(path)
    plan["source_files"] = sources
    save(root / "plan.json", plan)
    save(
        root / "registration.json",
        {
            "plan_sha256": sha(root / "plan.json"),
            "template_sha256": sha(template),
            "orders_enabled": False,
        },
    )
    return plan


def verify(root):
    plan = read(root / "plan.json")
    if sha(root / "plan.json") != read(root / "registration.json")["plan_sha256"]:
        raise ValueError("Registered plan changed")
    for name, identity in plan["source_files"].items():
        if sha(Path(name)) != identity:
            raise ValueError("Registered research source changed; create a new study")
    if any(k.startswith("FREQTRADE__") for k in os.environ):
        raise ValueError("Configuration environment overrides are forbidden")
    return plan


def account(root, data, plan, candidate, phase, capital, cost, start, end):
    folder = root / phase / f"{candidate}_capital_{capital}" / f"cost_{cost}bps"
    folder.mkdir(parents=True, exist_ok=False)
    result = replay(data, plan, candidate, capital, cost, start, end)
    m = metrics(result, capital)
    result["curve"].to_parquet(folder / "equity_5m.parquet", index=False)
    for name in ("state", "events", "decisions", "trades", "checkpoints"):
        save(folder / f"{name}.json", result[name])
    monthly = (
        result["curve"]
        .set_index("date")
        .equity.resample("MS")
        .first()
        .pct_change(fill_method=None)
        .dropna()
    )
    daily = result["curve"].set_index("date").equity.resample("D").last()
    rolls = {}
    for days in (30, 90, 180):
        values = daily.pct_change(days, fill_method=None).dropna()
        rolls[str(days)] = {
            "minimum": float(values.min()) if len(values) else None,
            "positive_fraction": float(values.gt(0).mean()) if len(values) else None,
        }
    row = {
        "schema": "crypto-trend-account.v1",
        "candidate": f"{candidate}_capital_{capital}",
        "mechanism": candidate,
        "phase": phase,
        "start": start,
        "end": end,
        "initial_equity_usdt": capital,
        "extra_cost_bps_per_side": cost,
        **m,
        "economic_audit": gate(m, plan["screening_targets"]),
        "rolling_calendar_returns": rolls,
        "monthly_returns": {t.isoformat(): float(v) for t, v in monthly.items()},
        "drawdown_convention": (
            "five_minute_trade_close_with_exit_cost_reserve_not_intrabar_or_mark_worst_case"
        ),
        "engine": "explicit_capacity_research_replay_not_native_freqtrade_or_exchange_execution",
        "capacity_model": (
            "submitted_using_past_turnover_then_filled_against_next_interval_quote_volume"
        ),
        "historical_evidence_status": (
            "previously_observed_history_fixed_current_selected_six_name_pool"
        ),
        "independent_oos": False,
        "native_final_account_reconciled": None,
        "funding_included": True,
        "production_eligible": False,
        "orders_enabled": False,
        "plan_sha256": sha(root / "plan.json"),
        "source_hashes": {p.name: sha(p) for p in sorted(folder.iterdir()) if p.is_file()},
    }
    save(folder / "result.json", row)
    print(
        json.dumps(
            {
                k: row[k]
                for k in (
                    "candidate",
                    "phase",
                    "extra_cost_bps_per_side",
                    "net_return",
                    "max_drawdown",
                    "closed_trades",
                )
            }
        ),
        flush=True,
    )
    return {
        **row,
        "path": str(folder.relative_to(root)),
        "result_sha256": sha(folder / "result.json"),
    }


def execute(root):
    plan = verify(root)
    if (root / "attempt.json").exists():
        raise ValueError("Preserve previous attempts; use a new version")
    save(root / "attempt.json", {"started_utc": datetime.now(UTC).isoformat(), "pid": os.getpid()})
    data = load(root / "data", plan)
    rows, base = [], plan["selection_capital_usdt"]
    for candidate in plan["candidates"] + ["LongOnlyControl"]:
        for cost in plan["extra_cost_bps_per_side"]:
            rows.append(
                account(
                    root,
                    data,
                    plan,
                    candidate,
                    "train",
                    base,
                    cost,
                    plan["start"],
                    plan["validation_start"],
                )
            )
    eligible = [
        c
        for c in plan["candidates"]
        if all(r["economic_audit"]["passed"] for r in rows if r["mechanism"] == c)
    ]
    chosen = (
        max(
            eligible,
            key=lambda c: next(
                r["daily_sharpe"]
                for r in rows
                if r["mechanism"] == c and r["extra_cost_bps_per_side"] == 3
            ),
        )
        if eligible
        else "cash"
    )
    selection = {
        "selected_candidate": chosen,
        "eligible": eligible,
        "future_results_included": False,
        "rule": plan["selection_rule"],
        "training_result_hashes": {r["path"]: r["result_sha256"] for r in rows},
        "diagnostic_evaluation_of_all_registered_mechanisms": True,
        "production_eligible": False,
    }
    save(root / "selection.json", selection)
    for phase, start, end in (
        ("validation", plan["validation_start"], plan["holdout_start"]),
        ("holdout", plan["holdout_start"], plan["end"]),
        ("continuous", plan["validation_start"], plan["end"]),
    ):
        for candidate in plan["candidates"] + ["LongOnlyControl"]:
            capitals = plan["capacity_capitals_usdt"] if phase == "continuous" else [base]
            for capital in capitals:
                for cost in plan["extra_cost_bps_per_side"]:
                    rows.append(
                        account(root, data, plan, candidate, phase, capital, cost, start, end)
                    )
    selected = [r for r in rows if r["mechanism"] == chosen and r["initial_equity_usdt"] == base]
    historical_pass = bool(selected) and all(r["economic_audit"]["passed"] for r in selected)
    summary = {
        "schema": "crypto-trend-mainline.v1",
        "selected_candidate": chosen,
        "selection_sha256": sha(root / "selection.json"),
        "plan_sha256": sha(root / "plan.json"),
        "historical_economic_screen_passed": historical_pass,
        "valid_return_source_established": False,
        "production_eligible": False,
        "orders_enabled": False,
        "independent_oos": False,
        "capacity_status": (
            "MODELED_PARTIAL_FILLS_ONLY_REAL_ORDER_BOOK_AND_HISTORICAL_FILTERS_UNVERIFIED"
        ),
        "scope_limitations": plan["scope_limitations"],
        "accounts": rows,
        "expansion_allowed": False,
        "next_action": (
            "Independent audit, then address the largest recorded failure; "
            "no new strategy family before review"
        ),
    }
    save(root / "evaluation.json", summary)
    save(
        root / "completion.json",
        {
            "finished_utc": datetime.now(UTC).isoformat(),
            "accounts": len(rows),
            "evaluation_sha256": sha(root / "evaluation.json"),
        },
    )
    return {"selected": chosen, "accounts": len(rows), "historical_pass": historical_pass}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["register", "prepare", "run"])
    p.add_argument("--run", type=Path, required=True)
    p.add_argument(
        "--template", type=Path, default=Path("research/crypto/trend_mainline_v001.json")
    )
    a = p.parse_args()
    if a.action == "register":
        result = register(a.run.resolve(), a.template.resolve())
    elif a.action == "prepare":
        plan = verify(a.run.resolve())
        result = prepare(plan, a.run.resolve() / "data")
    else:
        result = execute(a.run.resolve())
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
