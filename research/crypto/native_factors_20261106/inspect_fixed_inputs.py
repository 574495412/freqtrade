"""Actual frozen data signal coverage; no positions, wallets or backtests."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE / "sources"))
from factor_inputs import cross_section, daily_features


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    planpath = ROOT / "research/crypto/official_factor_20261106/plan.json"
    plan = json.loads(planpath.read_text())
    runtime = ROOT / "user_data/crypto_quant/native_factors_20261106"
    runtime.mkdir(exist_ok=True)
    summary = []
    inputs = {}
    for market in ("spot", "futures"):
        for window in ("L", "S"):
            run = next(r for r in plan["runs"] if r["market"] == market and r["window"] == window)
            manifest = ROOT / run["data_receipt"]["path"]
            assert sha(manifest) == run["data_receipt"]["sha256"]
            hashes = json.loads(manifest.read_text())
            universe = tuple(plan["markets"][market]["pairs"])
            frames = {}
            for pair in universe:
                stem = pair.replace("/", "_").replace(":", "_")
                name = f"{stem}-1h.feather" if market == "spot" else f"futures/{stem}-1h-futures.feather"
                path = ROOT / run["datadir"] / name
                assert sha(path) == hashes[name]
                inputs[str(path.relative_to(ROOT))] = hashes[name]
                frames[pair] = pd.read_feather(path)
            days = pd.date_range(*plan["windows"][window], freq="D", inclusive="left", tz="UTC")
            values, selected, valid = cross_section(frames, universe, days)
            scales = pd.DataFrame({p: daily_features(frames[p], days).scale24 for p in universe})
            records = []
            for day in days:
                records.append({"decision_at_utc": day.isoformat(),
                                "earliest_modeled_execution_at_utc": (day + pd.Timedelta(minutes=5)).isoformat(),
                                "universe_complete": bool(valid.loc[day]),
                                "F1_selected": list(selected.columns[selected.loc[day]]),
                                "F1_momentum": {p: None if pd.isna(v) else float(v) for p, v in values.loc[day].items()},
                                "F2_scale": {p: None if pd.isna(v) else float(v) for p, v in scales.loc[day].items()}})
            destination = runtime / f"{market}_{window}_daily_intents.json"
            if destination.exists():
                assert json.loads(destination.read_text()) == records, "Existing intent values changed"
            else:
                with destination.open("x") as f:
                    json.dump(records, f, indent=2, allow_nan=False)
                    f.write("\n")
            flat = scales.stack().dropna()
            summary.append({"market": market, "window": window, "pairs": len(universe),
                            "decision_days": len(days), "F1_complete_universe_days": int(valid.sum()),
                            "F1_selected_count_histogram": {str(k): int(v) for k, v in selected.sum(axis=1).value_counts().sort_index().items()},
                            "F2_pair_days": int(scales.size), "F2_valid_pair_days": int(scales.notna().sum().sum()),
                            "F2_scale_quantiles": {str(k): float(v) for k, v in flat.quantile([0, .25, .5, .75, 1]).items()},
                            "intents_path": str(destination.relative_to(ROOT)), "intents_sha256": sha(destination)})
    result = {"observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "user_authorization": "允许自研因子", "source_sha256": sha(Path(__file__)),
              "feature_source_sha256": sha(HERE / "sources/factor_inputs.py"), "plan_sha256": sha(planpath),
              "timing_mode": "BAR_CLOSE_NEXT_5M_ASSUMPTION_NOT_OBSERVED_RECEIPT",
              "financial_runs": 0, "economic_qualified": False, "RD1_applied": False,
              "rows": summary, "input_sha256": inputs}
    with (HERE / "input_coverage.json").open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")
    for row in summary:
        print(json.dumps({k: v for k, v in row.items() if not k.startswith("intents_")}), flush=True)


if __name__ == "__main__":
    main()
