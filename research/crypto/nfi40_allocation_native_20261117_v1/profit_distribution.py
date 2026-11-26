"""Describe the contribution of actual completed campaigns and marked account days."""

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(path, digest):
    if sha(path) != digest:
        raise ValueError(f"Artifact changed: {path}")
    return Path(path)


def distribution(archive, curve, capital, cost, grid, strategy="NostalgiaForInfinityX8"):
    with ZipFile(archive) as source:
        if source.testzip() is not None:
            raise ValueError("Archive CRC mismatch")
        summary = [
            json.loads(source.read(name))
            for name in source.namelist()
            if name.endswith(".json") and not name.endswith("_config.json")
        ]
    stats = next(data["strategy"][strategy] for data in summary if "strategy" in data)
    frame = pd.read_parquet(curve)
    if not pd.DatetimeIndex(frame.date).as_unit("ns").equals(grid):
        raise ValueError("Incomplete account calendar")
    nav = pd.Series(frame.hourly_mark_equity.to_numpy(), index=grid)
    if nav.iloc[0] != capital or stats["starting_balance"] <= 0:
        raise ValueError("Wallet identity differs")
    trades = pd.DataFrame(stats["trades"])
    if trades.is_open.any() or not trades.leverage.eq(1).all():
        raise ValueError("Unexpected terminal ledger")
    if not trades.fee_open.eq(cost / 10000).all() or not trades.fee_close.eq(cost / 10000).all():
        raise ValueError("Unexpected cost")
    trades["close"] = pd.to_datetime(trades.close_timestamp, unit="ms", utc=True)
    trades["minutes"] = (trades.close_timestamp - trades.open_timestamp) / 60000
    if abs(trades.profit_abs.sum() - stats["profit_total_abs"]) > 1e-6:
        raise ValueError("Native campaign total does not reconcile")
    closed = trades.loc[trades.close.lt(grid[-1]) & trades.exit_reason.ne("force_exit")]
    positive = closed.loc[closed.profit_abs.gt(0)].sort_values("profit_abs", ascending=False)
    gains = positive.profit_abs
    gross = float(gains.sum())
    day_nav = nav.reindex(pd.date_range(grid[0], grid[-1], freq="24h"))
    day_delta = day_nav.diff().dropna()
    last_fragment = float(nav.iloc[-1] - day_nav.iloc[-1])
    if abs(day_delta.sum() + last_fragment - (nav.iloc[-1] - capital)) > 1e-8:
        raise ValueError("Marked account days do not reconcile")
    daily_gross = day_delta.clip(lower=0).sum()
    first_half = grid.get_loc(pd.Timestamp("2026-01-01T00:00:00Z"))
    return {
        "starting_usdt": capital,
        "cost_bps_per_side": cost,
        "marked_ending_usdt": float(nav.iloc[-1]),
        "marked_cumulative_return": float(nav.iloc[-1] / capital - 1),
        "maximum_hourly_drawdown": float((1 - nav / nav.cummax()).max()),
        "continuous_2025_return": float(nav.iloc[first_half] / capital - 1),
        "continuous_2026_through_august_return": float(nav.iloc[-1] / nav.iloc[first_half] - 1),
        "natural_campaigns_closed_before_endpoint": len(closed),
        "profitable_campaigns": len(positive),
        "losing_campaigns": int(closed.profit_abs.lt(0).sum()),
        "worst_natural_campaign_usdt": float(closed.profit_abs.min()),
        "gross_losing_campaign_pnl_usdt": float(closed.profit_abs.clip(upper=0).sum()),
        "gross_winner_pnl_usdt": gross,
        "winner_median_usdt": float(gains.median()),
        "winner_p90_usdt": float(gains.quantile(0.9)),
        "win_duration_median_minutes": float(positive.minutes.median()),
        "winners_above_fraction_of_starting_capital": {
            str(level): {"count": int(len(big)), "gross_winner_pnl_share": float(big.sum() / gross)}
            for level in (0.01, 0.02, 0.05)
            for big in [gains[gains.ge(capital * level)]]
        },
        "top_10_gross_winner_pnl_share": float(gains.head(10).sum() / gross),
        "top_5_campaigns": json.loads(
            positive.head(5)[["pair", "is_short", "close", "profit_abs", "minutes", "enter_tag"]]
            .to_json(orient="records", date_format="iso")
        ),
        "completed_24h_marked_days": len(day_delta),
        "largest_marked_day_ending_utc": day_delta.idxmax().isoformat(),
        "largest_marked_day_gain_usdt": float(day_delta.max()),
        "top_5_days_share_of_positive_24h_cash_changes": float(day_delta.nlargest(5).sum() / daily_gross),
        "last_partial_day_change_usdt": last_fragment,
        "open_campaigns_at_marked_endpoint": int(frame.open_trades.iloc[-1]),
        "native_terminal_trade_count_including_force": len(trades),
        "archive_sha256": sha(archive),
        "curve_sha256": sha(curve),
    }


def main():
    plan = read(HERE / "plan.json")
    audits = read(HERE / "audit_results.json")
    if not audits["passed"]:
        raise ValueError("Numerical audits required for actual500 inputs")
    grid = pd.date_range(plan["start"], plan["comparison_end"], freq="h").as_unit("ns")
    if len(grid) != 14592:
        raise ValueError("Calendar differs from original comparison")
    baselines = read(HERE.parent / "mainline_comparison_contract_20261117_v1/contract.json")
    results = {}
    for baseline in baselines["baselines"]:
        cost = baseline["cost_bps_per_side"]
        inputs = baseline["old_native_outputs_rechecked"]
        archive = next(row for row in inputs if row["path"].endswith(".zip"))
        curve = next(row for row in inputs if row["path"].endswith("hourly_equity.parquet"))
        results[f"original1000_{cost}bp"] = distribution(
            verify(archive["path"], archive["sha256"]), verify(curve["path"], curve["sha256"]), 1000, cost, grid
        )
        candidate = f"nfi40_x8_500_cost{cost}"
        audit = next(row for row in audits["accounts"] if row["candidate"] == candidate)
        output = Path(plan["output_root"]) / candidate
        archive, = (output / "native_results").glob("*.zip")
        results[f"actual500_{cost}bp"] = distribution(
            verify(archive, audit["archive_sha256"]), verify(output / "hourly_equity.parquet", audit["hourly_equity_sha256"]),
            500, cost, grid
        )
    report = {
        "marked_window_utc": [grid[0].isoformat(), grid[-1].isoformat()],
        "threshold_base": "Starting account capital, inclusive thresholds. Campaigns exclude forced liquidation at replay termination.",
        "profit_concentration_denominator": "Sum of all positive natural completed campaign PnL after native fees and funding",
        "daily_cash_changes_use_continuous_hourly_marked_equity": True,
        "descriptive_attribution_no_trade_removal_counterfactual": True,
        "conditional_old_execution_model": True,
        "accounts": results,
    }
    with (HERE / "profit_distribution.json").open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    for name, row in results.items():
        print(name, row["marked_cumulative_return"], row["maximum_hourly_drawdown"],
              row["winner_median_usdt"], row["winners_above_fraction_of_starting_capital"],
              row["top_5_campaigns"][:1], row["top_10_gross_winner_pnl_share"])


if __name__ == "__main__":
    main()
