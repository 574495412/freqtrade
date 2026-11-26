"""Audit the forward continuation of underwater Top Coins positions at one week."""

from math import fsum, isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3.independent_account_audit_v2 import trade_path
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .capacity_2024_drawdown_attribution import market_streams
from .continuous_native_inputs import IndependentShardRead
from .inputs import HERE
from .long_adjustment_margin import mode_tags
from .recent_two_year_attribution import ledger, reports


CHECKPOINT = pd.Timedelta(days=7)


def sharded_streams(reader, pair):
    prices = reader(pair, "5m-mark").set_index("date").open
    rates = reader(pair, "1h-funding_rate").set_index("date").funding_rate
    hourly = reader(pair, "1h-mark").set_index("date").open.reindex(rates.index)
    assert rates.index.is_unique and rates.index.is_monotonic_increasing
    return prices, rates.index.as_unit("ms").asi8, (hourly * rates).to_numpy()


def campaign_marks(trade, dates, streams):
    prices, events, funding = streams
    clocks = dates.as_unit("ms").asi8
    assert ((clocks > trade["open_timestamp"]) & (clocks <= trade["close_timestamp"])).all()
    marked_prices = prices.reindex(dates).to_numpy()
    assert (np.isfinite(marked_prices) & (marked_prices > 0)).all()
    held = (events >= trade["open_timestamp"]) & (events <= trade["close_timestamp"])
    assert np.isfinite(funding[held]).all()
    path, exposure, _, final_funding, _ = trade_path(trade, clocks, marked_prices, events[held], funding[held])
    assert isclose(final_funding, trade["funding_fees"], rel_tol=0, abs_tol=1e-7)
    return path, exposure


def summary(rows):
    stops = [row for row in rows if row["time_exit_candidate"]]
    natural = [row for row in stops if row["endpoint_status"] == "natural_close"]
    return {"alive_at_day_7": len(rows), "underwater_at_day_7": len(stops),
            "candidate_natural_closes": len(natural), "candidate_terminal_marks": len(stops) - len(natural),
            "continuation_improved": sum(row["observed_continuation_usdt"] > 0 for row in stops),
            "continuation_deteriorated": sum(row["observed_continuation_usdt"] < 0 for row in stops),
            "later_large_natural_wins": sum(row["natural_win_at_least_1pct"] is True for row in natural),
            "missing_prior_nav_for_natural_closes": sum(row["natural_win_at_least_1pct"] is None for row in natural),
            "net_continuation_usdt": fsum(row["observed_continuation_usdt"] for row in stops)}


def assess(account, source_tags):
    data = ledger(account)
    archive = checked(data["refs"][".zip"])
    plan_source = receipt(archive.parents[1] / "registered_plan.json")
    plan = read(checked(plan_source))
    timeline = data["curve"]
    cutoff = timeline.index[-1]
    fingerprints = read(checked(plan["data_receipts"]))
    reader = IndependentShardRead(plan["data_receipts"]) if "files" in fingerprints else None
    every = stats_from(archive)["trades"]
    streams, rows = {}, []
    count = 0
    for trade in every:
        tags = set((trade["enter_tag"] or "").split())
        if trade["is_short"] or not tags or not tags <= source_tags:
            continue
        count += 1
        opened, closed = (pd.Timestamp(trade[f"{time}_timestamp"], unit="ms", tz="UTC") for time in ("open", "close"))
        checkpoint = opened + CHECKPOINT
        if checkpoint not in timeline.index or checkpoint >= closed:
            continue
        natural = trade["exit_reason"] != "force_exit" and closed < cutoff
        dates = pd.DatetimeIndex([checkpoint] if natural else [checkpoint, cutoff]).as_unit("ns")
        pair = trade["pair"]
        if pair not in streams:
            streams[pair] = sharded_streams(reader, pair) if reader is not None else market_streams(plan, fingerprints, pair)
        pnl, exposure = campaign_marks(trade, dates, streams[pair])
        endpoint = trade["profit_abs"] if natural else float(pnl[1])
        before_open = timeline.marked_equity.get(opened - FIVE_MINUTES)
        threshold = .01 * before_open if before_open is not None and before_open > 0 else None
        large_win = bool(endpoint >= threshold) if natural and threshold is not None else None
        rows.append({"pair": pair, "tags": sorted(tags), "first_fill": opened.isoformat(), "checkpoint": checkpoint.isoformat(),
                     "marked_campaign_at_checkpoint_usdt": float(pnl[0]), "mark_notional_at_checkpoint_usdt": float(exposure[0]),
                     "time_exit_candidate": bool(pnl[0] < 0), "endpoint_clock": (closed if natural else cutoff).isoformat(),
                     "endpoint_status": "natural_close" if natural else "terminal_mark",
                     "endpoint_campaign_pnl_usdt": endpoint, "observed_continuation_usdt": float(endpoint - pnl[0]),
                     "natural_win_at_least_1pct": large_win})
    return {"account_source": data["refs"], "plan": plan_source, "pure_top_coins_campaigns": count,
            "by_checkpoint_year": {year: summary([row for row in rows if row["checkpoint"].startswith(year)])
                                   for year in ("2025", "2026")},
            "summary": summary(rows), "campaigns": rows}


def main():
    primary = receipt(HERE / "recent_two_year_15bp_12_verified.json")
    control = read(checked(primary))["account"]
    plan = read(checked(control["registered_plan"]))
    strategy = plan["candidates"][0]["source_files"]["NostalgiaForInfinityX8.py"]
    tags = mode_tags(checked(strategy))["Top Coins"]
    full = assess(read(checked(control["proof"])), tags)
    other_cost = {name: {"summary_source": source, **assess(a, tags)} for name, source, a, _ in reports(8)}
    periods = full["by_checkpoint_year"]
    all_results = (full, *other_cost.values())
    flags = {
        "negative_continuation_both_years": all(row["net_continuation_usdt"] < 0 for row in periods.values()),
        "candidate_support_both_years": all(row["underwater_at_day_7"] > 0 for row in periods.values()),
        "no_observed_large_winner_cut": all(not row["summary"]["later_large_natural_wins"] for row in all_results),
        "prior_nav_available": all(not row["summary"]["missing_prior_nav_for_natural_closes"] for row in all_results),
    }
    result = {"source": receipt(Path(__file__)), "protocol": receipt(HERE / "top_coins_seven_day_exit_protocol.json"),
              "native_strategy": strategy, "source_tags": sorted(tags), "continuous_basis": primary, "continuous_15bp": full,
              "standalone_reset_windows_8bp": other_cost, "checks": flags, "advance": all(flags.values()),
              "new_native_accounts": 0, "orders_enabled": False}
    write_once(HERE / "top_coins_seven_day_exit.json", result)
    print(periods, flags, flush=True)


if __name__ == "__main__":
    main()
