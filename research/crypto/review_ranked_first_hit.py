"""Independently reconstruct daily risk metrics and review the first-hit capacity diagnostic."""

import argparse
import math
import statistics
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity
from research.crypto.audit_ensemble import raw_trades
from research.crypto.run_candidate import native_stats


def review(folder):  # noqa: C901 - independent sequential audit invariants
    summary = read(folder / "summary.json")
    errors, reviewed = {}, []

    def equal(field, observed, expected):
        if observed is None or expected is None:
            if observed is not expected:
                raise ValueError((field, observed, expected))
            return
        errors[field] = max(errors.get(field, 0), abs(observed - expected))
        if not math.isclose(observed, expected, rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError((field, observed, expected))

    def sharpe(values):
        deviation = statistics.stdev(values)
        return statistics.mean(values) / deviation * math.sqrt(365) if deviation > 1e-15 else None

    for item in summary["accounts"]:
        result, score, audit = item["result"], item["scorecard"], item["campaign_audit"]
        account = Path(score["source_result"]).parent
        plan, stats, trades = (
            read(account / "plan.json"),
            native_stats(account),
            raw_trades(account, result["candidate"]),
        )
        if read(account / "freeze.json")["source_identity"] != source_identity():
            raise ValueError("Native source changed")
        for key, path in [
            ("result_sha256", account / "result.json"),
            ("marked_equity_sha256", account / "hourly_equity.parquet"),
            ("native_zip_sha256", next(account.glob("backtest-result-*.zip"))),
        ]:
            if score["input_identities"][key] != sha(path):
                raise ValueError("Stored input changed")
        for key, value in score["native_closed_trade_fields_unchanged"].items():
            if value != stats.get(key):
                raise ValueError("Native metric changed")
        initial = plan["initial_equity_usdt"]
        start, end = pd.Timestamp(result["start"], tz="UTC"), pd.Timestamp(result["end"], tz="UTC")
        curve = pd.read_parquet(account / "hourly_equity.parquet").set_index("date").equity
        if start not in curve.index:
            if stats["starting_balance"] != initial or any(
                t["open_timestamp"] < start.value // 1_000_000 for t in trades
            ):
                raise ValueError("Unknown initial cash anchor")
            curve = pd.concat([pd.Series([initial], index=pd.DatetimeIndex([start])), curve])
        if not curve.index.equals(pd.date_range(start, end, freq="h")):
            raise ValueError("Missing actual equity clock")
        daily = curve.loc[pd.date_range(start, end, freq="D")].tolist()
        pnl = [b - a for a, b in pairwise(daily)]
        returns = [b / a - 1 for a, b in pairwise(daily)]
        marked, whole = score["marked_account"], score["whole_campaigns"]
        peak, mdd = initial, 0.0
        for value in curve:
            peak = max(peak, value)
            mdd = max(mdd, (peak - value) / peak)
        equal("marked_net_pnl", marked["net_profit_usdt"], math.fsum(pnl))
        equal(
            "campaign_net_pnl",
            result["net_profit_usdt"],
            math.fsum(t["profit_abs"] for t in trades),
        )
        equal("binance_sharpe_proxy", marked["binance_no_transfer_sharpe_proxy"], sharpe(pnl))
        equal("nav_sharpe", marked["nav_daily_sharpe_sample_365"], sharpe(returns))
        equal("hourly_mdd", marked["hourly_marked_mdd"], mdd)
        downside = math.sqrt(math.fsum(min(r, 0) ** 2 for r in returns) / len(returns))
        equal(
            "nav_sortino_rms",
            marked["nav_daily_sortino_semideviation_365"],
            statistics.mean(returns) / downside * math.sqrt(365) if downside > 1e-15 else None,
        )
        wins = [t["profit_abs"] for t in trades if t["profit_abs"] > 0]
        losses = [t["profit_abs"] for t in trades if t["profit_abs"] < 0]
        equal(
            "complete_position_winrate",
            whole["net_win_rate"],
            len(wins) / len(trades) if trades else None,
        )
        equal(
            "net_pf",
            whole["profit_factor"],
            math.fsum(wins) / -math.fsum(losses) if losses else None,
        )
        if not audit["campaign_risk_audit_passed"] or audit["actual_paths_replayed"] != len(trades):
            raise ValueError("Complete actual-position risk/path audit missing")
        if (
            not audit["ranking_audit_passed"]
            or sha(account / "ranking_decisions.json") != audit["ranking_decisions_sha256"]
        ):
            raise ValueError("Actual ranking audit or log changed")
        sleeves = audit["sleeves"]
        thresholds, targets = plan["screening_targets"], plan["joint_targets"]
        generic = (
            result["economic_audit"]["passed"]
            and any(s["net_profit_usdt"] > 0 for s in sleeves.values())
            and marked["binance_no_transfer_sharpe_proxy"] is not None
        )
        joint = {
            "positive_net_account": result["net_profit_usdt"] > 0,
            "minimum_pf": whole["profit_factor"] is not None
            and whole["profit_factor"] >= thresholds["minimum_profit_factor"],
            "maximum_drawdown": max(mdd, result["native_closed_drawdown"])
            <= thresholds["maximum_observed_drawdown"],
            "minimum_complete_positions": len(trades)
            >= thresholds["minimum_closed_trades_per_evaluation"],
            "net_complete_position_winrate": whole["net_win_rate"] is not None
            and whole["net_win_rate"] >= targets["minimum_net_whole_position_win_rate"],
            "annual_return_target": result["net_return"]
            >= (1 + targets["annual_compounded_net_return_target"]) ** (len(pnl) / 365.25) - 1,
            "small_frequency": sleeves["small"]["closed_positions"] / len(pnl)
            >= targets["minimum_small_complete_trades_per_calendar_day"],
            "both_positive_sleeves": all(s["net_profit_usdt"] > 0 for s in sleeves.values()),
            "core_major_profit": sleeves["core"]["net_profit_usdt"]
            > sleeves["small"]["net_profit_usdt"],
        }
        reviewed.append(
            {
                "candidate": result["candidate"],
                "complete_positions": len(trades),
                "small_complete_per_day": sleeves["small"]["closed_positions"] / len(pnl),
                "component_economic_screen_passed": generic,
                "project_targets_are_not_official_thresholds": True,
                "joint_discovery_checks": joint,
                "joint_discovery_passed": all(joint.values()),
                "separate_capacity_diagnostic": True,
                "previous_joint_unit_admission_passed": False,
                "all_costs_and_independent_forward_passed": False,
                "production_eligible": False,
            }
        )
    return {
        "reviewed_utc": datetime.now(UTC).isoformat(),
        "passed": True,
        "accounts": reviewed,
        "accounts_checked": len(reviewed),
        "scalar_maximum_absolute_errors": errors,
        "summary_sha256": sha(folder / "summary.json"),
        "review_source_sha256": sha(Path(__file__)),
        "native_source_files_unchanged": len(source_identity()),
        "orders_enabled": False,
        "production_eligible": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args()
    destination = args.folder / "independent_review.json"
    if destination.exists():
        raise ValueError("Preserve prior review")
    result = review(args.folder)
    save(destination, result)
    print({k: result[k] for k in ["passed", "accounts_checked", "native_source_files_unchanged"]})
