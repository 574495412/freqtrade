"""Build a causal daily factor input from already admitted Binance archives."""

import csv
import io
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save
from research.crypto.nfi80_online_baseline_20261125_v1.holdout_schedule import daily_series, ranking

from .annual_archives import DEST, archive_checksum
from .inputs import milliseconds


OUTPUT = DEST / "factor_2024"
START = pd.Timestamp("2023-06-01", tz="UTC")
ARCHIVE_FIRST = pd.Timestamp("2023-03-01", tz="UTC")
END = pd.Timestamp("2025-01-01", tz="UTC")


def source_rows(raw):
    path = checked(raw["archive"])
    checksum = checked(raw["checksum"])
    filename = raw["url"].rsplit("/", 1)[-1]
    assert archive_checksum(checksum.read_bytes(), filename) == raw["archive"]["sha256"]
    with ZipFile(path) as bundle:
        names = bundle.namelist()
        assert names == [filename.removesuffix(".zip") + ".csv"]
        with bundle.open(names[0]) as data:
            yield from csv.reader(io.TextIOWrapper(data, encoding="utf-8"))


def main():
    archive_plan = DEST / "raw_plan.json"
    plan = read(archive_plan)
    OUTPUT.mkdir(exist_ok=True)
    blobs = OUTPUT / "daily"
    blobs.mkdir(exist_ok=True)
    minimum, final = map(milliseconds, (ARCHIVE_FIRST, END))
    all_sources, series, used = {}, {}, {}
    for pair, case in plan["cases"].items():
        index = DEST / "pairs" / f"{case['symbol']}.json"
        raw = read(index)
        assert raw["pair"] == pair
        rows, originals = [], []
        for item in raw["archives"]:
            if (item["family"] != "klines" or item["interval"] != "1d"
                    or not "2023-03" <= item["month"] < "2025-01"):
                continue
            assert "archive" in item
            originals.append(item["archive"])
            for row in source_rows(item):
                if row[0] == "open_time":
                    continue
                if minimum <= int(row[0]) and int(row[6]) < final:
                    rows.append(row)
        if rows:
            series[pair] = daily_series(rows)
        target = blobs / f"{case['symbol']}.json"
        save(target, rows)
        all_sources[pair] = receipt(target)
        used[pair] = {"archive_index": receipt(index), "raw_sources": originals}
    days = {}
    missing = []
    for date in pd.date_range(START, END, inclusive="left"):
        label = date.date().isoformat()
        try:
            days[label] = ranking(series, milliseconds(date))
        except AssertionError:
            missing.append(label)
    save(OUTPUT / "input.json", {
        "source_collection": receipt(archive_plan), "source_builder": receipt(Path(__file__)),
        "daily_sources": all_sources, "source_receipts": used, "days": days,
        "missing_rank_dates": missing, "start": START.isoformat(), "end_exclusive": END.isoformat(),
        "observed_symbols": len(series), "orders_enabled": False,
    })
    print("dated universe", len(days), "daily observations", len(series), "missing rank dates", len(missing))


if __name__ == "__main__":
    main()
