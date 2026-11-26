"""Use the isolated Vibe-Trading registry and its native factor analysis."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.donchian60_tail_native_20261125_v1.run import checked, read, save, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VIBE = ROOT.parent / "Vibe-Trading"
DAY_MS = pd.Timedelta(days=1)
WEEK = 7 * DAY_MS


def input_panels():
    crypto = HERE.parent
    daily = read(crypto / "donchian60_tail_native_20261125_v1/preflight.json")["daily_receipts"]
    plan = read(crypto / "head_shoulders_financial_native_20261117_v3/plan.json")
    clocks = pd.date_range("2025-01-06", "2026-08-31", freq="W-MON", tz="UTC").as_unit("ns")
    observations = clocks[:-1]
    assert len(observations) == 86 and len(plan["pairs"]) == 40
    fields = {key: {} for key in ("close", "high", "prior_week_close", "forward")}
    receipt = {}
    data = Path(plan["datadir"]) / "futures"
    for pair in plan["pairs"]:
        path = checked(daily[pair])
        frame = pd.read_feather(path).set_index("date")
        close = frame.close.reindex(observations - DAY_MS).to_numpy()
        prior = frame.close.reindex(observations - WEEK - DAY_MS).to_numpy()
        high = frame.high.rolling(7, min_periods=7).max().reindex(observations - DAY_MS).to_numpy()
        detail_path = data / (pair.replace("/", "_").replace(":", "_") + "-5m-futures.feather")
        detail = pd.read_feather(detail_path, columns=["date", "open"]).set_index("date")
        prices = detail.open.reindex(clocks + pd.Timedelta(minutes=5)).to_numpy()
        assert np.isfinite(prices).all() and (prices > 0).all()
        assert np.isfinite(high).all() and (high >= close).all() and (close > 0).all() and (prior > 0).all()
        for key, values in zip(fields, (close, high, prior, prices[1:] / prices[:-1] - 1), strict=True):
            fields[key][pair] = values
        receipt[pair] = {"daily_sha256": daily[pair]["sha256"], "5m_sha256": sha(detail_path)}
    return {key: pd.DataFrame(value, index=observations) for key, value in fields.items()}, receipt


def run_native(panel, labels, folder):
    from src.tools.factor_analysis_tool import FactorAnalysisTool

    folder.mkdir(parents=True, exist_ok=False)
    factor_csv, return_csv = folder / "factor.csv", folder / "returns.csv"
    panel.to_csv(factor_csv)
    labels.to_csv(return_csv)
    result = json.loads(FactorAnalysisTool().execute(
        factor_csv=str(factor_csv), return_csv=str(return_csv), output_dir=str(folder / "native"), n_groups=5,
    ))
    assert result["status"] == "ok" and result["ic_count"] == len(panel), result
    expected = [panel.loc[t].corr(labels.loc[t], method="spearman") for t in panel.index]
    actual = pd.read_csv(folder / "native/ic_series.csv", index_col=0).IC.to_numpy()
    assert np.abs(actual - expected).max() < 1e-12
    return result


def main():
    sys.path.insert(0, str(VIBE / "agent"))
    from src.factors.registry import Registry

    sources = [HERE / "protocol.json", Path(__file__), *sorted((HERE / "zoo/core").glob("*.py"))]
    owners = [VIBE / "agent/src" / path for path in (
        "factors/registry.py", "factors/factor_analysis_core.py", "tools/factor_analysis_tool.py",
    )]
    out = ROOT / "user_data/crypto_quant" / HERE.name
    out.mkdir(parents=True, exist_ok=False)
    save(HERE / "freeze.json", {
        "local_sources": {str(path.relative_to(HERE)): sha(path) for path in sources},
        "vibe_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=VIBE, text=True).strip(),
        "vibe_owners": {str(path.relative_to(VIBE)): sha(path) for path in owners},
        "economic_results_seen": False,
    })
    panel, data = input_panels()
    save(out / "data_receipt.json", data)
    labels = panel.pop("forward")
    registry = Registry(zoo_root=HERE / "zoo")
    assert len(registry.list()) == 2
    index = labels.index
    boundary = pd.Timestamp("2026-01-01", tz="UTC")
    periods = {"full": index, "2025": index[index + WEEK < boundary], "2026_to_august": index[index >= boundary]}
    results = {}
    for name, key in (("week_high", "core_crypto_week_high_distance"), ("momentum", "core_crypto_week_momentum_control")):
        factor = registry.compute(key, panel)
        assert factor.index.equals(index) and factor.columns.equals(labels.columns)
        results[name] = {split: run_native(factor.loc[date], labels.loc[date], out / name / split)
                         for split, date in periods.items()}
    candidate = results["week_high"]
    full = candidate["full"]
    gate = (full["ic_mean"] > 0.03 and full["ic_positive_ratio"] > 0.55
            and candidate["2025"]["ic_mean"] > 0 and candidate["2026_to_august"]["ic_mean"] > 0
            and full["ic_mean"] > results["momentum"]["full"]["ic_mean"])
    save(HERE / "result.json", {
        "registered_factors": 2, "actual_factor_analysis_calls": 6, "native_accounts": 0,
        "assets": len(labels.columns), "unique_complete_weeks": len(labels), "results": results,
        "passes_predictive_gate": bool(gate), "orders_enabled": False,
    })


if __name__ == "__main__":
    main()
