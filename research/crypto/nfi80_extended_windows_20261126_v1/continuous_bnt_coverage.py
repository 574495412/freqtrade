"""Recover BNT historical prices and funding for a possible carried position."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save

from .annual_archives import archive_checksum
from .annual_inputs import BAR, DAY, MAX_FUNDING_GAP, candles, native_funding, resample, validate_bars
from .continuous_seams import open_inputs
from .inputs import HERE, OUT


PAIR = "BNT/USDT:USDT"
SYMBOL = "BNTUSDT"
ROOT = "https://data.binance.vision/data/futures/um"
ENDPOINT = "https://fapi.binance.com/fapi/v1/fundingRate"
MARK_ENDPOINT = "https://fapi.binance.com/fapi/v1/markPriceKlines"
MISSING_MARK_DAY = pd.Timestamp("2026-06-29", tz="UTC")


def fetch(url, limit):
    with urlopen(url, timeout=20) as response:
        data = response.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("Empty or oversize continuation data")
    return data


def archive(key, directory):
    family, bucket, clock = key
    interval = "5m/" if family != "fundingRate" else ""
    stem = f"{SYMBOL}-{interval.removesuffix('/') or family}-{clock}"
    url = f"{ROOT}/{bucket}/{family}/{SYMBOL}/{interval}{stem}.zip"
    target = directory / f"{family}-{stem}.zip"
    check = target.with_suffix(".CHECKSUM")
    if not target.exists():
        target.write_bytes(fetch(url, 2_000_000))
    if not check.exists():
        check.write_bytes(fetch(url + ".CHECKSUM", 2048))
    source = receipt(target)
    assert archive_checksum(check.read_bytes(), stem + ".zip") == source["sha256"]
    return {"family": family, "section": bucket, "month_or_day": clock,
            "url": url, "archive": source, "checksum": receipt(check)}


def cover_mark_gap(mark, expected, refs, raw):
    gap = expected.difference(pd.DatetimeIndex(mark.date))
    strict_day = pd.date_range(MISSING_MARK_DAY, MISSING_MARK_DAY + DAY, freq=BAR, inclusive="left")
    if gap.empty:
        return mark, None
    assert gap.as_unit("ns").equals(strict_day.as_unit("ns"))
    month, = (row for row in refs if row["family"] == "markPriceKlines"
              and row["month_or_day"] == "2026-06")
    day = archive(("markPriceKlines", "daily", MISSING_MARK_DAY.date().isoformat()), raw)
    restored = candles(day)
    assert pd.DatetimeIndex(restored.date).as_unit("ns").equals(strict_day.as_unit("ns"))
    left = MISSING_MARK_DAY - BAR
    right = MISSING_MARK_DAY + DAY
    params = {"symbol": SYMBOL, "interval": "5m", "startTime": int(left.timestamp() * 1000),
              "endTime": int((right + BAR).timestamp() * 1000) - 1, "limit": 500}
    url = MARK_ENDPOINT + "?" + urlencode(params)
    evidence = raw / "mark_public_overlap_response.json"
    evidence.write_bytes(fetch(url, 1_000_000))
    packet = pd.DataFrame(read(evidence))
    assert len(packet) == len(restored) + 2
    expected_marks = packet[[1, 2, 3, 4]].astype(float).set_axis(["open", "high", "low", "close"], axis=1)
    expected_marks.index = pd.DatetimeIndex(pd.to_datetime(packet[0], unit="ms", utc=True)).as_unit("ns")
    merged = pd.concat([mark, restored], ignore_index=True).sort_values("date").reset_index(drop=True)
    values = merged.set_index("date").loc[left:right, list(expected_marks.columns)]
    values.index = values.index.as_unit("ns")
    pd.testing.assert_frame_equal(values, expected_marks, check_exact=True, check_freq=False, check_names=False)
    return merged, {"source_month": month, "source_daily_archive": day, "original_missing_rows": len(gap),
                    "official_api_overlap": receipt(evidence) | {"url": url},
                    "independently_equal_mark_rows": len(packet), "original_month_retained": True}


def funding_rates(archive_data, start, stop, root):
    nov = pd.Timestamp("2026-11-01", tz="UTC")
    historical = native_funding(archive_data, start, nov)
    params = {"symbol": SYMBOL, "startTime": int((nov - DAY).timestamp() * 1000),
              "endTime": int(stop.timestamp() * 1000) - 1, "limit": 1000}
    url = ENDPOINT + "?" + urlencode(params)
    source = root / "funding_public_response.json"
    source.write_bytes(fetch(url, 1_000_000))
    data = pd.DataFrame(read(source))
    assert data.symbol.eq(SYMBOL).all() and 0 < len(data) < params["limit"]
    values = pd.DataFrame({"raw_date": pd.to_datetime(data.fundingTime, unit="ms", utc=True),
                           "funding_rate": data.fundingRate.astype(float)})
    warm = historical.loc[historical.raw_date >= nov - DAY, ["raw_date", "funding_rate"]].reset_index(drop=True)
    api_overlap = values.loc[values.raw_date < nov].reset_index(drop=True)
    assert not warm.empty
    pd.testing.assert_frame_equal(warm, api_overlap, check_exact=True)
    values = values.loc[values.raw_date >= nov].copy()
    values["date"] = values.raw_date.dt.floor("h")
    full = pd.concat([historical, values], ignore_index=True)
    assert full.date.is_monotonic_increasing and full.date.is_unique and np.isfinite(full.funding_rate).all()
    assert (full.raw_date - full.date).max() < pd.Timedelta(seconds=1)
    assert full.date.diff().dropna().max() <= MAX_FUNDING_GAP
    assert full.date.min() - start <= MAX_FUNDING_GAP and stop - full.date.max() <= MAX_FUNDING_GAP
    assert full.raw_date.max() < stop
    return full, receipt(source) | {"url": url, "exact_overlap_settlements": len(warm)}


def main():
    plans, inputs = open_inputs()
    recent = inputs["2026"]
    earlier = inputs["2025"]
    assert PAIR in earlier["pairs"] and PAIR not in recent["pairs"]
    start, stop = map(pd.Timestamp, (recent["start"], recent["end"]))
    assert str(start.date()) == "2026-01-01" and str(stop.date()) == "2026-11-09"
    source = OUT / "continuous_bnt_coverage_20261126"
    raw = source / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    weeks = pd.date_range("2026-11-01", stop.date(), freq="D")
    months = [f"2026-{month:02d}" for month in range(1, 11)]
    wanted = [(family, "monthly", m) for m in months
              for family in ("klines", "markPriceKlines", "fundingRate")]
    wanted += [(family, "daily", d.date().isoformat()) for d in weeks
               for family in ("klines", "markPriceKlines")]
    with ThreadPoolExecutor(max_workers=6) as workers:
        refs = list(workers.map(lambda key: archive(key, raw), wanted))
    assert len(refs) == 48

    def prices(family):
        return pd.concat([candles(record) for record in refs if record["family"] == family], ignore_index=True)

    bar = validate_bars(prices("klines"), start, start, stop, BAR)
    raw_marks, repair = cover_mark_gap(
        prices("markPriceKlines"), pd.date_range(start, stop, freq=BAR, inclusive="left"), refs, raw)
    mark = validate_bars(raw_marks, start, start, stop, BAR)
    funding, rest = funding_rates([row for row in refs if row["family"] == "fundingRate"],
                                  start, stop, raw)
    hourly = pd.Timedelta(hours=1)
    streams = {"5m-futures": bar, "5m-mark": mark, "1h-mark": resample(mark, hourly),
               "15m-futures": resample(bar, 3 * BAR), "1h-futures": resample(bar, hourly),
               "4h-futures": resample(bar, 4 * hourly), "1d-futures": resample(bar, DAY),
               "1h-funding_rate": funding[["date", "funding_rate"]]}
    assert funding.date.isin(streams["1h-mark"].date).all()
    parent = source / "candles/futures"
    parent.mkdir(parents=True, exist_ok=False)
    receipts = {}
    key = PAIR.replace("/", "_").replace(":", "_")
    for name, data in streams.items():
        assert data.date.is_monotonic_increasing and data.date.is_unique
        path = parent / f"{key}-{name}.feather"
        data.assign(date=data.date.dt.as_unit("us")).to_feather(path)
        receipts[name] = receipt(path) | {"rows": len(data), "first": data.date.min().isoformat(),
                                         "last": data.date.max().isoformat()}
    funding_source = source / "raw_funding_timestamps.parquet"
    funding.to_parquet(funding_source, index=False)
    save(HERE / "continuous_bnt_coverage.json", {
        "input_plans": plans, "pair": PAIR, "archives": refs, "funding_rest": rest, "mark_coverage_repair": repair,
        "funding_raw_clocks": receipt(funding_source), "files": receipts,
        "window": [start.isoformat(), stop.isoformat()], "recent_pairlist_contains_pair": False,
        "role": "Input continuation for carried positions; does not change the dynamic top-80 schedule",
        "source": receipt(Path(__file__)), "continuous_wallets_completed": 0, "orders_enabled": False,
    })
    print(len(bar), len(mark), len(funding), flush=True)


if __name__ == "__main__":
    main()
