"""Audit listing-short opportunities against the unchanged native NFI wallet."""

from collections import Counter, defaultdict
from math import fsum, isfinite
from pathlib import Path

import pandas as pd

from research.crypto.listing_reversal_20261107 import replay as listing
from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_trade_scale import FIVE_MINUTES
from .continuous_native_inputs import IndependentShardRead
from .inputs import HERE
from .recent_two_year_attribution import ledger


def registered_launches():
    rule = receipt(listing.PLAN)
    parent = read(checked(rule))
    assert receipt(Path(listing.__file__))["sha256"] == parent["replay_sha256"]
    intake_ref = receipt(listing.OUT / "admission.json")
    intake = read(checked(intake_ref))
    ref = receipt(listing.HERE / "coverage_result.json")
    recovery = read(checked(ref))
    assert not recovery["unresolved"] and not recovery["original_gate_changed"]
    admitted = {row["symbol"]: (row, listing.OUT / "data") for row in intake["rows"] if row["state"] == "ADMITTED"}
    for row in recovery["recovered"]:
        assert row["symbol"] not in admitted and row["state"] == "ADMITTED"
        admitted[row["symbol"]] = row, listing.OUT / "coverage_recovery/data"
    candidates = []
    for symbol, (row, base) in sorted(admitted.items()):
        path = checked({"path": str(base / symbol / "trade.parquet"), "sha256": row["trade_sha256"]})
        frame = pd.read_parquet(path)
        launched = pd.Timestamp(row["launch_utc"])
        grid = pd.date_range(launched, launched + pd.Timedelta(days=1), freq=FIVE_MINUTES, inclusive="left")
        assert len(grid) == listing.DAY and pd.DatetimeIndex(frame.index[:listing.DAY]).as_unit("ns").equals(grid.as_unit("ns"))
        first_day_quote = fsum(frame.quote_volume.iloc[:listing.DAY])
        if first_day_quote >= parent["day_quote_minimum"]:
            candidates.append({"symbol": symbol, "first_trade_clock": launched.isoformat(),
                               "short_order_clock": (launched + pd.Timedelta(days=1) + FIVE_MINUTES).isoformat(),
                               "first_day_quote_usdt": first_day_quote})
    assert len(candidates) == recovery["accounts"]["continuous/AllLaunches_15bps"]["summary"]["opportunities"]
    return candidates, {"listing_rule": rule, "intake": intake_ref, "coverage_recovery": ref,
                        "first_launch_day": parent["first_launch_day"], "last_launch_day_exclusive": parent["last_launch_day_exclusive"],
                        "listing_hold_hours": parent["hold_hours"], "validated_launches": len(admitted)}


def inspect_capacity(candidates, native, plan):
    schedule = read(checked(plan["historical_schedule"]))["days"]
    assets = read(checked(plan["market_snapshot"]))["markets"]
    by_id = {market["id"]: pair for pair, market in assets.items()}
    assert len(by_id) == len(assets)
    feed = IndependentShardRead(plan["data_receipts"])
    curve = native["curve"]
    book = stats_from(checked(native["refs"][".zip"]))["trades"]
    trades_by_pair = defaultdict(list)
    opens, closes = Counter(), Counter()
    for trade in book:
        opened, closed = (trade[f"{side}_timestamp"] for side in ("open", "close"))
        assert opened <= closed
        trades_by_pair[trade["pair"]].append((opened, closed))
        opens[opened] += 1
        closes[closed] += 1
    for candidate in candidates:
        clock = pd.Timestamp(candidate["short_order_clock"])
        start = pd.Timestamp(candidate["first_trade_clock"])
        ms = clock.value // 1_000_000
        day = schedule[clock.date().isoformat()]
        assert pd.Timestamp(day["latest_input_close"]) < clock
        pair = by_id.get(candidate["symbol"])
        candidate["native_pair"] = pair
        if pair not in day["pairs"]:
            candidate["status"] = "outside_current_80"
            continue
        tape = feed(pair, "5m-futures").set_index("date")
        first_live = tape.loc[tape.volume.gt(0)].index.min()
        if first_live != start:
            candidate["status"] = "launch_clock_not_confirmed_by_native_tape"
            candidate["native_first_positive_volume"] = None if pd.isna(first_live) else first_live.isoformat()
            continue
        row = tape.reindex([clock]).iloc[0]
        if not all(isfinite(value) and value > 0 for value in (row.open, row.volume)):
            candidate["status"] = "no_positive_volume_native_entry_bar"
            continue
        before = int(curve.at[clock, "open_trades"])
        after = int(curve.at[clock + FIVE_MINUTES, "open_trades"])
        assert before + opens[ms] - closes[ms] == after
        candidate["native_nfi_before"] = before
        candidate["nfi_opens_on_entry_bar"] = opens[ms]
        candidate["nfi_closes_on_entry_bar"] = closes[ms]
        if any(opened <= ms <= closed for opened, closed in trades_by_pair[pair]):
            candidate["status"] = "native_pair_already_held_or_opening"
        elif after >= 12:
            candidate["status"] = "native_capacity_full"
        elif before + opens[ms] >= 12:
            candidate["status"] = "depends_on_same_bar_closes"
        else:
            candidate["status"] = "slot_visible_in_baseline"
    return dict(feed.calls)


def main():
    candidates, listing_sources = registered_launches()
    basis = receipt(HERE / "recent_two_year_15bp_12_verified.json")
    audited = read(checked(basis))["account"]
    plan = read(checked(audited["registered_plan"]))
    assert plan["candidates"][0]["max_open_trades"] == 12 and plan["allowed_leverage"] == 3
    wallet = ledger(read(checked(audited["proof"])))
    sources = inspect_capacity(candidates, wallet, plan)
    statuses = {}
    for year in ("2025", "2026"):
        rows = [row for row in candidates if row["short_order_clock"].startswith(year)]
        statuses[year] = dict(Counter(row["status"] for row in rows))
    out = {"source": receipt(Path(__file__)), "protocol": receipt(HERE / "listing_sleeve_protocol.json"),
           "nfi_native_account": basis, "listing_source": listing_sources,
           "year_status_counts": statuses, "aggregate": dict(Counter(row["status"] for row in candidates)),
           "native_candle_reads": sources, "candidates": candidates,
           "scope": "First-day listing opportunities from independently reconciled historical research inputs. "
                    "Intersect with the existing NFI universe, native quote clock and observed baseline occupancy. "
                    "No joint cash, executable fill, or account return is inferred.",
           "new_native_accounts": 0, "orders_enabled": False}
    write_once(HERE / "listing_sleeve_capacity.json", out)
    print(len(candidates), out["aggregate"], statuses, flush=True)


if __name__ == "__main__":
    main()
