"""Official-source performance fields with separate native and marked-account scopes."""

import argparse
import math
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.data.metrics import (
    calculate_calmar_from_balance,
    calculate_sharpe_from_balance,
    calculate_sortino_from_balance,
)
from freqtrade.research.data import read, save, sha
from research.crypto.audit_ensemble import fill_cash, raw_trades
from research.crypto.run_candidate import native_stats


STUDIES = [
    "sweep_rotation_run_v001",
    "compression_retest_run_v001",
    "compression_half_risk_run_v001",
    "rsi2_dual_horizon_run_v001",
    "native_absorption_run_v001",
    "bounded_inventory_run_v001",
    "inventory_market_context_run_v001",
]


def annual_sharpe(returns):
    values = np.asarray(returns, dtype=float)
    if len(values) < 2 or not np.isfinite(values).all():
        return None
    deviation = float(values.std(ddof=1))
    return float(values.mean() / deviation * math.sqrt(365)) if deviation > 1e-15 else None


def underwater_days(curve, initial, start, end):
    peak_time, peak, underwater_start, longest = start, initial, None, 0.0
    for clock, value in curve.items():
        if value >= peak - 1e-10:
            if underwater_start is not None:
                longest = max(longest, (clock - underwater_start).total_seconds() / 86400)
            peak_time, peak, underwater_start = clock, max(peak, value), None
        elif underwater_start is None:
            underwater_start = peak_time
    if underwater_start is not None:
        longest = max(longest, (end - underwater_start).total_seconds() / 86400)
    return longest, underwater_start is not None


def anchor_initial_wallet(curve, start, initial, native_starting_balance, trades):
    """Add only the documented flat initial wallet, never fill a missing market observation."""
    if start not in curve.index:
        if abs(native_starting_balance - initial) > 1e-8:
            raise ValueError("Native initial wallet does not match the protocol")
        start_ms = start.value // 1_000_000
        if any(t["open_timestamp"] < start_ms for t in trades):
            raise ValueError("Cannot assume an initial flat wallet with pre-period positions")
        return pd.concat([pd.Series([float(initial)], index=pd.DatetimeIndex([start])), curve])
    return curve


def marked_metrics(curve, initial, start, end, max_base_balance=None):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("Positive UTC-aware evaluation interval required")
    curve = curve.loc[(curve.index >= start) & (curve.index <= end)]
    if curve.index.has_duplicates or not curve.index.is_monotonic_increasing:
        raise ValueError("Unique sorted marked equity required")
    expected = pd.date_range(start, end, freq="h")
    if not curve.index.equals(expected):
        raise ValueError("Hourly marked equity must cover every clock, including both endpoints")
    if not np.isfinite(curve).all() or (curve <= 0).any():
        raise ValueError("Positive finite equity required for risk-adjusted ratios")
    if abs(float(curve.iloc[0]) - initial) > 1e-5:
        raise ValueError("Initial equity anchor differs from account")
    boundary = pd.date_range(start, end, freq="D")
    if boundary[-1] != end:
        raise ValueError("This scorecard requires complete UTC calendar days")
    daily = curve.reindex(boundary)
    pnl = daily.diff().iloc[1:]
    nav_return = daily.pct_change(fill_method=None).iloc[1:]
    # No-deposit local proxy: daily incremental PnL / fixed MaxBaseBalance.
    # This is separate from daily compounded NAV return and is not a live platform value.
    max_base_balance = initial if max_base_balance is None else max_base_balance
    if max_base_balance <= 0:
        raise ValueError("Positive MaxBaseBalance required")
    fixed_base_roi = pnl / max_base_balance
    drawdown = 1 - curve / curve.cummax()
    days = (end - start).total_seconds() / 86400
    cagr = float((curve.iloc[-1] / initial) ** (365 / days) - 1)
    dd = float(drawdown.max())
    down = np.minimum(nav_return.to_numpy(), 0)
    downside = float(np.sqrt(np.mean(down**2)))
    wallet = daily.rename("equity").rename_axis("date").reset_index()
    native_balance = {
        "sharpe_daily_marked_input": calculate_sharpe_from_balance(wallet, balance_col="equity"),
        "sortino_daily_marked_input": calculate_sortino_from_balance(wallet, balance_col="equity"),
        "calmar_hourly_marked_input": calculate_calmar_from_balance(
            curve.rename("equity").rename_axis("date").reset_index(), balance_col="equity"
        ),
    }
    # Preserve the native zero-denominator sentinel separately from measured ratios.
    native_status = {
        k: "UNDEFINED_NATIVE_SENTINEL" if v == -100 else "COMPUTED"
        for k, v in native_balance.items()
    }
    if dd <= 1e-15:
        native_status["calmar_hourly_marked_input"] = "UNDEFINED_NO_DRAWDOWN_NATIVE_ZERO"
    longest, unrecovered = underwater_days(curve, initial, start, end)
    return {
        "calendar_days": int(days),
        "net_profit_usdt": float(curve.iloc[-1] - initial),
        "net_return": float(curve.iloc[-1] / initial - 1),
        "binance_no_transfer_roi_proxy": float((curve.iloc[-1] - initial) / max_base_balance),
        "max_base_balance_no_transfer_assumption": max_base_balance,
        "cagr_365": cagr,
        "binance_no_transfer_sharpe_proxy": annual_sharpe(fixed_base_roi),
        "binance_sharpe_actual_platform_value": None,
        "nav_daily_sharpe_sample_365": annual_sharpe(nav_return),
        "nav_daily_sortino_semideviation_365": (
            float(nav_return.mean() / downside * math.sqrt(365)) if downside > 1e-15 else None
        ),
        "cagr_over_hourly_marked_mdd": cagr / dd if dd > 1e-15 else None,
        "annualized_nav_volatility_sample_365": float(nav_return.std(ddof=1) * math.sqrt(365)),
        "hourly_marked_mdd": dd,
        "longest_underwater_days_including_unrecovered": longest,
        "underwater_at_end": unrecovered,
        "positive_days": int(pnl.gt(1e-10).sum()),
        "negative_days": int(pnl.lt(-1e-10).sum()),
        "flat_days": int(pnl.abs().le(1e-10).sum()),
        "profitable_calendar_day_fraction": float(pnl.gt(1e-10).mean()),
        "native_functions_on_marked_equity": native_balance,
        "native_function_value_status": native_status,
    }, pd.DataFrame(
        {
            "date": boundary[1:],
            "pnl_usdt": pnl.to_numpy(),
            "nav_return": nav_return.to_numpy(),
            "fixed_base_roi": fixed_base_roi.to_numpy(),
        }
    )


def card(folder):
    result, plan = read(folder / "result.json"), read(folder / "plan.json")
    name = result["candidate"]
    stats, trades = native_stats(folder), raw_trades(folder, name)
    curve = pd.read_parquet(folder / "hourly_equity.parquet").set_index("date").equity
    start, end = pd.Timestamp(result["start"], tz="UTC"), pd.Timestamp(result["end"], tz="UTC")
    added_initial_anchor = start not in curve.index
    curve = anchor_initial_wallet(
        curve, start, plan["initial_equity_usdt"], stats["starting_balance"], trades
    )
    marked, daily = marked_metrics(curve, plan["initial_equity_usdt"], start, end)
    if abs(marked["net_profit_usdt"] - result["net_profit_usdt"]) > 1e-5:
        raise ValueError("Terminal marked equity differs from native account")
    if abs(math.fsum(t["profit_abs"] for t in trades) - result["net_profit_usdt"]) > 1e-5:
        raise ValueError("Whole campaign profits differ from native account")
    cash_error = max((fill_cash(t) for t in trades), default=0.0)
    rolling = {}
    for days in [7, 30, 60, 90, 180]:
        if marked["calendar_days"] < days:
            rolling[str(days)] = None
            continue
        begin = end - pd.Timedelta(days=days)
        rolling[str(days)], _ = marked_metrics(
            curve,
            float(curve.loc[begin]),
            begin,
            end,
            max_base_balance=plan["initial_equity_usdt"],
        )
        closed = [
            t
            for t in trades
            if begin.value // 1_000_000 <= t["close_timestamp"] < end.value // 1_000_000
        ]
        positive = math.fsum(max(t["profit_abs"], 0) for t in closed)
        negative = -math.fsum(min(t["profit_abs"], 0) for t in closed)
        rolling[str(days)]["closed_in_window_campaigns"] = {
            "count": len(closed),
            "net_win_rate": sum(t["profit_abs"] > 0 for t in closed) / len(closed)
            if closed
            else None,
            "profit_factor": positive / negative if negative else None,
            "scope": "Entire campaigns closing in window; may have opened earlier. "
            "These campaign PnLs are not interval marked-account return.",
        }
    months = []
    bounds = pd.date_range(start, end, freq="MS")
    for begin, finish in pairwise(bounds):
        months.append(
            {
                "month": begin.strftime("%Y-%m"),
                "net_return": float(curve.loc[finish] / curve.loc[begin] - 1),
            }
        )
    wins = [t for t in trades if t["profit_abs"] > 0]
    losses = [t for t in trades if t["profit_abs"] < 0]
    gp, gl = math.fsum(t["profit_abs"] for t in wins), -math.fsum(t["profit_abs"] for t in losses)
    fees = math.fsum(
        o["amount"] * o["safe_price"] * (t["fee_open"] if o["ft_is_entry"] else t["fee_close"])
        for t in trades
        for o in t["orders"]
    )
    layers = {}
    for tag in sorted({t["enter_tag"] for t in trades}):
        group = [t for t in trades if t["enter_tag"] == tag]
        layers[tag] = {
            "whole_campaigns": len(group),
            "net_profit_usdt": math.fsum(t["profit_abs"] for t in group),
            "net_win_rate": sum(t["profit_abs"] > 0 for t in group) / len(group),
        }
    # Binance performance-route criteria require both a lead copy portfolio AND own futures data.
    # Only the clearly defined portfolio subset can be approximated from this simulation.
    subset = {
        "positive_pnl_30_60_90_days": all(
            rolling[str(n)]["net_profit_usdt"] > 0 for n in [30, 60, 90]
        ),
        "at_least14_profitable_days_in_last30": rolling["30"]["positive_days"] >= 14,
        "hourly_proxy_mdd_30_90_at_most20percent": all(
            rolling[str(n)]["hourly_marked_mdd"] <= 0.20 for n in [30, 90]
        ),
    }
    return {
        "candidate": name,
        "phase": result["phase"],
        "start": result["start"],
        "end": result["end"],
        "extra_cost_bps_per_side": result["extra_cost_bps_per_side"],
        "marked_account": marked,
        "initial_equity_anchor": (
            "Known flat initial native wallet added at evaluation start; "
            "original first capture is1h later"
            if added_initial_anchor
            else "Original start observation"
        ),
        "trailing_windows": rolling,
        "monthly_marked_returns": months,
        "whole_campaigns": {
            "count": len(trades),
            "net_win_rate": len(wins) / len(trades) if trades else None,
            "profit_factor": gp / gl if gl else None,
            "net_expectancy_usdt": result["net_profit_usdt"] / len(trades) if trades else None,
            "mean_win_over_mean_loss": (gp / len(wins)) / (gl / len(losses))
            if wins and losses
            else None,
            "complete_campaigns_per_calendar_day": len(trades) / marked["calendar_days"],
            "fees_plus_cost_proxy_usdt": fees,
            "funding_net_usdt": result["funding_net_usdt"],
            "mean_holding_minutes": math.fsum(t["trade_duration"] for t in trades) / len(trades)
            if trades
            else None,
            "layers": layers,
        },
        "native_closed_trade_fields_unchanged": {
            k: stats.get(k)
            for k in [
                "cagr",
                "sharpe",
                "sortino",
                "calmar",
                "sqn",
                "p_value",
                "expectancy",
                "expectancy_ratio",
                "profit_factor",
                "drawdown_duration_s",
                "max_drawdown_account",
                "max_consecutive_losses",
            ]
        },
        "binance_smart_filter_portfolio_subset_local_proxy": subset,
        "binance_full_smart_filter_eligibility": "NOT_ESTABLISHED_SIMULATED_ACCOUNT",
        "unavailable_platform_fields": {
            "own_leader_futures_30_60_90_day_results": None,
            "official_trading_day_denominator": None,
            "actual_aum": None,
            "actual_copy_trader_pnl": None,
            "ranking_percentile": None,
        },
        "audits": {
            "maximum_decimal_cash_error_usdt": cash_error,
            "terminal_equity_reconciled": True,
        },
        "input_identities": {
            "result_sha256": sha(folder / "result.json"),
            "marked_equity_sha256": sha(folder / "hourly_equity.parquet"),
            "native_zip_sha256": sha(next(folder.glob("backtest-result-*.zip"))),
        },
        "source_result": str((folder / "result.json").absolute()),
        "old_economic_audit_unchanged": result["economic_audit"],
        "historical_evidence_status": result["historical_evidence_status"],
        "production_eligible": False,
    }, daily


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve previous scorecards")
    contract = read(args.contract)
    args.output.mkdir(parents=True)
    save(args.output / "contract.json", contract)
    cards = []
    for study in STUDIES:
        for result in sorted((args.root / study).glob("*/*/cost_*bps/result.json")):
            record, daily = card(result.parent)
            key = (
                f"{study}__{record['phase']}__{record['candidate']}__"
                f"{record['extra_cost_bps_per_side']}bps"
            )
            daily.to_parquet(args.output / f"{key}.parquet", index=False)
            cards.append(record)
    save(
        args.output / "scorecards.json",
        {
            "generated_utc": datetime.now(UTC).isoformat(),
            "metric_source_sha256": sha(Path(__file__)),
            "native_metric_source_sha256": sha(Path("freqtrade/data/metrics.py")),
            "contract_sha256": sha(args.contract),
            "accounts": cards,
            "account_count": len(cards),
            "native_trade_results_changed": False,
            "orders_enabled": False,
            "production_eligible": False,
        },
    )
    print({"complete_accounts_scored": len(cards), "native_results_changed": False})
