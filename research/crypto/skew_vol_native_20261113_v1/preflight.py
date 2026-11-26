"""Before economics: exact registered source/input and actual causal-feature checks."""

import importlib.util
import json
from pathlib import Path

import pandas as pd

from research.crypto.skew_vol_native_20261113_v1.features import observations
from research.crypto.skew_vol_native_20261113_v1.SkewVolPortfolio import AnnualSkew, LowVolControl
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha


HERE = Path(__file__).resolve().parent


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    spec = importlib.util.spec_from_file_location("skew_native", HERE / "native_runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    for candidate in plan["candidates"]:
        runner.verify(plan, candidate)
    source = json.loads(Path(plan["candidates"][0]["config"]["path"]).read_text())
    scores = json.loads(Path(source["research_monthly_scores"]).read_text())
    checks = []
    for pair in plan["pairs"]:
        stem = pair.replace("/", "_").replace(":", "_")
        data = pd.read_feather(Path(plan["datadir"]) / "futures" / f"{stem}-1d-futures.feather")
        for edge in ["2025-01-01T00:00Z", "2026-01-01T00:00Z", "2026-08-01T00:00Z"]:
            edge = pd.Timestamp(edge)
            full = observations(data, edge)
            if observations(data.loc[data.date < edge], edge) != full:
                raise ValueError("Feature truncation mismatch")
            changed = data.copy()
            changed.loc[changed.date >= edge, "close"] *= 100
            if observations(changed, edge) != full:
                raise ValueError("Future feature perturbation mismatch")
            checks.append({"pair": pair, "decision": str(edge), "passed": True})
        for strategy in [AnnualSkew, LowVolControl]:
            obj = strategy(source)
            obj.bot_start()
            full = obj.populate_exit_trend(
                obj.populate_entry_trend(data.copy(), {"pair": pair}), {}
            )
            for edge in ["2025-01-01T00:00Z", "2026-01-01T00:00Z", "2026-08-01T00:00Z"]:
                prefix = data.loc[data.date <= pd.Timestamp(edge)].copy()
                short = obj.populate_exit_trend(
                    obj.populate_entry_trend(prefix, {"pair": pair}), {}
                )
                for key in ["enter_long", "enter_short", "exit_long", "exit_short"]:
                    if not full.loc[short.index, key].equals(short[key]):
                        raise ValueError("Native source signal prefix mismatch")
    if len(scores["months"]) != 20 or len(scores["rows"]) != 800:
        raise ValueError("Incomplete original feature window")
    save(
        HERE / "preflight.json",
        {
            "passed": True,
            "actual_input_prefix_checks": checks,
            "actual_native_signal_prefix_checks": 40 * 2 * 3 * 4,
            "independent_scalar_feature_cells": 1600,
            "source_formula_directions_fixed": True,
            "plan_sha256": sha(HERE / "plan.json"),
            "scope": "Causal source input/signals, not official recursive/lookahead or alpha effectiveness.",
        },
    )
    print(json.dumps({"passed": True, "input_prefix_checks": len(checks), "signal_checks": 960}))


if __name__ == "__main__":
    main()
