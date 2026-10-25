"""Prepare checksummed spot prices beside existing immutable perpetual evidence."""

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from freqtrade.research.data import complete_hours, read, read_archive, save, sha


def spot_archive(symbol, period, output):
    name = f"{symbol}-1h-{period}.zip"
    url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1h/{name}"
    path = output / "raw" / symbol / name
    checksum = path.with_suffix(".zip.CHECKSUM")
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            if not checksum.exists():
                response = requests.get(url + ".CHECKSUM", timeout=30)
                response.raise_for_status()
                checksum.write_bytes(response.content)
            expected = checksum.read_text().split()[0]
            if len(expected) != 64:
                raise ValueError("Invalid official checksum")
            if not path.exists():
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                if hashlib.sha256(response.content).hexdigest() != expected:
                    raise ValueError("Spot archive checksum mismatch")
                path.write_bytes(response.content)
            if sha(path) != expected:
                raise ValueError("Cached spot archive changed")
            return {
                "symbol": symbol,
                "period": period,
                "url": url,
                "path": str(path),
                "sha256": expected,
            }
        except (requests.RequestException, OSError, ValueError):
            if attempt == 2:
                raise
    raise RuntimeError("Spot archive did not complete")


def prepare(plan_path, output):
    plan = read(plan_path)
    output.mkdir(parents=True, exist_ok=False)
    save(output / "plan.json", plan)
    parent = Path(plan["parent_dataset"])
    if sha(parent / "receipts.json") != plan["parent_receipts_sha256"]:
        raise ValueError("Parent data receipts changed")
    expected = read(parent / "receipts.json")
    months = pd.period_range(
        plan["spot_data_start"], pd.Timestamp(plan["end"]) - pd.Timedelta(days=1), freq="M"
    )
    jobs = [(symbol, str(month), output) for symbol in plan["symbols"] for month in months]
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda job: spot_archive(*job), jobs))
    save(output / "spot_sources.json", records)
    parent_sources, coverage = [], {}
    for symbol in plan["symbols"]:
        spot = complete_hours(
            pd.concat([read_archive(Path(r["path"])) for r in records if r["symbol"] == symbol]),
            plan["spot_data_start"],
            plan["end"],
        )
        pair = symbol.removesuffix("USDT") + "_USDT_USDT"
        sources = {
            "perpetual": parent / "candles/futures" / f"{pair}-1h-futures.feather",
            "mark": parent / "candles/futures" / f"{pair}-1h-mark.feather",
            "funding": parent / f"{symbol}_funding_events.parquet",
        }
        for kind, path in sources.items():
            relative = str(path.relative_to(parent))
            if sha(path) != expected[relative]:
                raise ValueError("Parent data file changed")
            parent_sources.append({"kind": kind, "path": str(path), "sha256": expected[relative]})
        perpetual = complete_hours(
            pd.read_feather(sources["perpetual"]), plan["spot_data_start"], plan["end"]
        )
        mark = complete_hours(
            pd.read_feather(sources["mark"]), plan["spot_data_start"], plan["end"]
        )
        panel = spot.rename(columns={c: "spot_" + c for c in spot.columns if c != "date"})
        panel = panel.merge(
            perpetual.rename(
                columns={c: "perpetual_" + c for c in perpetual.columns if c != "date"}
            ),
            on="date",
            validate="one_to_one",
        )
        panel = panel.merge(
            mark[["date", "open"]].rename(columns={"open": "mark_open"}),
            on="date",
            validate="one_to_one",
        )
        panel.to_parquet(output / f"{symbol}_prices.parquet", index=False)
        funding = pd.read_parquet(sources["funding"])
        if funding.calc_time.duplicated().any() or not funding.calc_time.is_monotonic_increasing:
            raise ValueError("Funding event clock is duplicated or unsorted")
        funding.to_parquet(output / f"{symbol}_funding.parquet", index=False)
        coverage[symbol] = {
            "complete_hourly_prices": len(panel),
            "actual_funding_events": len(funding),
        }
    save(output / "parent_sources.json", parent_sources)
    save(
        output / "result.json",
        {
            "plan_sha256": sha(plan_path),
            "coverage": coverage,
            "raw_spot_archives": len(records),
            "orders_enabled": False,
        },
    )
    save(
        output / "receipts.json",
        {str(p.relative_to(output)): sha(p) for p in sorted(output.rglob("*")) if p.is_file()},
    )
    return coverage


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(prepare(args.plan.absolute(), args.output.absolute()), flush=True)
