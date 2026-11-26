"""Reconcile the 2024 stress-window difference by native first fill."""

from math import fsum, isclose
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.head_shoulders_financial_native_20261117_v3 import independent_account_audit_v2
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .capacity_2024_drawdown_attribution import market_streams
from .inputs import HERE
from .recent_two_year_attribution import ledger
from .volatility_across_years import annual_plan, selected_receipt


def first_fill(trade):
    return trade["pair"], trade["is_short"], trade["open_timestamp"]


def account_campaigns(account, dates):
    plan, _, refs = annual_plan(account, 2024)
    native = selected_receipt(account["receipts"], ".zip")
    curve = pd.read_parquet(checked(refs["nav"])).set_index("date").loc[dates]
    fingerprints = read(checked(plan["data_receipts"]))
    trades = stats_from(checked(native))["trades"]
    clocks = dates.as_unit("ms").asi8
    by_fill, prices = {}, {}
    for trade in trades:
        opened, closed = trade["open_timestamp"], trade["close_timestamp"]
        if closed < clocks[0] or opened >= clocks[-1]:
            continue
        pair = trade["pair"]
        if pair not in prices:
            prices[pair] = market_streams(plan, fingerprints, pair)
        marks, events, rates = prices[pair]
        held = (clocks > opened) & (clocks <= closed)
        observed = marks.reindex(dates).to_numpy()
        funding = (events >= opened) & (events <= closed)
        if not (np.isfinite(observed[held]) & (observed[held] > 0)).all():
            raise ValueError(f"Missing mark for an open campaign in {pair}")
        pnl, exposure, _, settled_funding, _ = independent_account_audit_v2.trade_path(
            trade, clocks, np.where(held, observed, 0), events[funding], rates[funding],
        )
        if not isclose(settled_funding, trade["funding_fees"], abs_tol=1e-7, rel_tol=0):
            raise ValueError(f"Funding does not reconcile for {pair} at {opened}")
        key = first_fill(trade)
        if key in by_fill:
            raise ValueError(f"Duplicate campaign first fill {key}")
        by_fill[key] = {
            "tag": trade["enter_tag"],
            "close": pd.Timestamp(closed, unit="ms", tz="UTC").isoformat(),
            "already_held_at_peak": bool(held[0]),
            "held_at_trough": bool(held[1]),
            "entry_fills_during_interval": sum(
                1
                for row in trade["orders"]
                if row["ft_is_entry"] and clocks[0] <= row["order_filled_timestamp"] < clocks[-1]
            ),
            "marked_pnl_change_usdt": float(pnl[1] - pnl[0]),
            "return_contribution": float((pnl[1] - pnl[0]) / curve.marked_equity.iloc[0]),
            "peak_gross_notional_to_nav": float(exposure[0] / curve.marked_equity.iloc[0]),
            "trough_gross_notional_to_nav": float(exposure[1] / curve.marked_equity.iloc[1]),
        }
    return by_fill, {first_fill(trade) for trade in trades}, refs | {"native_trades": native}


def summarize(book, other, expected):
    partition = {"matched_first_fill": [], "different_first_fill": []}
    for key, row in sorted(book.items()):
        pair, short, timestamp = key
        group = "matched_first_fill" if key in other else "different_first_fill"
        partition[group].append({
            "pair": pair,
            "side": "short" if short else "long",
            "first_fill": pd.Timestamp(timestamp, unit="ms", tz="UTC").isoformat(),
            **row,
        })
    metrics = ("return_contribution", "peak_gross_notional_to_nav", "trough_gross_notional_to_nav")
    sums = {
        group: {
            "campaigns": rows,
            **{metric: fsum(row[metric] for row in rows) for metric in metrics},
        }
        for group, rows in partition.items()
    }
    targets = (
        expected["interval_return"],
        *(row["mark_gross_notional_to_equity_reconstructed"] for row in expected["snapshots"]),
    )
    for metric, value in zip(metrics, targets, strict=True):
        if not isclose(fsum(row[metric] for row in sums.values()), value, abs_tol=1e-8, rel_tol=0):
            raise ValueError(f"The campaign attribution does not reconcile for {metric}")
    return sums


def occupancy(curve, native, checks, selected):
    stats = stats_from(checked(native))
    unfilled = ("timedout_entry_orders", "canceled_trade_entries", "replaced_entry_orders")
    if any(stats[name] for name in unfilled):
        raise ValueError("Cannot establish fill order from confirmations with unfilled orders")
    confirmed = read(checked(checks))
    trades = stats["trades"]
    step = pd.Timedelta(minutes=5)
    results = []
    for row in selected:
        clock = pd.Timestamp(row["first_fill"])
        millis = clock.value // 1_000_000
        before = int(curve.at[clock, "open_trades"])
        after = int(curve.at[clock + step, "open_trades"])
        accepted = [
            data for data in confirmed
            if data["accepted"] and pd.Timestamp(data["time"]) == clock
        ]
        pairs = [data["pair"] for data in accepted]
        filled = [trade["pair"] for trade in trades if trade["open_timestamp"] == millis]
        exits = sum(trade["close_timestamp"] == millis for trade in trades)
        if sorted(pairs) != sorted(filled) or len(set(pairs)) != len(pairs):
            raise ValueError(f"Accepted entry sequence does not match native fills at {clock}")
        if before + len(pairs) - exits != after or not all(
            data["inside_current_pool"] for data in accepted
        ):
            raise ValueError(f"Native filled positions do not reconcile at {clock}")
        order = before + pairs.index(row["pair"]) + 1
        results.append({
            "pair": row["pair"],
            "first_fill": row["first_fill"],
            "positions_before_candle": before,
            "positions_after_candle": after,
            "entry_slot_bounds": [max(1, order - exits), min(stats["max_open_trades"], order)],
        })
    return results


def compared_occupancy(stress, stress_sources):
    stress_nav = pd.read_parquet(checked(stress_sources["nav"])).set_index("date")
    stress_checks = receipt(
        checked(stress_sources["native_plan"]).with_name("initial_entry_checks.json")
    )
    recent_ref = receipt(HERE / "recent_two_year_15bp_comparison.json")
    recent = read(checked(recent_ref))
    account = recent["accounts"]["14"]
    native_wallet = ledger(read(checked(account["proof"])))
    checks = receipt(
        checked(account["execution"]).parent / "account" / "initial_entry_checks.json"
    )
    extra = recent["fill_and_nav_attribution"]["trade_changes"]["native_entries_only_14"]
    recent_slots = occupancy(native_wallet["curve"], native_wallet["refs"][".zip"], checks, extra)
    for slots, row in zip(recent_slots, extra, strict=True):
        slots["over_1pct_of_prior_equity"] = row["over_1pct_of_prior_equity"]
    return {
        "stress_2024": occupancy(
            stress_nav, stress_sources["native_trades"], stress_checks, stress,
        ),
        "near_two_years": recent_slots,
        "sources": {
            "stress_initial_entry_checks": stress_checks,
            "recent_initial_entry_checks": checks,
            "recent_account_comparison": recent_ref,
        },
        "scope": "Entry rank bounds on the observed 14-slot paths, allowing for same-candle exits.",
    }


def main():
    reference = receipt(HERE / "annual_2024_capacity_drawdown_attribution.json")
    audit = read(checked(reference))
    window = audit["windows"]["boost_14"]
    dates = pd.DatetimeIndex([window["start"], window["end"]]).as_unit("ns")
    accounts = read(checked(audit["account_comparison"]))["accounts"]
    control, control_fills, control_sources = account_campaigns(accounts["fixed_boost_12"], dates)
    candidate, candidate_fills, candidate_sources = account_campaigns(accounts["boost_14"], dates)
    left = summarize(control, candidate_fills, window["same_interval"]["fixed_boost_12"])
    right = summarize(candidate, control_fills, window["same_interval"]["boost_14"])
    delta = {
        group: right[group]["return_contribution"] - left[group]["return_contribution"]
        for group in left
    }
    write_once(HERE / "stress_campaign_comparison.json", {
        "source": receipt(Path(__file__)),
        "original_drawdown_audit": reference,
        "start": window["start"],
        "end": window["end"],
        "cost_bps_per_side": audit["cost_per_side_bp"],
        "accounts": {"12": left, "14": right},
        "account_sources": {"12": control_sources, "14": candidate_sources},
        "return_difference_14_minus_12": delta,
        "entry_occupancy": compared_occupancy(
            right["different_first_fill"]["campaigns"], candidate_sources,
        ),
        "scope": (
            "Observed campaign PnL and gross notional on the same two marks. "
            "Matching uses instrument, side and first fill timestamp in the full accounts. "
            "Different first fills include potential changes in timing."
        ),
        "orders_enabled": False,
    })
    print(delta)


if __name__ == "__main__":
    main()
