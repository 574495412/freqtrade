"""Extend native continuous candles through November 25 without duplicating tapes."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_account_admission_20261117_v1.account_audit import stats_from
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .annual_inputs import MAX_FUNDING_GAP, resample
from .continuous_native_inputs import candle_filename
from .continuous_seams import INTERVALS, MARKET_FIELDS
from .continuous_shards import ROOT as ORIGINAL
from .inputs import HERE, OUT, bar_rows, candles, concurrently, milliseconds, public_rows


ROOT = OUT / "continuous_wallet_november_inputs_20261126"
PROTOCOL = HERE / "continuous_november_protocol.json"
DAY = pd.Timedelta(days=1)
BAR = pd.Timedelta(minutes=5)
HOUR = pd.Timedelta(hours=1)
CASH = "1h-funding_rate"


def sources():
    baseline = receipt(ORIGINAL / "shards.json")
    earlier = read(checked(baseline))
    recent = receipt(OUT / "registrations/fixed_boost_12-plan.json")
    later = read(checked(recent))
    previous_schedule = read(checked(earlier["schedule"]))
    later_schedule = read(checked(later["historical_schedule"]))
    assert earlier["engine_commit"] == later["engine_commit"]
    markets = read(checked(earlier["market_snapshot"]))
    recent_markets = read(checked(later["market_snapshot"]))
    union = previous_schedule["union"]
    open_only = sorted(set(union) - set(later["pairs"]))
    assert len(union) == 222 and len(open_only) == 110 and set(union) == set(later_schedule["daily_sources"])
    assert set(union) == set(markets["markets"]) == set(markets["leverage_tiers"])
    for pair in later["pairs"]:
        before, after = markets["markets"][pair], recent_markets["markets"][pair]
        assert all(before.get(k) == after.get(k) for k in MARKET_FIELDS), pair
        assert markets["leverage_tiers"][pair] == recent_markets["leverage_tiers"][pair], pair
    contract = read(PROTOCOL)
    seam = pd.Timestamp(contract["seam_utc"])
    end = pd.Timestamp(contract["window"]["end_exclusive_utc"])
    assert pd.Timestamp(earlier["stop_exclusive"]).normalize() == seam == pd.Timestamp(later["start"]) - DAY
    assert end == pd.Timestamp(later["end"]) and earlier["first_time"] == contract["window"]["start_utc"]
    return baseline, recent, earlier, later, previous_schedule, later_schedule, markets, open_only, seam, end


def freeze_inputs():
    baseline, recent, earlier, later, prev_day, late_day, _, missing, seam, end = sources()
    leaves = {}
    for fee in (15, 8):
        folder = HERE.parent / "nfi80_online_baseline_20261125_v1"
        comparison = receipt(folder / f"comparison_{fee}bp.json")
        account_proofs = read(checked(comparison))
        for name in ("control", "candidate"):
            row = account_proofs[name]
            assert row["five_minute_nav_reconciliation"]["passed"]
            raw, = (source for source in row["sources"] if source["path"].endswith(".zip"))
            alive = [trade["pair"] for trade in stats_from(checked(raw))["trades"] if trade["exit_reason"] == "force_exit"]
            assert set(alive) <= set(later["pairs"])
            leaves[f"{name}_{fee}bp"] = {"comparison": comparison, "native_archive": raw, "open_at_prior_terminal": alive}
    ROOT.mkdir(parents=True, exist_ok=True)
    frozen = ROOT / "frozen_inputs.json"
    value = {
        "capture_code": receipt(Path(__file__)), "protocol": receipt(PROTOCOL),
        "earlier_manifest": baseline, "later_plan": recent,
        "pairs_reused": later["pairs"], "pairs_requiring_capture": missing,
        "old_known_open_positions": leaves, "supplement_market_kline_start": str(seam - DAY),
        "extended_stop": str(end), "frozen_before_extended_financial_results": True, "orders_enabled": False,
    }
    if frozen.exists() and read(frozen) != value:
        initial = read(frozen)
        capture_source = receipt(ROOT / "capture_source_initial.py")
        assert capture_source["sha256"] == initial["capture_code"]["sha256"]
        assert {key: row for key, row in initial.items() if key != "capture_code"} == {
            key: row for key, row in value.items() if key != "capture_code"
        }
        value.update({"initial_capture_freeze": receipt(frozen), "initial_capture_code": capture_source,
                      "technical_revision": "Normalize clock dtype and rebuild the final truncated hourly mark"})
        frozen = ROOT / "frozen_assembly_inputs.json"
    return write_once(frozen, value)


def verify_grid(frame, kind, start, end):
    dates = pd.DatetimeIndex(frame["date"]).as_unit("ns")
    if kind == CASH:
        assert len(dates) > 1 and dates.is_unique and dates.is_monotonic_increasing
        assert np.isfinite(frame["funding_rate"]).all()
        gaps = dates.to_series().diff().dropna()
        assert max(dates[0] - start, end - dates[-1], gaps.max()) <= MAX_FUNDING_GAP
        assert start <= dates[0] <= dates[-1] < end
    else:
        grid = pd.date_range(start, end, freq=INTERVALS[kind], inclusive="left").as_unit("ns")
        assert dates.equals(grid), (kind, len(grid), len(dates))
        assert np.isfinite(frame.drop(columns="date")).all(axis=None)
        assert frame[["open", "high", "low", "close"]].gt(0).all(axis=None)
        assert frame["volume"].ge(0).all()


def supplemental(pair, symbol, daily_receipt, seam, end):
    folder = ROOT / "supplements" / symbol
    anchor = folder / "capture.json"
    folder.mkdir(parents=True, exist_ok=True)
    if anchor.exists():
        recorded = read(anchor)
        for record in [*recorded["tapes"].values(), *recorded["raw_exchange_inputs"], recorded["daily_receipt"]]:
            checked(record)
        return recorded
    start = seam - DAY
    raw_refs = []
    five_minute = {}
    for kind, route in (("5m-futures", "klines"), ("5m-mark", "markPriceKlines")):
        rows, captures = bar_rows(symbol, route, start, end, "5m", BAR)
        five_minute[kind] = candles(rows, start, end, BAR)
        raw_refs.extend(captures)
    trade, marks = five_minute["5m-futures"], five_minute["5m-mark"]
    bound = [row for row in read(checked(daily_receipt)) if milliseconds(start) <= int(row[0]) < milliseconds(end)]
    daily = candles(bound, start, end, DAY)
    dates, amounts = [], []
    for left in pd.date_range(start, end, freq="7D", inclusive="left"):
        rows, proof = public_rows(symbol, "fundingRate", left, min(left + 7 * DAY, end), 200)
        assert len(rows) < 200 and all(row["symbol"] == symbol for row in rows)
        dates.extend(int(row["fundingTime"]) for row in rows)
        amounts.extend(float(row["fundingRate"]) for row in rows)
        raw_refs.append(proof)
    observed = pd.DatetimeIndex(pd.to_datetime(dates, unit="ms", utc=True))
    nominal = observed.floor("h")
    assert np.all(observed - nominal < pd.Timedelta(seconds=1))
    rates = pd.DataFrame({"date": nominal, "funding_rate": amounts})
    tapes = {
        **five_minute, "15m-futures": resample(trade, 3 * BAR), "1h-futures": resample(trade, HOUR),
        "4h-futures": resample(trade, 4 * HOUR), "1d-futures": daily,
        "1h-mark": resample(marks, HOUR), CASH: rates,
    }
    files = {}
    for kind, frame in tapes.items():
        verify_grid(frame, kind, start, end)
        target = folder / Path(candle_filename(pair, kind)).name
        frame["date"] = frame["date"].dt.as_unit("us")
        frame.reset_index(drop=True).to_feather(target)
        files[kind] = receipt(target)
    record = {"symbol": symbol, "daily_receipt": daily_receipt, "tapes": files, "raw_exchange_inputs": raw_refs,
              "start": str(start), "stop_exclusive": str(end)}
    write_once(anchor, record)
    return record


def exact_overlap(older, fresh, kind, anchor, stop, derived):
    def bounded(frame):
        frame = frame.set_index("date")
        frame.index = frame.index.as_unit("ns")
        return frame.loc[(frame.index >= anchor) & (frame.index < stop)]
    previous, now = (bounded(frame) for frame in (older, fresh))
    if not len(previous) or not previous.index.isin(now.index).all():
        raise ValueError(f"Prior observations unavailable at the {kind} seam")
    if derived and kind.endswith("futures"):
        np.testing.assert_allclose(previous["volume"], now.loc[previous.index, "volume"], rtol=1e-10, atol=1e-12)
        previous, now = previous.drop(columns="volume"), now.drop(columns="volume")
    pd.testing.assert_frame_equal(previous, now.loc[previous.index], check_exact=True, check_freq=False)
    return len(previous)


def complete_hourly(pair, older, recent_files, start, end, previous_end):
    """Rebuild the one shortened hourly mark from the extended 5-minute tape."""
    hour = previous_end.floor("h")
    assert previous_end - hour == BAR
    five_key, hour_key = (candle_filename(pair, kind) for kind in ("5m-mark", "1h-mark"))
    old_minute, old_hour = (pd.read_feather(checked(older[key][-1])) for key in (five_key, hour_key))
    old_partial = old_minute.loc[old_minute.date.ge(hour) & old_minute.date.lt(previous_end)]
    old_hour = old_hour.loc[old_hour.date.eq(hour)]
    assert len(old_partial) == 1 and len(old_hour) <= 1
    fields = ["open", "high", "low", "close", "volume"]
    if len(old_hour):
        np.testing.assert_array_equal(old_partial[fields].to_numpy(), old_hour[fields].to_numpy())
    minute = pd.read_feather(checked(recent_files["5m-mark"]))
    minute = minute.loc[minute.date.ge(start) & minute.date.lt(end)]
    completed = resample(minute, HOUR)
    verify_grid(completed, "1h-mark", start, end)
    hourly_source = pd.read_feather(checked(recent_files["1h-mark"]))
    hourly_source = hourly_source.loc[hourly_source.date.ge(start) & hourly_source.date.lt(end)]
    if len(hourly_source.loc[hourly_source.date.eq(hour)]) != 1:
        raise ValueError(f"Missing continuation mark at {pair} {hour}")
    # Outside the truncated hour the reconstructed marks must match every observed row.
    for frame in (completed, hourly_source):
        frame["date"] = frame["date"].dt.as_unit("us")
    complete_other = completed.loc[completed.date.ne(hour)].reset_index(drop=True)
    source_other = hourly_source.loc[hourly_source.date.ne(hour)].reset_index(drop=True)
    pd.testing.assert_frame_equal(complete_other, source_other, check_exact=True, check_dtype=False)
    before = old_partial[fields].iloc[0]
    fixed = completed.loc[completed.date.eq(hour), fields].iloc[0]
    assert fixed["open"] == before["open"]
    assert fixed["high"] >= before["high"] and fixed["low"] <= before["low"]
    assert fixed["volume"] >= before["volume"]
    output = ROOT / "completed_hourly_marks" / Path(hour_key).name
    output.parent.mkdir(exist_ok=True)
    if output.exists():
        pd.testing.assert_frame_equal(completed, pd.read_feather(output), check_exact=True)
    else:
        completed.to_feather(output)
    return receipt(output), {"from_5m": recent_files["5m-mark"], "reference_hourly": recent_files["1h-mark"],
                             "old_5m_observations": len(old_partial), "old_hourly_present": bool(len(old_hour)),
                             "complete_hour": fixed.to_dict(), "first_five_minutes": before.to_dict(),
                             "changes": [f for f in fields if fixed[f] != before[f]]}


def assemble():
    evidence = freeze_inputs()
    baseline, recent, earlier, later, calendars, new_days, venue, missing, seam, end = sources()
    added_pool = {**calendars["days"], **new_days["days"]}
    assert set(calendars["days"]).isdisjoint(new_days["days"])
    start = pd.Timestamp(earlier["first_time"])
    days = pd.date_range(start, end, freq="D", inclusive="left")
    assert list(added_pool) == days.strftime("%Y-%m-%d").tolist()
    streams = {}
    later_hashes = read(checked(later["data_receipts"]))
    all_overlap, readiness, complete_hours = Counter(), [], {}
    before = pd.Timestamp(earlier["stop_exclusive"])
    for pair in calendars["union"]:
        is_supplement = pair in missing
        if is_supplement:
            symbol = venue["markets"][pair]["id"]
            supplement = receipt(ROOT / "supplements" / symbol / "capture.json")
            details = read(checked(supplement))
            assert pd.Timestamp(details["start"]) == seam - DAY and pd.Timestamp(details["stop_exclusive"]) == end
            readiness.append(supplement)
            late = details["tapes"]
        else:
            late = {kind: {"path": str(Path(later["datadir"]) / candle_filename(pair, kind)),
                           "sha256": later_hashes[candle_filename(pair, kind)]} for kind in INTERVALS}
        recovered, hour_evidence = complete_hourly(pair, earlier["files"], late, seam - DAY, end, before)
        late = {**late, "1h-mark": recovered}
        complete_hours[pair] = hour_evidence
        for kind in INTERVALS:
            key = candle_filename(pair, kind)
            current = earlier["files"][key]
            last = current[-1]
            new = late[kind]
            old_data, fresh = (pd.read_feather(checked(item)) for item in (last, new))
            derived = is_supplement and kind in {"15m-futures", "1h-futures", "4h-futures"}
            closed_boundary = before.floor("h") if kind == "1h-mark" else before
            all_overlap[kind] += exact_overlap(old_data, fresh, kind, seam - DAY, closed_boundary, derived)
            clipped = fresh.loc[(fresh.date >= seam) & (fresh.date < end)]
            verify_grid(clipped, kind, seam, end)
            suffix = {"source_year": "2026_november", **new, "start_inclusive": seam.isoformat(),
                      "stop_exclusive": end.isoformat()}
            streams[key] = [*current[:-1], {**last, "stop_exclusive": seam.isoformat()}, suffix]
    assert len(streams) == 222 * len(INTERVALS)
    schedule = write_once(ROOT / "schedule.json", {"days": added_pool, "union": calendars["union"]})
    protocol = read(PROTOCOL)
    expected_marks = int((end - start) / BAR)
    assert len(days) == protocol["window"]["pairlist_days"] and expected_marks == protocol["window"]["five_minute_marks"]
    repaired = write_once(ROOT / "completed_hourly_mark_transition.json", {
        "hour": before.floor("h").isoformat(), "count": len(complete_hours),
        "old_5m_rows_per_hour": 1, "extended_5m_rows_per_hour": 12,
        "original_files_retained": True, "pairs": complete_hours, "orders_enabled": False,
    })
    manifest = write_once(ROOT / "shards.json", {
        "base_manifest": baseline, "later_plan": recent, "frozen_inputs": evidence,
        "supplemental_market_files": readiness, "schedule": schedule, "files": streams,
        "market_snapshot": earlier["market_snapshot"], "engine_commit": earlier["engine_commit"],
        "first_time": start.isoformat(), "stop_exclusive": end.isoformat(), "daily_decisions": len(days),
        "five_minute_marks_expected": expected_marks, "compared_old_and_replacement_rows_by_stream": dict(all_overlap),
        "completed_hourly_mark_transition": repaired,
        "continuous_wallets_completed": 0, "orders_enabled": False,
    })
    print("Registered", len(days), "days with", len(streams), "logical feeds:", manifest["path"], flush=True)
    return manifest


def capture():
    freeze_inputs()
    _, _, _, _, _, later, venue, missing, seam, end = sources()

    def pair_inputs(pair):
        return supplemental(pair, venue["markets"][pair]["id"], later["daily_sources"][pair]["source"], seam, end)

    concurrently(missing, pair_inputs, "continuous_november_supplement")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "capture", "assemble", "all"))
    options = parser.parse_args()
    if options.mode == "freeze":
        print(freeze_inputs())
    if options.mode in ("capture", "all"):
        capture()
    if options.mode in ("assemble", "all"):
        assemble()
