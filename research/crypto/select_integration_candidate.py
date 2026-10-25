"""Review existing whole accounts for paper integration without rewriting prior verdicts."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.performance_scorecard import card, marked_metrics


PHASES = ("train", "validation", "holdout", "continuous")
COSTS = (0, 3, 10)


def inventory(root):
    groups = {}
    for path in sorted(root.rglob("cost_*/result.json")):
        result = read(path)
        name = result.get("candidate", result.get("variant"))
        key = (path.relative_to(root).parts[0], name)
        groups.setdefault(key, []).append((path, result))
    output = []
    for (run, name), records in sorted(groups.items()):
        rows, reasons = [], []
        pairs = set()
        for path, result in records:
            phase, cost = result["phase"], result["extra_cost_bps_per_side"]
            if (phase, cost) in pairs:
                raise ValueError(f"Duplicate account scenario: {path}")
            pairs.add((phase, cost))
            audit = result.get("economic_audit", {})
            passed = audit.get("passed") is True
            if phase in PHASES and cost in COSTS and not passed:
                reasons.append(f"{phase}/{cost}bps economic screen failed or unavailable")
            rows.append(
                {
                    "phase": phase,
                    "extra_cost_bps_per_side": cost,
                    "start": result["start"],
                    "end": result["end"],
                    "net_return": result["net_return"],
                    "closed_trades": result["closed_trades"],
                    "win_rate": result["win_rate"],
                    "hourly_observed_drawdown": result["hourly_observed_drawdown"],
                    "economic_screen_passed": passed,
                    "source": str(path.resolve()),
                    "sha256": sha(path),
                }
            )
        missing = sorted({(p, c) for p in PHASES for c in COSTS} - pairs)
        if missing:
            reasons.append("Missing scenarios: " + ", ".join(f"{p}/{c}" for p, c in missing))
        output.append(
            {
                "run": run,
                "candidate": name,
                "rows": rows,
                "whole_account_historical_screen_passed": not reasons,
                "reasons": reasons,
            }
        )
    return output


def review(root, output):
    output.mkdir(parents=True, exist_ok=False)
    entries = inventory(root)
    accepted = [x for x in entries if x["whole_account_historical_screen_passed"]]
    # Refuse to manufacture a winner if several candidates need a new comparison.
    if len(accepted) != 1:
        raise ValueError(f"Expected one existing complete economic candidate, got {len(accepted)}")
    chosen = accepted[0]
    run = root / chosen["run"]
    evaluation, audit, selection = [
        read(run / name) for name in ("evaluation.json", "portfolio_audit.json", "selection.json")
    ]
    if not evaluation["all_historical_economic_screens_passed"] or not audit["passed"]:
        raise ValueError("Portfolio evidence is incomplete")
    for row in audit["accounts"]:
        path = run / row["phase"] / chosen["candidate"] / f"cost_{row['cost']}bps/result.json"
        if sha(path) != row["portfolio_result_sha256"]:
            raise ValueError("Audited portfolio result changed")
    plan = read(run / "plan.json")
    # Verify all component archives, curves and frozen identities, not only summaries.
    for component in plan["components"]:
        for rel, digest in component["file_identities"].items():
            if sha(Path(component["run"]) / rel) != digest:
                raise ValueError(f"Component evidence changed: {rel}")
    save(output / "inventory.json", {"entries": entries})
    scores = []
    for phase in PHASES:
        folder = run / phase / chosen["candidate"] / "cost_3bps"
        result = read(folder / "result.json")
        curve_path = folder / "hourly_equity.parquet"
        curve = pd.read_parquet(curve_path).set_index("date").equity
        metrics, daily = marked_metrics(
            curve,
            plan["initial_equity_usdt"],
            pd.Timestamp(result["start"], tz="UTC"),
            pd.Timestamp(result["end"], tz="UTC"),
        )
        if abs(metrics["net_profit_usdt"] - result["net_profit_usdt"]) > 1e-5:
            raise ValueError("Portfolio marked equity does not reconcile")
        daily.to_parquet(output / f"{phase}_daily_returns.parquet", index=False)
        components = []
        for item in plan["components"]:
            component_card, _ = card(Path(item["run"]) / phase / item["name"] / "cost_3bps")
            components.append(component_card)
        scores.append(
            {
                "phase": phase,
                "whole_account": result,
                "marked_metrics": metrics,
                "curve_sha256": sha(curve_path),
                "component_scorecards": components,
            }
        )
    save(output / "scorecards.json", {"rows": scores})
    receipt = {
        "schema": "crypto-integration-candidate-review.v1",
        "reviewed_utc": datetime.now(UTC).isoformat(),
        "objective": "Prefer the best evidenced overall account; no small/large sleeve gate",
        "scope": "Existing Freqtrade native accounts only; no global optimum claim",
        "policy": {
            "required_phases": list(PHASES),
            "extra_cost_bps_per_side": list(COSTS),
            "minimum_closed_trades_per_period": 30,
            "minimum_profit_factor": 1.1,
            "maximum_observed_drawdown": 0.15,
            "positive_net_each_scenario": True,
            "threshold_origin": "Existing project economic screen, not official exchange criteria",
            "small_large_profit_frequency_and_80pct_win_rate_gates_removed": True,
            "independent_forward_and_tail_risk_requirements_retained": True,
            "retrospective_policy_change": True,
        },
        "candidate_groups": len(entries),
        "scenario_results": sum(len(x["rows"]) for x in entries),
        "eligible_for_paper_review_count": len(accepted),
        "selected_candidate": chosen["candidate"],
        "selected_run": str(run.resolve()),
        "selection_kind": "Best evidenced historical candidate for paper integration",
        "inventory_sha256": sha(output / "inventory.json"),
        "scorecards_sha256": sha(output / "scorecards.json"),
        "historical_agent_recommendation": selection["agent"],
        "historical_agent_endorsement_changed": False,
        "historical_verdicts_rewritten": False,
        "independent_oos": False,
        "production_eligible": False,
        "real_orders_enabled": False,
        "limitations": [
            "Combination formed after component histories were seen; selection is retrospective",
            "Continuous evaluation overlaps validation and holdout; never sum their trade counts",
            "NFI DCA and -0.99 fallback stop retain unresolved loss-tail risk",
            "Hourly marked drawdown does not bound intrahour risk",
            "Two independently funded wallets, 1000 simulated USDT each; no shared wallet",
            "No tested capital scaling, copy-trader ranking, or prospective profitability claim",
            "New learned-signal studies lack complete multiyear accounts and online signal support",
        ],
    }
    save(output / "selection.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.root.resolve(), args.output.resolve())
    print(
        {
            key: result[key]
            for key in (
                "selected_candidate",
                "candidate_groups",
                "scenario_results",
                "production_eligible",
            )
        }
    )
