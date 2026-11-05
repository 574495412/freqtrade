"""Independently audit one completed registered wide-comparison account."""
import hashlib
import importlib.util
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "user_data/crypto_quant/wide_strategy_comparison_20261105_v001"
ORACLE = Path("/Users/chrise/GitHub/TradingAgents/research_tools/nfi_reproduction/audit_original.py")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(name, root=OUT, cost=8):
    folder = root / (name + f"-cost{cost}")
    result_path = folder / "result.json"
    result = json.loads(result_path.read_text())
    plan = json.loads((root / "registered_plan.json").read_text())
    if json.loads((folder / "registered_plan.json").read_text()) != plan:
        raise ValueError("Account plan differs from registration")
    archive_path, = (folder / "native_results").glob("backtest-result-*.zip")
    with zipfile.ZipFile(archive_path) as archive:
        filename, = [n for n in archive.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
        stats = json.loads(archive.read(filename))["strategy"][result["strategy"]]
    if stats["pairlist"] != plan["pairs"] or stats["starting_balance"] != plan["initial_equity_usdt"]:
        raise ValueError("Native account contract differs")
    if any(t["leverage"] != plan["allowed_leverage"] or not math.isclose(t["fee_open"], cost / 10000)
           or not math.isclose(t["fee_close"], cost / 10000) for t in stats["trades"]):
        raise ValueError("Actual leverage or fee differs")
    spec = importlib.util.spec_from_file_location("prior_independent_cash_audit", ORACLE)
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    cash = [oracle.cash_from_orders(trade) for trade in stats["trades"]]
    max_error = max((float(x["absolute_error"]) for x in cash), default=0)
    qty_error = max((abs(float(x["quantity_residual"])) for x in cash), default=0)
    agg_error = abs(math.fsum(float(x["reconstructed_net_pnl"]) for x in cash) - stats["profit_total_abs"])
    if max_error > 1e-5 or qty_error > 1e-8 or agg_error > 1e-4:
        raise ValueError("Independent cash or quantity reconciliation failed")
    curve = folder / "hourly_equity.parquet"
    frame = pd.read_parquet(curve).set_index("date")
    for registered, reported in zip(plan["periods"], result["periods"], strict=True):
        start, end = pd.Timestamp(registered["start"]), pd.Timestamp(registered["end"])
        part = frame.loc[start:end, "hourly_mark_equity"]
        if not part.index.equals(pd.date_range(start, end, freq="h")) or not np.isfinite(part).all():
            raise ValueError("NAV calendar differs")
        if abs(float(part.iloc[-1] / part.iloc[0] - 1) - reported["net_return"]) > 1e-10:
            raise ValueError("Return does not match saved NAV")
        if abs(float((1 - part / part.cummax()).max()) - reported["hourly_mark_drawdown"]) > 1e-10:
            raise ValueError("Drawdown does not match saved NAV")
    candidate = next(c for c in plan["candidates"] if c["name"] == name)
    for filename, source in candidate["source_files"].items():
        if sha(folder / "strategies" / filename) != source["sha256"]:
            raise ValueError("Strategy source mismatch")
    audit = {"candidate": name, "cost_bps_per_side": cost, "plan_sha256": sha(root / "registered_plan.json"),
             "archive_sha256": sha(archive_path), "result_sha256": sha(result_path),
             "curve_sha256": sha(curve), "independent_auditor_sha256": sha(ORACLE),
             "trades": len(cash), "filled_orders": sum(len(t["orders"]) for t in stats["trades"]),
             "maximum_trade_cash_error_usdt": max_error,
             "aggregate_cash_error_usdt": agg_error, "max_quantity_residual": qty_error,
             "native_fee_leverage_pair_capital_checked": True,
             "common_nav_calendar_return_drawdown_checked": True,
             "strategy_copy_checked": True,
             "orders_enabled": False, "production_eligible": False}
    output = folder / "independent-account-audit.json"
    with output.open("x") as stream:
        json.dump(audit, stream, indent=2)
        stream.write("\n")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--root", type=Path, default=OUT)
    parser.add_argument("--cost-bps", type=int, default=8)
    args = parser.parse_args()
    main(args.name, args.root.resolve(), args.cost_bps)
