"""Actual native factor_analysis on frozen completed-close/next-open labels."""

import json
from pathlib import Path

import pandas as pd
from bootstrap import HERE, boot, save
from native import require_freeze


def labels(opens, schedule, horizon):
    dates = opens.index
    entries = [
        i for i, d in enumerate(dates) if "2025-01-01" <= d.strftime("%Y-%m-%d") <= "2026-08-31"
    ]
    retained, clocks, excluded = [], [], []
    for i in entries[::horizon]:
        if i == 0 or i + horizon >= len(dates):
            excluded.append(
                {"entry": dates[i].isoformat(), "reason": "Missing warmup or terminal exit open"}
            )
            continue
        observation, entry, exit_at = dates[i - 1], dates[i], dates[i + horizon]
        before, entered, exited = (
            schedule.loc[observation, "close"],
            schedule.loc[entry, "open"],
            schedule.loc[exit_at, "open"],
        )
        if not before < entered < exited:
            raise ValueError("Forward label chronology failed")
        retained.append((opens.iloc[i + horizon] / opens.iloc[i] - 1).rename(observation))
        clocks.append(
            {
                "observation": observation.isoformat(),
                "observed_close": before.isoformat(),
                "entry": entry.isoformat(),
                "entry_open": entered.isoformat(),
                "exit": exit_at.isoformat(),
                "exit_open": exited.isoformat(),
                "entry_year": entry.year,
            }
        )
    return pd.DataFrame(retained), clocks, excluded


def main():
    require_freeze()
    boot()
    from src.tools.factor_analysis_tool import FactorAnalysisTool

    protocol = json.loads((HERE / "protocol.json").read_text())
    ref = json.loads((HERE / "input_reference.json").read_text())
    inputs = json.loads(Path(ref["inputs"]).read_text())
    native = json.loads((HERE / "native_result.json").read_text())
    results, comparisons = [], []
    for cohort in native["cohorts"]:
        market = cohort["market"]
        folder = HERE / "native" / market
        schedule = pd.read_csv(inputs["schedules"][market]["path"], index_col=0)
        schedule.index = pd.to_datetime(schedule.index, utc=True)
        for col in ["open", "close"]:
            schedule[col] = pd.to_datetime(schedule[col], utc=True)
        opens = pd.read_parquet(folder / "open.parquet")
        scores = {
            identity: pd.read_parquet(folder / f"{identity}.parquet")
            for identity in cohort["factors"]
        }
        for horizon in [5, 1]:
            returns, clocks, excluded = labels(opens, schedule, horizon)
            base = HERE / "predictive" / market / str(horizon)
            base.mkdir(parents=True, exist_ok=False)
            save(base / "label_clocks.json", clocks)
            save(base / "excluded_terminal_anchors.json", excluded)
            for identity, score in scores.items():
                for split in ["all", "2025", "2026"]:
                    observed = [
                        pd.Timestamp(c["observation"])
                        for c in clocks
                        if split == "all" or c["entry_year"] == int(split)
                    ]
                    target = base / identity / split
                    target.mkdir(parents=True)
                    factor = score.loc[observed]
                    factor.to_csv(target / "factor.csv")
                    returns.loc[observed].to_csv(target / "returns.csv")
                    response = json.loads(
                        FactorAnalysisTool().execute(
                            factor_csv=str(target / "factor.csv"),
                            return_csv=str(target / "returns.csv"),
                            output_dir=str(target / "native"),
                            n_groups=3,
                        )
                    )
                    save(target / "native_response.json", response)
                    if response["status"] != "ok":
                        raise ValueError(response)
                    valid = (factor.notna() & returns.loc[observed].notna()).sum(axis=1)
                    results.append(
                        {
                            "market": market,
                            "factor": identity,
                            "horizon": horizon,
                            "split": split,
                            "diagnostic": target.relative_to(HERE).as_posix(),
                            "response": response,
                            "minimum_valid_assets": int(valid.min()),
                        }
                    )
            if horizon == 5:
                for i, left in enumerate(cohort["factors"]):
                    for right in cohort["factors"][i + 1 :]:
                        value = (
                            scores[left]
                            .loc[returns.index]
                            .corrwith(scores[right].loc[returns.index], axis=1)
                            .abs()
                            .mean()
                        )
                        comparisons.append(
                            {
                                "market": market,
                                "left": left,
                                "right": right,
                                "mean_abs_cross_sectional_pearson": float(value),
                                "duplicate": bool(value > 0.99),
                            }
                        )
    gates = []
    for cohort in native["cohorts"]:
        for factor in cohort["factors"]:
            records = {
                r["split"]: r
                for r in results
                if r["market"] == cohort["market"] and r["factor"] == factor and r["horizon"] == 5
            }
            r = records["all"]["response"]
            numeric = (
                r["ic_mean"] > 0.03
                and r["ir"] > 0.5
                and r["ic_positive_ratio"] > 0.55
                and r["ic_count"] >= protocol["gates"]["minimum_observations"]
                and records["2025"]["response"]["ic_mean"] > 0
                and records["2026"]["response"]["ic_mean"] > 0
            )
            gates.append(
                {
                    "market": cohort["market"],
                    "factor": factor,
                    "numeric_gate": bool(numeric),
                    "accepted_independent_alpha": False,
                    "input_gate": (
                        "Incomplete original pool / nominal unit and availability certification"
                    ),
                    "account_gate": "Ordinary EquityBacktesting financial entry closed",
                }
            )
    save(
        HERE / "predictive_result.json",
        {
            "results": results,
            "gates": gates,
            "comparisons": comparisons,
            "actual_factor_analysis_calls": len(results),
            "new_registry_definitions": 0,
            "newly_accepted_independent_alphas": 0,
            "complete_historical_accounts": 0,
            "portfolio_accounts": 0,
            "bench_records": 0,
            "net_account_returns": None,
            "secondary_cannot_rescue_primary": True,
            "goal_complete": False,
        },
    )
    print("FA DONE", len(results), "actual calls; gates", gates, flush=True)


if __name__ == "__main__":
    main()
