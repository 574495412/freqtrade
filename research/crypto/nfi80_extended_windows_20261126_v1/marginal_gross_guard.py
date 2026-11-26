"""Check a proposed marginal-long gross limit on verified native order paths."""

from collections import defaultdict
from math import fsum
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .capacity_2024_drawdown_attribution import market_streams
from .continuous_native_inputs import IndependentShardRead
from .inputs import HERE
from .recent_two_year_attribution import ledger
from .stress_campaign_comparison import occupancy


STRESS = HERE / "stress_campaign_comparison.json"
COMPARISONS = {fee: HERE / f"recent_two_year_{fee}bp_comparison.json" for fee in (15, 8)}
GROSS_LIMIT = 3


def band_exposure(trades, marks, dates):
    clocks = dates.as_unit("ms").asi8
    gross = np.zeros(len(dates))
    counts = np.zeros(len(dates), dtype=int)
    for trade in trades:
        held = (clocks > trade["open_timestamp"]) & (clocks <= trade["close_timestamp"])
        if not held.any():
            continue
        orders = trade["orders"]
        times = np.asarray([o["order_filled_timestamp"] for o in orders])
        assert (np.diff(times) >= 0).all()
        quantities = np.asarray([0, *np.cumsum([
            o["amount"] if o["ft_is_entry"] else -o["amount"] for o in orders
        ])])
        at = times.searchsorted(clocks[held], side="left")
        owned = quantities[at]
        quote = marks(trade["pair"], dates[held])
        assert (owned > 0).all() and np.isfinite(quote).all() and (quote > 0).all()
        gross[held] += owned * quote
        counts += held
    return gross, counts


def review(curve, archive, checks, entries, selected, marks):
    book = stats_from(checked(archive))["trades"]
    dates = pd.DatetimeIndex(sorted({pd.Timestamp(e["first_fill"]) for e in entries})).as_unit("ns")
    observed, count = band_exposure(book, marks, dates)
    nav = curve.loc[dates]
    np.testing.assert_array_equal(count, nav.open_trades)
    pre_gross = pd.Series(observed, index=dates)
    confirm = defaultdict(list)
    for check in read(checked(checks)):
        if check["accepted"]:
            confirm[pd.Timestamp(check["time"])].append(check["pair"])
    initial = defaultdict(dict)
    adjustments = set()
    for trade in book:
        first, *later = trade["orders"]
        opened = trade["open_timestamp"]
        assert first["ft_is_entry"] and first["order_filled_timestamp"] == opened
        initial[opened][trade["pair"]] = first["amount"] * first["safe_price"]
        adjustments.update(o["order_filled_timestamp"] for o in later)

    results = []
    for row, attributes in zip(entries, selected, strict=True):
        assert row["pair"] == attributes["pair"] and row["first_fill"] == attributes["first_fill"]
        date = pd.Timestamp(row["first_fill"])
        clock = date.value // 1_000_000
        first_fills = confirm[date]
        assert len(first_fills) == len(initial[clock]) and set(first_fills) == initial[clock].keys()
        earlier = first_fills[:first_fills.index(row["pair"]) + 1]
        first_nav = float(curve.at[date, "marked_equity"])
        assert first_nav > 0
        forecast = None
        low, high = row["entry_slot_bounds"]
        if attributes.get("side", "long") == "short" or high <= 12:
            decision = "outside_rule"
        elif low <= 12 or clock in adjustments:
            decision = "unresolved"
        else:
            forecast = (float(pre_gross.at[date]) + fsum(initial[clock][pair] for pair in earlier)) / first_nav
            decision = "would_block" if forecast > GROSS_LIMIT else "would_allow"
        results.append({
            **attributes, **row, "pre_candle_marked_nav_usdt": first_nav,
            "pre_candle_gross_mark_to_nav": float(pre_gross.at[date]) / first_nav,
            "projected_gross_mark_to_nav": forecast, "decision": decision,
        })
    return results


def annual(artifact):
    refs = artifact["account_sources"]["14"]
    checks = artifact["entry_occupancy"]["sources"]["stress_initial_entry_checks"]
    entries = artifact["entry_occupancy"]["stress_2024"]
    campaigns = artifact["accounts"]["14"]["different_first_fill"]["campaigns"]
    targets = [{"pair": c["pair"], "first_fill": c["first_fill"], "side": c["side"],
                "marked_loss_to_trough_usdt": c["marked_pnl_change_usdt"]} for c in campaigns]
    plan = read(checked(refs["native_plan"]))
    fingerprints = read(checked(plan["data_receipts"]))
    curve = pd.read_parquet(checked(refs["nav"])).set_index("date")

    def marks(pair, dates):
        values, _, _ = market_streams(plan, fingerprints, pair)
        return values.reindex(dates).to_numpy()

    return review(curve, refs["native_trades"], checks, entries, targets, marks)


def recent(artifact):
    account = artifact["accounts"]["14"]
    wallet = ledger(read(checked(account["proof"])))
    plan = read(checked(account["registered_plan"]))
    target = artifact["fill_and_nav_attribution"]["trade_changes"]["native_entries_only_14"]
    checks = receipt(checked(account["execution"]).parent / "account/initial_entry_checks.json")
    archive = wallet["refs"][".zip"]
    entries = occupancy(wallet["curve"], archive, checks, target)
    reader = IndependentShardRead(plan["data_receipts"])

    def marks(pair, dates):
        values = reader(pair, "5m-mark").set_index("date").open
        return values.reindex(dates).to_numpy()

    return review(wallet["curve"], archive, checks, entries, target, marks)


def main():
    protocol = receipt(HERE / "marginal_gross_guard_protocol.json")
    stress = receipt(STRESS)
    recent_refs = {str(fee): receipt(path) for fee, path in COMPARISONS.items()}
    freeze = write_once(HERE / "marginal_gross_guard_freeze.json", {
        "protocol": protocol, "stress": stress, "recent": recent_refs,
        "source": receipt(Path(__file__)), "orders_enabled": False,
    })
    early = annual(read(checked(stress)))
    primary = {fee: recent(read(checked(ref))) for fee, ref in recent_refs.items()}
    stress_passed = any(row["pair"] in ("ETH/USDT:USDT", "ONG/USDT:USDT")
                        and row["decision"] == "would_block" for row in early)
    winners = {fee: all(row["decision"] in ("outside_rule", "would_allow") for row in rows
                        if row["over_1pct_of_prior_equity"]) for fee, rows in primary.items()}
    write_once(HERE / "marginal_gross_guard_result.json", {
        "freeze": freeze, "stress_2024": early, "near_two_years": primary,
        "stress_hit": stress_passed, "large_winners_retained": winners,
        "advance": stress_passed and all(winners.values()), "orders_enabled": False,
    })
    print(stress_passed, winners, flush=True)


if __name__ == "__main__":
    main()
