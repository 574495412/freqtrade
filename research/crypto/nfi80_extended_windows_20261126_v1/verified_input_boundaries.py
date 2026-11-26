"""Extend annual normalization with verified funding launches and daily API repairs."""

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .annual_inputs import BAR, DAY, OHLCV, candles, load_csv, native_funding, resample, source_boundary, validate_bars


HOUR = pd.Timedelta(hours=1)


def first_settlement(proof, missing, symbol):
    assert len(missing) == 1
    gap, = missing
    assert gap["family"] == "fundingRate" and gap["interval"] is None
    assert gap["month"] == proof["missing_funding_month"] and gap["state"] == "missing_checksum"
    query = read(checked(proof["public_funding_probe"]))
    assert query["endpoint"] == "/fapi/v1/fundingRate"
    empty, observed = query["interval_seed"]
    a, b = empty["params"], observed["params"]
    start = pd.Timestamp(gap["month"] + "-01", tz="UTC")
    end = start + pd.DateOffset(months=1)
    assert a["symbol"] == b["symbol"] == symbol and a["limit"] >= 2 and b["limit"] >= 2
    assert pd.Timestamp(a["startTime"], unit="ms", tz="UTC") == start
    assert pd.Timestamp(a["endTime"] + 1, unit="ms", tz="UTC") == end
    assert a["endTime"] + 1 == b["startTime"] and empty["response"]["result"] == []
    rows = observed["response"]["result"]
    assert 0 < len(rows) < b["limit"]
    clocks = [row["fundingTime"] for row in rows]
    assert clocks == sorted(set(clocks)) and all(row["symbol"] == symbol for row in rows)
    assert b["startTime"] <= clocks[0] <= clocks[-1] <= b["endTime"]
    return pd.Timestamp(clocks[0], unit="ms", tz="UTC")


def repair_daily(frame, begin, stop, symbol, reference):
    query = read(checked(reference))
    assert query["endpoint"] == "/fapi/v1/klines"
    args = query["params"]
    assert args["symbol"] == symbol and args["interval"] == "1d"
    rows = query["rows"]
    assert 0 < len(rows) < args["limit"] and all(len(row) >= 7 for row in rows)
    clocks = pd.to_datetime([int(row[0]) for row in rows], unit="ms", utc=True)
    ends = pd.to_datetime([int(row[6]) for row in rows], unit="ms", utc=True)
    assert clocks.is_unique and clocks.is_monotonic_increasing
    assert (clocks == clocks.normalize()).all() and (ends == clocks + DAY - pd.Timedelta(milliseconds=1)).all()
    assert args["startTime"] <= int(rows[0][0]) <= int(rows[-1][0]) <= args["endTime"]
    expected = pd.date_range(begin, stop, inclusive="left")
    missing = expected.difference(pd.DatetimeIndex(frame.date))
    assert missing.equals(pd.DatetimeIndex(pd.to_datetime(query["missing"], utc=True)))
    public = pd.DataFrame([[float(v) for v in row[1:6]] for row in rows], index=clocks, columns=OHLCV)
    assert missing.isin(public.index).all()
    archived = frame.set_index("date")
    same = public.index.intersection(archived.index)
    assert len(same) >= 2
    np.testing.assert_allclose(archived.loc[same, list(OHLCV)], public.loc[same, list(OHLCV)],
                               rtol=1e-12, atol=1e-12)
    combined = pd.concat([archived, public.loc[missing]]).sort_index()
    assert combined.index.is_unique
    return combined.rename_axis("date").reset_index(), {
        "source": reference, "added_rows": len(missing), "overlap_rows": len(same),
        "repaired_clocks": [clock.isoformat() for clock in missing],
    }


def prepare(destination, pair, case, schedule, policy, proof):
    source = destination / "pairs" / f"{case['symbol']}.json"
    index = read(source)
    assert index["pair"] == pair
    rows = index["archives"]
    missing = [row for row in rows if "archive" not in row]
    funding_override = "public_funding_probe" in proof
    if funding_override:
        first = first_settlement(proof, missing, case["symbol"])
    else:
        assert not missing
    first_selectable = next(pd.Timestamp(day, tz="UTC") for day, info in schedule["days"].items()
                            if pair in info["pairs"])
    start, stop = map(pd.Timestamp, policy["window"])
    listing = min(pd.Timestamp(case["onboard"]), pd.Timestamp(case["daily_first"]))
    day_listing = listing.normalize()
    assert first_selectable >= start
    warm = {key: pd.Timestamp(value) for key, value in policy["warmup"].items()}
    sources = defaultdict(list)
    for row in rows:
        if "archive" in row:
            sources[row["family"], row["interval"]].append(row)
    def raw_price(family, interval):
        return pd.concat([candles(row) for row in sources[family, interval]], ignore_index=True)

    def price(family, interval, beginning, step, end=stop):
        return validate_bars(raw_price(family, interval), beginning, day_listing, end, step)

    base = price("klines", "5m", max(warm["5m"], listing.floor(BAR)), BAR)
    mark = price("markPriceKlines", "5m", max(warm["mark_and_funding"], listing.floor(BAR)), BAR)
    daily_start = max(warm["1d"], day_listing)
    verified_first = source_boundary(pair, case, [], {pair: proof}) if "first_candle_public_probe" in proof else None
    if verified_first is not None:
        daily_start = verified_first
    daily_input = raw_price("klines", "1d")
    daily_repair = None
    if "daily_gap_public_probe" in proof:
        daily_input, daily_repair = repair_daily(daily_input, daily_start, stop, case["symbol"],
                                               proof["daily_gap_public_probe"])
    daily = validate_bars(daily_input, daily_start, daily_start, stop, DAY)
    funding_sources = sources["fundingRate", None]
    funding_start = max(warm["mark_and_funding"], mark.date.iloc[0])
    funding_boundary = None
    if funding_override:
        first_archive_row = load_csv(funding_sources[0], with_header=True).iloc[0]
        assert pd.Timestamp(int(first_archive_row["calc_time"]), unit="ms", tz="UTC") == first
        assert max(listing, funding_start) <= first < first_selectable
        funding_start = first
        funding_boundary = {"earliest_public_funding": first.isoformat(), "first_selectable": first_selectable.isoformat(),
                            "evidence": proof["public_funding_probe"]}
    funding = native_funding(funding_sources, funding_start, stop)
    if funding_override:
        assert funding.raw_date.iloc[0] == first
    hourly_mark = resample(mark, HOUR)
    assert funding.date.isin(hourly_mark.date).all()
    assert first_selectable >= max(base.date.iloc[0], mark.date.iloc[0])
    four = resample(base, 4 * HOUR)
    if sources["klines", "4h"]:
        prefix = price("klines", "4h", max(warm["4h"], listing.floor(4 * HOUR)), 4 * HOUR, warm["5m"])
        four = pd.concat([prefix, four], ignore_index=True)
    streams = {
        "5m-futures": base, "5m-mark": mark, "1h-mark": hourly_mark,
        "15m-futures": resample(base, 3 * BAR), "1h-futures": resample(base, HOUR),
        "4h-futures": four, "1d-futures": daily,
        "1h-funding_rate": funding[["date", "funding_rate"]],
    }
    files, coverage = {}, {}
    stem = pair.replace("/", "_").replace(":", "_")
    for name, frame in streams.items():
        assert frame.date.is_monotonic_increasing and frame.date.is_unique
        key = f"futures/{stem}-{name}.feather"
        path = destination / "candles" / key
        temporary = path.with_suffix(".writing")
        frame.assign(date=frame.date.dt.as_unit("us")).to_feather(temporary)
        temporary.replace(path)
        files[key] = receipt(path)["sha256"]
        coverage[name] = {"count": len(frame), "first": frame.date.iloc[0].isoformat(),
                          "last": frame.date.iloc[-1].isoformat()}
    clock_file = destination / "funding_clocks" / f"{stem}.parquet"
    funding.to_parquet(clock_file, index=False)
    return {
        "state": "normalized", "archive_index": receipt(source), "files": files,
        "funding_original_clock": receipt(clock_file), "coverage": coverage,
        "funding_boundary": funding_boundary, "daily_repair": daily_repair,
        "first_daily_override": verified_first.isoformat() if verified_first is not None else None,
        "boundary_adapter": receipt(Path(__file__)),
    }
