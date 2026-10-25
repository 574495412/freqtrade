"""Recheck unchanged first-hit labels through native protected callbacks and the fixed strategy."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto import audit_first_hit_learning
from research.crypto.interaction_models import verify_sources
from research.crypto.replay_first_hit_wrapped import replay


def reaudit(study, output):
    if output.exists():
        raise ValueError("Preserve previous callback audit")
    plan_path = Path("research/crypto/first_hit_learning_plan_v001.json")
    plan = read(plan_path)
    verify_sources(plan)
    prepared = read(study / "panel/receipt.json")
    screen = read(study / "evaluation/result.json")
    h1_path = study / "panel/h1_units.parquet"
    held_path = study / "panel/validation_features.parquet"
    for path in [h1_path, held_path]:
        if sha(path) != prepared["file_hashes"][path.name]:
            raise ValueError("Original input changed")
    held = pd.read_parquet(held_path)
    original = audit_first_hit_learning.replay
    try:
        audit_first_hit_learning.replay = replay
        first = audit_first_hit_learning.audit_units(plan, pd.read_parquet(h1_path))
        costs = []
        for cost in plan["extra_cost_bps_per_side"]:
            path = study / "evaluation" / f"validation_units_cost_{cost}bps.parquet"
            if sha(path) != screen["cost_label_file_hashes"][path.name]:
                raise ValueError("Original cost labels changed")
            labels = pd.read_parquet(path)
            units = labels.merge(held[["row_id", "atr"]], on="row_id", validate="one_to_one")
            costs.append({"cost": cost, **audit_first_hit_learning.audit_units(plan, units, cost)})
    finally:
        audit_first_hit_learning.replay = original
    paths = [
        Path(__file__),
        Path("research/crypto/replay_first_hit_wrapped.py"),
        Path("research/crypto/strategies/first_hit_dual_v2.py"),
    ]
    result = {
        "reviewed_utc": datetime.now(UTC).isoformat(),
        "passed": True,
        "source_hashes": {str(p.absolute()): sha(p) for p in paths},
        "original_plan_sha256": sha(plan_path),
        "H1_wrapped_paths": first,
        "H2_wrapped_cost_paths": costs,
        "original_labels_changed": False,
        "models_or_forecasts_changed": False,
        "scope": "Original exit rules and labels rechecked through the deepcopy-protected "
        "order_filled callback and real-trade after-fill stop update. This is a "
        "new engineering audit, not a new independent profitability experiment.",
        "orders_enabled": False,
        "production_eligible": False,
    }
    save(output, result)
    print(
        {
            "passed": True,
            "H1_paths": first["units_checked"],
            "H2_paths": sum(c["units_checked"] for c in costs),
        },
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reaudit(args.study, args.output)
