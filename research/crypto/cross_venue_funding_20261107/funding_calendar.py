"""Cross-check public Binance settlement intervals and isolate missing-event exposure."""

import csv
import hashlib
import io
from itertools import pairwise
from pathlib import Path
from statistics import mean
from urllib.request import urlopen
from zipfile import ZipFile

import pandas as pd

from research.crypto.cross_venue_funding_20261107 import study, verify
from research.crypto.nfi_strength_allocation_20261107.audit import read
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


ARCHIVE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
MONTHS = ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10")
HOUR_MS = 3_600_000


def load_archive(coin, month, output):
    stem = f"{coin}USDT"
    name = f"{stem}-fundingRate-{month}.zip"
    url = f"{ARCHIVE}/{stem}/{name}"
    with urlopen(url, timeout=15) as response:  # noqa: S310
        raw = response.read()
    with urlopen(url + ".CHECKSUM", timeout=15) as response:  # noqa: S310
        checksum = response.read()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != checksum.decode().split()[0]:
        raise ValueError("Official funding archive checksum differs")
    path = output / name
    path.write_bytes(raw)
    check_path = output / (name + ".CHECKSUM")
    check_path.write_bytes(checksum)
    with ZipFile(io.BytesIO(raw)) as archive:
        (file,) = archive.namelist()
        with archive.open(file) as stream:
            rows = list(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8")))
    if any(
        pd.to_datetime(int(r["calc_time"]), unit="ms", utc=True).strftime("%Y-%m") != month
        for r in rows
    ):
        raise ValueError("Funding timestamp outside the named archive month")
    return rows, {"url": url, "file": study.record(path), "checksum": study.record(check_path)}


def audit_coin(coin, raw, output):
    periods = {}
    archive_rates = {}
    receipts = []
    for month in MONTHS:
        rows, receipt = load_archive(coin, month, output)
        receipts.append(receipt)
        for row in rows:
            key = int(row["calc_time"])
            period = int(row["funding_interval_hours"])
            if key in periods or period not in (1, 2, 4, 8):
                raise ValueError("Invalid funding frequency or duplicate event")
            periods[key] = period
            archive_rates[key] = row["last_funding_rate"]
    rest = verify.catalog(raw, f"bn_fund_{coin}USDT_")
    for event in rest:
        t = event["fundingTime"]
        if t >= study.ms(study.END):
            continue
        if t not in archive_rates or verify.D(archive_rates[t]) != verify.D(event["fundingRate"]):
            raise ValueError("Binance REST and monthly archive disagree")
    required = {k for k in archive_rates if study.ms(study.WARM) <= k < study.ms(study.END)}
    if required != {r["fundingTime"] for r in rest if r["fundingTime"] < study.ms(study.END)}:
        raise ValueError("One source omits settlement rows present in the other")
    gaps = []
    changes = []
    for a, b in pairwise(rest):
        t0, t1 = (x["fundingTime"] for x in (a, b))
        interval = round((t1 - t0) / HOUR_MS)
        if t1 not in periods:
            continue
        duration = periods[t1]
        if interval != duration:
            changes.append({"time_ms": t1, "elapsed_hours": interval, "archive_hours": duration})
        if interval > duration:
            first = ((t1 + HOUR_MS // 2) // HOUR_MS) * HOUR_MS - duration * HOUR_MS
            gaps.extend(t for t in range(first, t0, -duration * HOUR_MS))
    return periods, {
        "monthly_receipts": receipts,
        "matched_rest_records": len(required),
        "after_monthly_endpoint": len(rest) - len(required),
        "interval_corrections": changes,
        "unobserved_scheduled_events_ms": sorted(gaps),
    }


def market_frames(raw, coin, periods):
    def binance_price(symbol):
        rows = verify.catalog(raw, f"bn_price_{symbol}_")
        return study.price_frame([(r[0], r[1], r[2], r[3], r[4]) for r in rows])

    data = verify.sources(raw, coin)
    bn = study.funding_frame(data[3], False)
    # Explicit archive intervals supersede elapsed-gap normalization for known events.
    hours = bn.time.map(periods)
    bn["daily"] = (24 * bn.rate / hours).where(hours.notna(), bn.daily)
    # The audit helper excludes the first event because its price proxy is unknown.
    first = raw[f"hl_fund_{coin}_{study.ms(study.WARM)}"][0]
    hl = study.funding_frame([(first["time"], first["fundingRate"], float("nan")), *data[4]], True)
    hc = raw[f"hl_price_{coin}"]
    prices = study.price_frame([(r["t"], r["o"], r["h"], r["l"], r["c"]) for r in hc])
    return binance_price(coin + "USDT"), prices, bn, hl, binance_price("USDCUSDT")


def main():
    r = read(study.RESULT)
    raw = {k: read(checked(v["source"])) for k, v in read(checked(r["inputs"])).items()}
    output = study.OUT / "binance_monthly_funding"
    output.mkdir()
    markets = {}
    coverage = {}
    for coin in study.COINS:
        periods, coverage[coin] = audit_coin(coin, raw, output)
        markets[coin] = market_frames(raw, coin, periods)
    adjusted = {}
    for key, item in r["windows"].items():
        day, label = key.split("_")
        a = pd.Timestamp(day, tz="UTC")
        b = study.SPLIT if a == study.BEGIN else study.END
        cost = int(label[:-3])
        rows = [
            {**row, "coin": coin}
            for coin, data in markets.items()
            for row in study.evaluate_market(*data, a, b, cost)
        ]
        original = read(checked(item["ledger"]))
        if len(rows) != len(original):
            raise ValueError("Funding interval correction changed the episode count")
        for x, y in zip(rows, original, strict=True):
            if {k: v for k, v in x.items() if k != "signal_diff_per_day"} != {
                k: v for k, v in y.items() if k != "signal_diff_per_day"
            }:
                raise ValueError("Funding interval correction changed episode clocks or economics")
        natural = [x for x in rows if x["reason"] != "boundary"]
        quarantined = []
        admitted = []
        for row in natural:
            start = study.ms(pd.Timestamp(row["open"]))
            end = study.ms(pd.Timestamp(row["close"]))
            gaps = coverage[row["coin"]]["unobserved_scheduled_events_ms"]
            # A prospective episode enters after all near-boundary settlement events.
            target = quarantined if any(start < t < end for t in gaps) else admitted
            target.append(row)
        adjusted[key] = {
            "interval_normalization_preserved_episode_clocks_and_net": True,
            "quarantined": quarantined,
            "admitted_natural_episodes": len(admitted),
            "admitted_mean_net": mean(x["net"] for x in admitted),
        }
    save(
        study.HERE / "funding_calendar.json",
        {
            "result_sha256": sha(study.RESULT),
            "source_sha256": sha(Path(__file__)),
            "coverage": coverage,
            "windows": adjusted,
        },
    )


if __name__ == "__main__":
    main()
