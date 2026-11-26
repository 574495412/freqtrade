"""Compare listing exposure timing to actual native NFI drawdown and capacity."""

from collections import Counter
from math import ceil, isclose
from pathlib import Path

import pandas as pd

from research.crypto.listing_reversal_20261107.replay import FIVE_MINUTES_MS, HERE as LISTING_STUDY, OUT as LISTING_DATA
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .capacity_2024_drawdown_attribution import worst_drawdown
from .inputs import HERE
from .recent_two_year_attribution import ledger


NFI = {str(fee): HERE / f"recent_two_year_{fee}bp_14_verified.json" for fee in (15, 8)}
LISTING = LISTING_STUDY / "coverage_result.json"
CASES = ("continuous", "reset_2026")
DAY = pd.Timedelta(days=1)


def auxiliary(case, coverage):
    case_name = f"{case}/AllLaunches_15bps"
    folder = LISTING_DATA / "coverage_recovery" / case_name
    report = coverage["accounts"][case_name]
    fingerprints = report["audit"]["hashes"]
    readbacks = {name: {"path": str(folder / name), "sha256": digest} for name, digest in fingerprints.items()}
    nav = pd.read_parquet(checked(readbacks["nav.parquet"])).set_index("clock")
    nav.index = pd.to_datetime(nav.index * FIVE_MINUTES_MS, unit="ms", utc=True).as_unit("ns")
    assert nav.index.is_unique and nav.index.to_series().diff().dropna().eq(FIVE_MINUTES).all()
    assert nav.equity.iloc[0] == 1000 and len(nav) == report["audit"]["nav_points"]
    assert isclose(nav.equity.iloc[-1] / nav.equity.iloc[0] - 1, report["summary"]["net_return"], abs_tol=1e-12)
    _, _, decline = worst_drawdown(nav.equity)
    assert isclose(decline, report["summary"]["max_drawdown"], abs_tol=1e-12)
    trades = read(checked(readbacks["closed.json"]))
    events = read(checked(readbacks["events.json"]))
    assert len(trades) == report["summary"]["completed"] == report["audit"]["campaigns"]
    assert len(events) == report["audit"]["events"]
    return nav, report, readbacks


def interval(native, listing, peak, trough):
    result = {"peak": peak.isoformat(), "trough": trough.isoformat(),
              "native_return": float(native.at[trough, "marked_equity"] / native.at[peak, "marked_equity"] - 1),
              "listing_has_both_endpoints": peak in listing.index and trough in listing.index}
    if result["listing_has_both_endpoints"]:
        result.update(
            listing_return=float(listing.at[trough, "equity"] / listing.at[peak, "equity"] - 1),
            listing_exposed_marks=int(listing.loc[peak:trough].open_positions.gt(0).sum()),
        )
    return result


def daily_overlap(native, listing):
    common = native.index.intersection(listing.index)
    clocks = common[common == common.normalize()]
    a = native.marked_equity.reindex(clocks).pct_change(fill_method=None)
    b = listing.equity.reindex(clocks).pct_change(fill_method=None)
    maximum_positions = listing.open_positions.resample("D").max()
    prior = maximum_positions.reindex(clocks - DAY).set_axis(clocks)
    eligible = pd.DataFrame({"native": a, "listing": b}).loc[prior.gt(0)].dropna()
    native_down = eligible.loc[eligible.native < 0].sort_values("native", kind="stable")
    tail = native_down.head(ceil(len(native_down) / 10))
    return {
        "common_complete_days": len(clocks) - 1, "listing_exposed_days": len(eligible),
        "native_down_days_while_listing_exposed": len(native_down),
        "listing_up_on_native_down_days": int(native_down.listing.gt(0).sum()),
        "worst_native_decile_days": len(tail), "listing_up_on_those_days": int(tail.listing.gt(0).sum()),
        "mean_listing_return_on_those_days": float(tail.listing.mean()) if len(tail) else None,
        "adverse_dates": [
            {"day_utc": (clock - DAY).date().isoformat(), "native_return": r.native, "listing_return": r.listing}
            for clock, r in tail.iterrows()
        ],
    }


def capacity(wallet, archived_intents):
    native = wallet["curve"]
    trades = stats_from(checked(wallet["refs"][".zip"]))["trades"]
    opens = Counter(trade["open_timestamp"] for trade in trades)
    closes = Counter(trade["close_timestamp"] for trade in trades)
    statuses = Counter()
    for row in archived_intents:
        clock = pd.Timestamp(row["short_order_clock"])
        tick = clock.value // 1_000_000
        before, after = (int(native.at[at, "open_trades"]) for at in (clock, clock + FIVE_MINUTES))
        assert before + opens[tick] - closes[tick] == after
        if before + opens[tick] < 14:
            statuses["guaranteed_slot_in_observed_nfi"] += 1
        elif before - closes[tick] >= 14:
            statuses["full_even_if_existing_closes_processed_first"] += 1
        else:
            statuses["depends_on_same_candle_order"] += 1
    assert sum(statuses.values()) == len(archived_intents)
    return dict(statuses)


def main():
    protocol, listing, intents = (receipt(path) for path in (HERE / "listing_sleeve_overlap_protocol.json",
                                LISTING, HERE / "listing_sleeve_capacity.json"))
    control = {fee: receipt(path) for fee, path in NFI.items()}
    freeze = write_once(HERE / "listing_sleeve_overlap_freeze.json", {
        "source": receipt(Path(__file__)), "protocol": protocol, "listing": listing, "intents": intents,
        "native_controls": control, "orders_enabled": False,
    })
    books = {fee: ledger(read(checked(read(checked(ref))["account"]["proof"]))) for fee, ref in control.items()}
    wallet = books["15"]["curve"]
    start, end, _ = worst_drawdown(wallet.marked_equity)
    answer = {}
    for case in CASES:
        nav, report, readbacks = auxiliary(case, read(checked(listing)))
        common = wallet.index.intersection(nav.index)
        peak, trough, _ = worst_drawdown(wallet.loc[common, "marked_equity"])
        exposed = nav.loc[nav.open_positions.gt(0)]
        answer[case] = {
            "listing_sources": readbacks, "listing_reported_metrics": report["summary"],
            "listing_covered_marks": [nav.index[0].isoformat(), nav.index[-1].isoformat()],
            "listing_exposed_boundary": [exposed.index[0].isoformat(), exposed.index[-1].isoformat()],
            "full_native_max_drawdown_interval": interval(wallet, nav, start, end),
            "native_max_drawdown_while_listing_ledger_covered": interval(wallet, nav, peak, trough),
            "same_utc_day_activity": daily_overlap(wallet, nav),
        }
    opportunities = read(checked(intents))["candidates"]
    stats = {fee: capacity(book, opportunities) for fee, book in books.items()}
    write_once(HERE / "listing_sleeve_overlap_result.json", {
        "freeze": freeze, "independent_account_timing": answer, "intended_entry_clock_capacity": stats,
        "listing_intended_entry_count": len(opportunities), "no_native_listings_account": True,
        "combined_account_runs": 0, "orders_enabled": False,
    })
    print(stats, {case: row["same_utc_day_activity"] for case, row in answer.items()}, flush=True)


if __name__ == "__main__":
    main()
