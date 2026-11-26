"""Validate annual public archives and prepare the native Freqtrade feed."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save

from .annual_archives import DEST, HERE, archive_checksum


BAR = pd.Timedelta(minutes=5)
DAY = pd.Timedelta(days=1)
MAX_FUNDING_GAP = pd.Timedelta(hours=9)
OHLCV = ("open", "high", "low", "close", "volume")
AGG = dict(zip(OHLCV, ("first", "max", "min", "last", "sum"), strict=True))


def load_csv(source, with_header=False):
    path = checked(source["archive"])
    check = checked(source["checksum"])
    filename = source["url"].rsplit("/", 1)[-1]
    if archive_checksum(check.read_bytes(), filename) != source["archive"]["sha256"]:
        raise ValueError(f"Official receipt differs from content for {filename}")
    with ZipFile(path) as bundle:
        names = bundle.namelist()
        if names != [filename.removesuffix(".zip") + ".csv"]:
            raise ValueError(f"Unexpected monthly archive member for {filename}")
        return pd.read_csv(io.BytesIO(bundle.read(names[0])),
                           header=0 if with_header else None, low_memory=False)


def candles(source):
    frame = load_csv(source)
    if str(frame.iloc[0, 0]).startswith("open_time"):
        frame = frame.iloc[1:]
    frame = frame.iloc[:, :7].apply(pd.to_numeric, errors="raise")
    unit = "us" if frame.iloc[0, 0] > 10 ** 14 else "ms"
    dates = pd.to_datetime(frame[0].astype("int64"), unit=unit, utc=True)
    values = frame.iloc[:, 1:6].astype("float64")
    values.columns = OHLCV
    values.insert(0, "date", dates)
    return values.reset_index(drop=True)


def validate_bars(frame, earliest, listing_day, end, step):
    selected = frame.loc[(frame.date >= earliest) & (frame.date < end)].reset_index(drop=True)
    if selected.empty:
        raise ValueError(f"No candles after expected first boundary {earliest}")
    actual = pd.DatetimeIndex(selected.date).as_unit("ns")
    expected = pd.date_range(earliest, end, freq=step, inclusive="left").as_unit("ns")
    if actual[0] != expected[0]:
        # The listing may have started intraday. Its prior complete-day source
        # establishes which day may have a shorter first bar series.
        if actual[0].normalize() != earliest.normalize() or listing_day.normalize() != earliest.normalize():
            raise ValueError(f"Expected first {step} clock {earliest}, observed {actual[0]}")
        expected = expected[expected >= actual[0]]
    if not actual.equals(expected):
        missing = expected.difference(actual)
        raise ValueError(f"Invalid {step} sequence with {len(missing)} absent clocks, first {missing[:3].tolist()}")
    values = selected[list(OHLCV)]
    if ((~np.isfinite(values.to_numpy())).any()
            or (values[list(OHLCV[:4])] <= 0).any().any() or values.volume.lt(0).any()
            or values.high.lt(values[["open", "low", "close"]].max(axis=1)).any()
            or values.low.gt(values[["open", "high", "close"]].min(axis=1)).any()):
        raise ValueError(f"Invalid {step} OHLCV values")
    return selected


def resample(frame, step):
    bins = frame.resample(step, on="date")
    return bins.agg(AGG).loc[bins.close.count().eq(step // BAR)].reset_index()


def native_funding(sources, start, end):
    rows = pd.concat([load_csv(row, with_header=True) for row in sources], ignore_index=True)
    raw = pd.to_datetime(rows["calc_time"], unit="ms", utc=True)
    values = pd.DataFrame({"raw_date": raw, "funding_rate": rows["last_funding_rate"].astype(float)})
    values = values.loc[(values.raw_date >= start) & (values.raw_date < end)].reset_index(drop=True)
    values["date"] = values.raw_date.dt.floor("h")
    dates = values.date
    if (len(values) < 2 or not dates.is_unique or not dates.is_monotonic_increasing
            or (values.raw_date - dates).max() >= pd.Timedelta(seconds=1)
            or not np.isfinite(values.funding_rate).all()):
        raise ValueError("Incomplete or unrepresentable funding settlement clocks")
    gaps = dates.diff().dropna()
    if max(dates.iloc[0] - start, end - dates.iloc[-1], gaps.max()) > MAX_FUNDING_GAP:
        first_gap = gaps.loc[gaps.gt(MAX_FUNDING_GAP)]
        raise ValueError(f"Funding gap: {gaps.max()}, initial/final "
                         f"{dates.iloc[0] - start}/{end - dates.iloc[-1]}, "
                         f"first internal break {dates.loc[first_gap.index[:1]].tolist()}")
    return values


def source_boundary(pair, case, missing, exceptions):
    if pair not in exceptions:
        return None
    proof = exceptions[pair]
    if "first_candle_public_probe" in proof and not missing:
        raw = read(checked(proof["first_candle_public_probe"]))
        start = pd.Timestamp(proof["initial_observed_day"])
        assert len(raw["rows"]) == 1 and raw["params"]["symbol"] == case["symbol"]
        assert raw["params"]["interval"] == proof["interval"] == "1d"
        assert pd.Timestamp(raw["params"]["startTime"], unit="ms", tz="UTC") == pd.Timestamp(
            proof["metadata_boundary"]
        )
        assert pd.Timestamp(raw["params"]["endTime"] + 1, unit="ms", tz="UTC") == start + DAY
        assert pd.Timestamp(int(raw["rows"][0][0]), unit="ms", tz="UTC") == start
        return start
    if len(missing) != 1:
        return None
    identity = ("family", "interval", "month")
    if not all(missing[0][key] == proof[key] for key in identity):
        return None
    empty = read(checked(proof["empty_public_month"]))
    populated = read(checked(proof["public_month_with_first_candle"]))
    start = pd.Timestamp(proof["initial_observed_day"])
    assert empty["rows"] == [] and populated["rows"]
    assert all(raw["params"]["symbol"] == case["symbol"] and
               raw["params"]["interval"] == "1d" for raw in (empty, populated))
    assert pd.Timestamp(int(populated["rows"][0][0]), unit="ms", tz="UTC") == start
    initial = pd.Timestamp(empty["params"]["startTime"], unit="ms", tz="UTC")
    end_ms = pd.Timestamp(empty["params"]["endTime"] + 1, unit="ms", tz="UTC")
    assert initial.strftime("%Y-%m") == proof["month"] and end_ms == start
    return start


def prepare(pair, case, schedule, policy, exceptions):
    source = DEST / "pairs" / f"{case['symbol']}.json"
    if not source.is_file():
        return {"state": "waiting_for_archives"}
    index = read(source)
    assert index["pair"] == pair
    missing = [row for row in index["archives"] if "archive" not in row]
    initial_daily = source_boundary(pair, case, missing, exceptions)
    if missing and initial_daily is None:
        return {"state": "archive_gap", "missing": missing}
    records = defaultdict(list)
    for row in index["archives"]:
        if "archive" in row:
            records[(row["family"], row["interval"])].append(row)
    start, stop = map(pd.Timestamp, policy["window"])
    listing = min(pd.Timestamp(case["onboard"]), pd.Timestamp(case["daily_first"]))
    first_selectable = next(pd.Timestamp(date, tz="UTC") for date, row in schedule["days"].items()
                            if pair in row["pairs"])
    warm = policy["warmup"]
    bars_start = pd.Timestamp(warm["5m"])
    mark_start = pd.Timestamp(warm["mark_and_funding"])
    start4 = pd.Timestamp(warm["4h"])
    start_day = pd.Timestamp(warm["1d"])
    day_listing = listing.normalize()

    def price_rows(family, interval):
        return pd.concat([candles(row) for row in records[family, interval]], ignore_index=True)

    base = validate_bars(price_rows("klines", "5m"), max(bars_start, listing.floor(BAR)),
                         day_listing, stop, BAR)
    marks = validate_bars(price_rows("markPriceKlines", "5m"), max(mark_start, listing.floor(BAR)),
                          day_listing, stop, BAR)
    funding = native_funding(records["fundingRate", None], max(mark_start, marks.date.iloc[0]), stop)
    hourly = resample(marks, pd.Timedelta(hours=1))
    assert funding.date.isin(hourly.date).all(), f"Unmatched funding marks for {pair}"
    assert first_selectable >= start and first_selectable >= max(base.date.iloc[0], marks.date.iloc[0])
    daily_first = max(start_day, day_listing) if initial_daily is None else initial_daily
    daily = validate_bars(price_rows("klines", "1d"), daily_first, daily_first, stop, DAY)
    step4 = pd.Timedelta(hours=4)
    four = resample(base, step4)
    if records["klines", "4h"]:
        prefix = validate_bars(price_rows("klines", "4h"), max(start4, listing.floor(step4)),
                               day_listing, bars_start, step4)
        four = pd.concat([prefix, four], ignore_index=True)
    feeds = {
        "5m-futures": base, "5m-mark": marks, "1h-mark": hourly,
        "15m-futures": resample(base, pd.Timedelta(minutes=15)),
        "1h-futures": resample(base, pd.Timedelta(hours=1)), "4h-futures": four,
        "1d-futures": daily, "1h-funding_rate": funding[["date", "funding_rate"]],
    }
    normalized, observed = {}, {}
    stem = pair.replace("/", "_").replace(":", "_")
    for tag, frame in feeds.items():
        assert frame.date.is_monotonic_increasing and frame.date.is_unique
        frame = frame.assign(date=frame.date.dt.as_unit("us"))
        key = f"futures/{stem}-{tag}.feather"
        target = DEST / "candles" / key
        temporary = target.with_suffix(".writing")
        frame.to_feather(temporary)
        temporary.replace(target)
        normalized[key] = receipt(target)["sha256"]
        observed[tag] = {"count": len(frame), "first": frame.date.iloc[0].isoformat(),
                         "last": frame.date.iloc[-1].isoformat()}
    raw_clocks = DEST / "funding_clocks" / f"{stem}.parquet"
    funding.to_parquet(raw_clocks, index=False)
    return {"state": "normalized", "archive_index": receipt(source), "files": normalized,
            "funding_original_clock": receipt(raw_clocks), "coverage": observed,
            "first_daily_override": initial_daily.isoformat() if initial_daily is not None else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", action="append", help="Verify named contracts before full admission")
    parser.add_argument("--resume-report", type=Path, help="Reuse already verified native feeds")
    args = parser.parse_args()
    plan_path = DEST / "raw_plan.json"
    output = read(plan_path)
    policy = read(checked(output["protocol"]))
    schedule = read(checked(output["schedule"]))
    exception_path = HERE / "annual_source_exceptions.json"
    exceptions = read(exception_path)
    Path(DEST / "candles/futures").mkdir(exist_ok=True, parents=True)
    Path(DEST / "funding_clocks").mkdir(exist_ok=True)
    wanted = list(dict.fromkeys(args.pair)) if args.pair else list(output["cases"])
    records = {}
    if args.resume_report:
        previous = read(args.resume_report)
        assert previous["raw_plan"] == receipt(plan_path)
        for pair in wanted:
            row = previous["records"].get(pair, {})
            if row.get("state") != "normalized":
                continue
            checked(row["archive_index"])
            checked(row["funding_original_clock"])
            for key, digest in row["files"].items():
                checked({"path": DEST / "candles" / key, "sha256": digest})
            if pair in exceptions:
                assert row["first_daily_override"] == source_boundary(
                    pair, output["cases"][pair],
                    [entry for entry in read(checked(row["archive_index"]))["archives"]
                     if "archive" not in entry], exceptions,
                ).isoformat()
            records[pair] = row
        print("reused native inputs", len(records), flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(prepare, pair, output["cases"][pair], schedule, policy, exceptions): pair
                   for pair in wanted if pair not in records}
        for future in as_completed(pending):
            pair = pending[future]
            try:
                records[pair] = future.result()
            except (ValueError, OSError, KeyError, AssertionError) as error:
                records[pair] = {"state": "blocked", "reason": str(error)}
            if len(records) % 10 == 0 or len(records) == len(wanted):
                print("native inputs", len(records), len(wanted), flush=True)
    ready = {pair: row for pair, row in records.items() if row["state"] == "normalized"}
    all_ready = not args.pair and len(ready) == len(output["cases"])
    report = {"raw_plan": receipt(plan_path), "requested_pairs": len(wanted), "normalized_pairs": len(ready),
              "records": records, "native_inputs_admitted": all_ready, "orders_enabled": False,
              "boundary_evidence": receipt(exception_path), "normalizer": receipt(Path(__file__))}
    if args.resume_report:
        report["verified_prefix"] = receipt(args.resume_report)
    save(DEST / ("normalization_probe.json" if args.pair else "normalization.json"), report)
    print("eligible for native account", all_ready, "normalized", len(ready), flush=True)
    if all_ready:
        flat = {key: digest for record in ready.values() for key, digest in record["files"].items()}
        assert len(flat) == 8 * len(ready)
        save(DEST / "normalized-receipts.json", flat)


if __name__ == "__main__":
    main()
