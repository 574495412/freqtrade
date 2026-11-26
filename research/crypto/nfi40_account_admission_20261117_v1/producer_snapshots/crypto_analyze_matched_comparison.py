"""Audit completed frozen accounts and report Pareto comparisons without tuning."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dominates(left, right):
    """Across the same cost/calendar scenarios; no weighted score or tie rescue."""
    weak = all(a[0] >= b[0] - 1e-12 and a[1] <= b[1] + 1e-12
               for a, b in zip(left, right, strict=True))
    strict = any(a[0] > b[0] + 1e-12 or a[1] < b[1] - 1e-12
                 for a, b in zip(left, right, strict=True))
    return weak and strict


def analyze(root, output):
    plan = json.loads((root / "registered_plan.json").read_text())
    expected = len(plan["candidates"])*len(plan["total_cost_bps_per_side"])
    paths = sorted(root.glob("*/result.json"))
    if len(paths) != expected or not (root / "completion.json").exists():
        raise ValueError(f"Incomplete matrix: {len(paths)}/{expected}; no selection published")
    expected_keys = {(c["name"], cost) for c in plan["candidates"] for cost in plan["total_cost_bps_per_side"]}
    observed_keys = [(v["candidate"], v["cost_bps_per_side"])
                     for v in (json.loads(p.read_text()) for p in paths)]
    if len(set(observed_keys)) != expected or set(observed_keys) != expected_keys:
        raise ValueError("Duplicate or missing registered scenario")
    output.mkdir(parents=True, exist_ok=False)
    oracle_path = Path("/Users/chrise/GitHub/TradingAgents/research_tools/nfi_reproduction/audit_original.py")
    spec = importlib.util.spec_from_file_location("prior_independent_cash_audit", oracle_path)
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    rows, audits, identities, vectors = [], [], {}, {}
    all_source_checks = []
    for path in paths:
        result = json.loads(path.read_text())
        folder = path.parent
        if json.loads((folder / "registered_plan.json").read_text()) != plan:
            raise ValueError("Mixed registered account contracts")
        archive_path, = (folder / "native_results").glob("backtest-result-*.zip")
        with zipfile.ZipFile(archive_path) as archive:
            filename, = [n for n in archive.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
            stats = json.loads(archive.read(filename))["strategy"][result["strategy"]]
        if stats["pairlist"] != plan["pairs"] or stats["starting_balance"] != plan["initial_equity_usdt"]:
            raise ValueError("Native exported market or capital contract differs")
        if any(t["leverage"] != 1 or not math.isclose(t["fee_open"], result["cost_bps_per_side"]/10000)
               for t in stats["trades"]):
            raise ValueError("Native exported leverage/cost differs")
        cash = [oracle.cash_from_orders(trade) for trade in stats["trades"]]
        maximum_error = max((float(x["absolute_error"]) for x in cash), default=0)
        quantity_error = max((abs(float(x["quantity_residual"])) for x in cash), default=0)
        aggregate_error = abs(math.fsum(float(x["reconstructed_net_pnl"]) for x in cash) - stats["profit_total_abs"])
        if maximum_error > 1e-5 or aggregate_error > 1e-4 or quantity_error > 1e-8:
            raise ValueError("Pre-existing independent cash auditor rejected account")
        audits.append({"candidate": result["candidate"], "cost": result["cost_bps_per_side"],
                       "trades": len(cash), "orders": sum(len(t["orders"]) for t in stats["trades"]),
                       "max_cash_error_usdt": maximum_error, "aggregate_cash_error_usdt": aggregate_error,
                       "max_quantity_residual": quantity_error, "passed": True})
        curve_path = folder / "hourly_equity.parquet"
        frame = pd.read_parquet(curve_path).set_index("date")
        for registered, reported in zip(plan["periods"], result["periods"], strict=True):
            if registered["name"] != reported["name"]:
                raise ValueError("Reported calendar slice differs")
            start, end = pd.Timestamp(registered["start"]), pd.Timestamp(registered["end"])
            part = frame.loc[start:end, "hourly_mark_equity"]
            if not part.index.equals(pd.date_range(start, end, freq="h")) or not np.isfinite(part).all():
                raise ValueError("Independent hourly calendar/mark check failed")
            ret = float(part.iloc[-1]/part.iloc[0]-1)
            dd = float((1-part/part.cummax()).max())
            if abs(ret-reported["net_return"]) > 1e-10 or abs(dd-reported["hourly_mark_drawdown"]) > 1e-10:
                raise ValueError("Independent return/drawdown reconstruction failed")
            # Only full UTC-midnight-to-midnight observations enter daily Sharpe.
            # A final 23-hour fragment is included in account return, not mislabeled
            # as an additional full daily return. This does not affect ranking.
            daily = part.loc[part.index.hour == 0].pct_change().dropna()
            daily_std = daily.std(ddof=1)
            sharp = float(daily.mean()/daily_std*np.sqrt(365)) if daily_std > 0 else None
            month_boundaries = sorted({start, end, *pd.date_range(start, end, freq="MS")})
            monthly = part.loc[month_boundaries].pct_change().dropna()
            row = {**reported, "candidate": result["candidate"], "cost_bps_per_side": result["cost_bps_per_side"],
                   "daily_nav_sharpe": sharp, "daily_return_observations": len(daily),
                   "daily_return_convention": "complete UTC-midnight intervals only",
                   "worst_observed_calendar_month_return": float(monthly.min()) if len(monthly) else None,
                   "source": str(path), "hourly_curve_sha256": sha(curve_path)}
            rows.append(row)
        identities[str(path)] = sha(path)
        identities[str(archive_path)] = sha(archive_path)
        identities[str(curve_path)] = sha(curve_path)
        candidate = next(c for c in plan["candidates"] if c["name"] == result["candidate"])
        for name, record in candidate["source_files"].items():
            if sha(folder / "strategies" / name) != record["sha256"]:
                raise ValueError("Executed strategy copy differs")
        all_source_checks.append(folder.name)
    names = [c["name"] for c in plan["candidates"]]
    for name in names:
        selected = sorted((r for r in rows if r["candidate"] == name),
                          key=lambda r: (r["cost_bps_per_side"], r["name"]))
        vectors[name] = [(r["net_return"], r["hourly_mark_drawdown"]) for r in selected]
    dominance = {a: [b for b in names if a != b and dominates(vectors[a], vectors[b])] for a in names}
    frontier = [n for n in names if not any(n in values for values in dominance.values())]
    robust_positive = [n for n in names if all(v[0] > 0 for v in vectors[n])]
    historical_screens = {}
    for name in names:
        reasons = []
        for row in (r for r in rows if r["candidate"] == name):
            prefix = f"{row['cost_bps_per_side']}bps/{row['name']}"
            if row["net_return"] <= 0: reasons.append(prefix+": nonpositive return")
            if row["hourly_mark_drawdown"] > .15: reasons.append(prefix+": observed drawdown above historical 15% screen")
            if row["completed_campaigns"] < 30: reasons.append(prefix+": fewer than 30 completed campaigns")
            if row["campaign_gross_profit"] <= 0 or row["campaign_gross_profit"] < 1.1*row["campaign_gross_loss"]:
                reasons.append(prefix+": completed-campaign profit factor below 1.1")
        historical_screens[name] = {"passed": not reasons, "reasons": reasons,
                                    "not_user_live_risk_authorization": True}
    summary = {"schema": "cz.matched-native-comparison-result.v1",
               "completed_at": datetime.now(timezone.utc).isoformat(), "accounts": len(paths),
               "rows": rows, "dominates": dominance, "pareto_frontier": frontier,
               "unique_historical_pareto_candidate": frontier[0] if len(frontier)==1 else None,
               "positive_all_cost_and_calendar_scenarios": robust_positive,
               "historical_screens": historical_screens, "native_fill_cash_audits": audits,
               "native_source_copy_checks_passed": all_source_checks,
               "independent_auditor": {"path": str(oracle_path), "sha256": sha(oracle_path)},
               "registered_plan_sha256": sha(root / "registered_plan.json"),
               "input_hashes": identities, "orders_enabled": False,
               "production_eligible": False, "independent_oos": False,
               "global_optimum_claimed": False, "limitations": plan["known_limitations"]}
    (output / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    pd.DataFrame(rows).to_csv(output / "periods.csv", index=False)
    print(json.dumps({"accounts":len(paths),"frontier":frontier,"robust_positive":robust_positive,
                      "screens":{n:v['passed'] for n,v in historical_screens.items()}},indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.root.resolve(), args.output.resolve())
