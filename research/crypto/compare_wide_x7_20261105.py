"""Combine completed audited X8/X4/X7 accounts with matching contracts."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "user_data/crypto_quant"
RUNS = {
    "base8": ROOT / "wide_strategy_comparison_20261105_v001",
    "stress15": ROOT / "wide_strategy_cost15_20261105_v001",
    "x7": ROOT / "wide_strategy_x7_20261105_v001",
}
ANALYSES = {
    "base8": ROOT / "wide_strategy_comparison_20261105_analysis_v001/comparison.json",
    "stress15": ROOT / "wide_strategy_cost15_20261105_analysis_v001/comparison.json",
    "x7": ROOT / "wide_strategy_x7_20261105_analysis_v001/comparison.json",
}
OUT = ROOT / "wide_strategy_three_candidate_20261105_analysis_v001"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dominates(left, right):
    weak = all(a[0] >= b[0] - 1e-12 and a[1] <= b[1] + 1e-12
               for a, b in zip(left, right, strict=True))
    strict = any(a[0] > b[0] + 1e-12 or a[1] < b[1] - 1e-12
                 for a, b in zip(left, right, strict=True))
    return weak and strict


def main():
    plans = {name: json.loads((root / "registered_plan.json").read_text()) for name, root in RUNS.items()}
    fixed = ("engine_commit", "start", "end", "comparison_end", "pairs", "initial_equity_usdt",
             "allowed_leverage", "datadir", "data_receipts", "marks", "market_snapshot", "periods")
    for key in fixed:
        if any(plan[key] != plans["base8"][key] for plan in plans.values()):
            raise ValueError("Frozen account contract differs: " + key)
    rows, audit_records = [], []
    for name, root in RUNS.items():
        if not (root / "completion.json").is_file():
            raise ValueError("Incomplete run: " + str(root))
        report = json.loads(ANALYSES[name].read_text())
        if report["registered_plan_sha256"] != sha(root / "registered_plan.json"):
            raise ValueError("Analyzer used another plan")
        rows.extend(report["rows"])
        for result_path in root.glob("*/result.json"):
            audit_path = result_path.parent / "independent-account-audit.json"
            audit = json.loads(audit_path.read_text())
            if audit["result_sha256"] != sha(result_path):
                raise ValueError("Independent audit does not bind the account")
            audit_records.append({"path": str(audit_path), "sha256": sha(audit_path)})
    names = ["nfi-x8-1x", "nfi-x7-1x", "nfi-x4-compact-1x"]
    expected_keys = {(name, cost, period["name"]) for name in names for cost in (8, 15)
                     for period in plans["base8"]["periods"]}
    actual_keys = {(r["candidate"], r["cost_bps_per_side"], r["name"]) for r in rows}
    if len(rows) != len(expected_keys) or actual_keys != expected_keys or len(audit_records) != 6:
        raise ValueError("Incomplete or duplicated scenario set")
    vectors = {}
    for name in names:
        selected = sorted((r for r in rows if r["candidate"] == name),
                          key=lambda r: (r["cost_bps_per_side"], r["name"]))
        vectors[name] = [(r["net_return"], r["hourly_mark_drawdown"]) for r in selected]
    dominance = {a: [b for b in names if a != b and dominates(vectors[a], vectors[b])] for a in names}
    frontier = [name for name in names if not any(name in beaten for beaten in dominance.values())]
    OUT.mkdir(parents=True, exist_ok=False)
    result = {"schema": "cz.three-candidate-wide-comparison.v1", "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "rows": rows, "dominates": dominance, "pareto_frontier": frontier,
              "scope": "Three incumbent strategies, 40 pairs, 1x, costs 8/15bps and overlapping calendar slices; retrospective, not globally optimal or independent OOS.",
              "x7_selection_timing": plans["x7"]["registration_note"],
              "plans": {name: {"path": str(root / 'registered_plan.json'), "sha256": sha(root / 'registered_plan.json')}
                        for name, root in RUNS.items()}, "independent_account_audits": audit_records,
              "orders_enabled": False, "production_eligible": False}
    (OUT / "comparison.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"frontier": frontier, "dominates": dominance, "rows": len(rows)}))


if __name__ == "__main__":
    main()
