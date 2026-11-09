"""Reconcile completed native100USDT artifacts; never manufacture account returns."""

import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.nfi_blacklist_rules_20261109.inputs import read, ref, save


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant/nfi_truth_audit_20261109_v001/accounts"


def summarize(case):
    key = case.get("output_key", f"{case['name']}_{case['cost']}bps")
    folder = OUT / key
    if (
        read(folder / "completion.json")["returncode"]
        or not read(folder / "fill_cash_audit.json")["passed"]
    ):
        raise ValueError("Uncompleted/unreconciled native account")
    result = read(folder / "result.json")
    plan = read(case["plan"]["path"])
    curve = pd.read_parquet(folder / "five_minute_equity.parquet")
    expected = pd.date_range(plan["start"], plan["comparison_end"], freq="5min")
    if not pd.DatetimeIndex(curve.date).equals(expected):
        raise ValueError("Incomplete native calendar")
    values = curve.marked_equity.to_numpy(float)
    if not np.isfinite(values).all():
        raise ValueError("Incomplete mark coverage")
    ret = values[-1] / values[0] - 1
    dd = (1 - values / np.maximum.accumulate(values)).max()
    metrics = result["periods"][0]
    drawdowns = 1 - values / np.maximum.accumulate(values)
    trough_index = int(np.argmax(drawdowns))
    peak_index = int(np.argmax(values[: trough_index + 1]))
    recovered = np.flatnonzero(
        (np.arange(len(values)) > trough_index) & (values >= values[peak_index])
    )
    if (
        abs(ret - metrics["net_return"]) > 1e-12
        or abs(dd - metrics["five_minute_mark_drawdown"]) > 1e-12
    ):
        raise ValueError("Native curve/result mismatch")
    if (
        curve.open_margin_usdt.max() > 99 + 1e-6
        or curve.open_trades.max() > plan["candidates"][0]["max_open_trades"]
    ):
        raise ValueError("Native capital/slot ceiling violated")
    (archive,) = (folder / "native_results").glob("*.zip")
    with ZipFile(archive) as z:
        names = [
            n
            for n in z.namelist()
            if n.endswith(".json")
            and "_config" not in n
            and "_meta" not in n
            and "_market_change" not in n
        ]
        objects = [json.loads(z.read(n)) for n in names]
        stats = next(o["strategy"][result["strategy"]] for o in objects if "strategy" in o)
    trades = stats["trades"]
    natural = [
        t
        for t in trades
        if t["exit_reason"] != "force_exit"
        and t["close_timestamp"] < pd.Timestamp(plan["comparison_end"]).timestamp() * 1000
    ]
    liquidation = [t for t in trades if t["exit_reason"] == "liquidation"]
    if any(t["leverage"] != plan["allowed_leverage"] for t in trades):
        raise ValueError("Unregistered leverage in native fills")
    checks = read(folder / "initial_entry_checks.json")
    if any(t["accepted"] and not t["inside_current_pool"] for t in checks):
        raise ValueError("Native admitted initial entry outside pool")
    frame = pd.DataFrame(trades)
    frame.drop(columns=["orders"], errors="ignore").to_csv(folder / "campaigns.csv", index=False)
    fills = []
    for n, trade in enumerate(trades):
        for order in trade["orders"]:
            fills.append({"campaign": n, "pair": trade["pair"], **order})
    pd.DataFrame(fills).to_csv(folder / "fills.csv", index=False)
    curve.to_csv(folder / "equity.csv", index=False)
    decision_file = folder / "stake-decisions.jsonl"
    decisions = (
        [json.loads(line) for line in decision_file.read_text().splitlines()]
        if decision_file.exists()
        else []
    )
    natural_wins = sum(t["profit_abs"] > 0 for t in natural)
    campaign_entries = [sum(o["ft_is_entry"] for o in t["orders"]) for t in trades]
    return {
        "name": case["name"],
        "output_key": key,
        "cost_bps_each_side": case["cost"],
        "initial_USDT": float(values[0]),
        "ending_marked_USDT": float(values[-1]),
        "net_return_pct": float(ret * 100),
        "maximum_5m_mark_drawdown_pct": float(dd * 100),
        "natural_completed_campaigns": len(natural),
        "daily_nav_sharpe": metrics["daily_nav_sharpe"],
        "native_funding_net_USDT": result["native_funding_net_usdt"],
        "natural_wins": natural_wins,
        "natural_win_rate_pct": natural_wins / len(natural) * 100 if natural else None,
        "all_terminal_campaigns": len(trades),
        "all_terminal_win_rate_pct": sum(t["profit_abs"] > 0 for t in trades) / len(trades) * 100
        if trades
        else None,
        "open_campaigns_at_cutoff": int(curve.open_trades.iloc[-1]),
        "open_mark_pnl_USDT": float(values[-1] - values[0] - curve.closed_realized_pnl.iloc[-1]),
        "peak_open_margin_cost_USDT": float(curve.open_margin_usdt.max()),
        "peak_market_gross_USDT": float(curve.gross.max()),
        "time_with_open_position_pct": float(curve.open_trades.gt(0).mean() * 100),
        "time_at90U_margin_pct": float(curve.open_margin_usdt.ge(90).mean() * 100),
        "drawdown_peak_time": str(curve.date.iloc[peak_index]),
        "drawdown_trough_time": str(curve.date.iloc[trough_index]),
        "drawdown_recovery_time": str(curve.date.iloc[recovered[0]]) if len(recovered) else None,
        "maximum_open_campaigns": int(curve.open_trades.max()),
        "addition_fills": sum(max(n - 1, 0) for n in campaign_entries),
        "allocation_decisions": len(decisions),
        "minimum_stake_bound_decisions": sum(
            r["native_stake"] * r["multiplier"] < (r["min_stake"] or 0) for r in decisions
        ),
        "max_entries_per_campaign": max(campaign_entries, default=0),
        "modeled_liquidations": len(liquidation),
        "research_risk_gate_passed": bool(
            ret > 0 and dd <= 0.30 and not liquidation and values.min() > 0
        ),
        "production_eligible": False,
        "independent_oos": False,
        "full_PIT_universe": False,
        "calendar_points": len(curve),
        "native_result": ref(folder / "result.json"),
        "native_archive": ref(archive),
        "curve": ref(folder / "five_minute_equity.parquet"),
        "cash_audit": ref(folder / "fill_cash_audit.json"),
        "completion": ref(folder / "completion.json"),
    }


def main():
    cases = [
        *read(HERE / "economic-registration.json")["plans"],
        *read(HERE / "spot-registration-v002.json")["plans"],
    ]
    rows = [summarize(case) for case in cases]
    save(
        HERE / "cap100-comparison.json",
        {
            "accounts": rows,
            "comparison": "Same calendar, separate spot/futures contracts; no global winner claim",
            "stress_higher_costs_tested": False,
            "orders_enabled": False,
        },
    )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
