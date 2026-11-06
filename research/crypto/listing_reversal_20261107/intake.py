"""Admit checksummed launch, mark and actual funding data before scoring A05."""

import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests

from freqtrade.research.data import read, read_archive, save, sha
from research.crypto.listing_reversal_20261107.catalog import HERE, OUT, PLAN, fetch, now


ADMISSION = HERE / "admission_registration.json"
VISION = "https://data.binance.vision/data/futures/um"
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
STEP = pd.Timedelta(minutes=5)
STABLE = {"BUSD", "DAI", "FDUSD", "PYUSD", "TUSD", "USD1", "USDC", "USDE", "USDP", "USDS"}


def register():
    if ADMISSION.exists() or (OUT / "data").exists():
        raise FileExistsError("Launch intake already registered")
    catalog_path = OUT / "catalog.json"
    catalog = read(catalog_path)
    plan = read(PLAN)
    if catalog["registration_sha256"] != sha(PLAN):
        raise ValueError("Catalog provenance differs")
    included = []
    excluded = {}
    for row in catalog["symbols"]:
        day = row.get("first_archive_day")
        if (
            not day
            or not plan["first_archive_day_start"] <= day < plan["first_archive_day_end_exclusive"]
        ):
            continue
        symbol = row["symbol"]
        if symbol.removesuffix("USDT") in STABLE:
            excluded[symbol] = "stablecoin base"
        elif row.get("contract_type") not in (None, "PERPETUAL"):
            excluded[symbol] = "non-crypto-perpetual contract type"
        elif not row.get("collector_since") or row["collector_since"][:10] != day:
            excluded[symbol] = "collector calendar disagreement"
        elif row.get("onboard_utc") and row["onboard_utc"][:10] != day:
            excluded[symbol] = "exchange onboard calendar disagreement or symbol reuse"
        else:
            included.append(row)
    if len(included) > 400:
        raise ValueError("Exceeds one-request-per-symbol funding rate budget")
    save(
        ADMISSION,
        {
            "registered_utc": now(),
            "catalog_sha256": sha(catalog_path),
            "code_sha256": sha(Path(__file__)),
            "raw_reader_sha256": sha(Path("freqtrade/research/data.py")),
            "included": included,
            "excluded": excluded,
            "required_hours": 120,
            "funding_policy": (
                "One public exact historical funding request per admitted symbol; preserve "
                "settlement mark and raw millisecond time. Require at least one event per "
                "nine elapsed hours from launch through the coverage end; no zero fill."
            ),
            "clock_policy": (
                "First positive-volume Binance 5m bar from the earliest daily ZIP. A published "
                "onboard timestamp, when available, must fall in this bar or within one bar of "
                "its start. Full 120h price and mark calendar is required. Missing portions "
                "of monthly K-lines use publisher daily archives; no price fabrication."
            ),
            "financial_accounts": 0,
        },
    )


def archive(symbol, family, period, path, receipts):
    filename = f"{symbol}-5m-{period}.zip"
    grain = "monthly" if len(period) == 7 else "daily"
    segment = f"{grain}/{family}/{quote(symbol)}/5m/{quote(filename)}"
    url = f"{VISION}/{segment}"
    local = path / f"{family}-{period}.zip"
    if local.exists():
        return read_archive(local)
    try:
        response = requests.get(url + ".CHECKSUM", timeout=(8, 18))
        if response.status_code == 404:
            receipts.append({"url": url, "state": "NOT_PUBLISHED"})
            return None
        response.raise_for_status()
        checksum = response.content
        body = fetch(url)
    except requests.RequestException as error:
        raise ValueError(f"HTTP archive failure: {family}/{period}: {error}") from error
    local.write_bytes(body)
    local.with_suffix(".zip.CHECKSUM").write_bytes(checksum)
    if sha(local) != checksum.decode().split()[0]:
        raise ValueError(f"Publisher SHA mismatch: {family}/{period}")
    with ZipFile(io.BytesIO(body)) as zipped:
        if sum(item.file_size for item in zipped.infolist()) > 8_000_000:
            raise ValueError("Unexpected archive expansion")
    receipts.append({"url": url, "state": "VERIFIED", "file": local.name, "sha256": sha(local)})
    return read_archive(local)


def complete_grid(symbol, family, grid, path, receipts, first=None):
    start, end = grid[0], grid[-1]
    monthly = [
        archive(symbol, family, month, path, receipts)
        for month in sorted(set(grid.strftime("%Y-%m")))
    ]
    chunks = [x for x in monthly if x is not None]
    data = pd.concat(chunks).set_index("date") if chunks else pd.DataFrame(index=grid[:0])
    if not data.empty:
        data.index = data.index.as_unit("us")
    missing_days = grid.difference(data.index).normalize().unique()
    for day in missing_days:
        period = day.date().isoformat()
        additional = (
            first
            if first is not None and period == start.date().isoformat()
            else archive(symbol, family, period, path, receipts)
        )
        if additional is not None:
            extra = additional.set_index("date")
            extra.index = extra.index.as_unit("us")
            data = pd.concat([data, extra.loc[~extra.index.isin(data.index)]])
    data = data.sort_index().loc[start:end]
    if not data.index.equals(grid):
        raise ValueError(f"Incomplete {family} window: {len(data)} of {len(grid)} rows")
    return data


def funding_events(symbol, start, end, path):
    start_ms, end_ms = int(start.timestamp()) * 1000, int(end.timestamp()) * 1000
    # Single call per symbol. Raw fundingTime remains the authoritative model clock.
    response = requests.get(
        FUNDING_URL,
        params={"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
        timeout=(8, 18),
    )
    receipt = path / "funding.json"
    receipt.write_bytes(response.content)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not 0 < len(rows) < 1000:
        raise ValueError("Empty, invalid or truncated funding")
    if any(x["symbol"] != symbol for x in rows):
        raise ValueError("Funding symbol mismatch")
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime([x["fundingTime"] for x in rows], unit="ms", utc=True).as_unit(
                "us"
            ),
            "rate": [float(x["fundingRate"]) for x in rows],
            "mark": [float(x["markPrice"]) for x in rows],
        }
    ).set_index("date")
    clocks = frame.index
    if (
        not clocks.is_monotonic_increasing
        or clocks.has_duplicates
        or clocks[0] < start
        or clocks[-1] > end
        or not np.isfinite(frame.to_numpy()).all()
        or (frame.mark <= 0).any()
    ):
        raise ValueError("Invalid funding cash inputs")
    if max(clocks[0] - start, end - clocks[-1], clocks.to_series().diff().max()) > pd.Timedelta(
        hours=9
    ):
        raise ValueError("Funding coverage gap over nine hours")
    return frame


def admit(row, hours):
    symbol = row["symbol"]
    path = OUT / "data" / symbol
    path.mkdir()
    receipts = []
    result = {"symbol": symbol, "archive_receipts": receipts}
    try:
        first = archive(symbol, "klines", row["first_archive_day"], path, receipts)
        if first is None or first.loc[first.volume > 0].empty:
            raise ValueError("No positive-volume launch bar in first published day")
        launch = first.loc[first.volume > 0, "date"].iloc[0]
        if row.get("onboard_utc") and abs(launch - pd.Timestamp(row["onboard_utc"])) > STEP:
            raise ValueError("First traded bar disagrees with published onboard clock")
        if str(launch.date()) != row["first_archive_day"]:
            raise ValueError("First archive crosses the registered UTC launch day")
        result["launch_utc"] = launch.isoformat()
        end = launch + pd.Timedelta(hours=hours)
        grid = pd.date_range(launch, end, freq="5min", inclusive="left").as_unit("us")
        trade = complete_grid(symbol, "klines", grid, path, receipts, first)
        mark = complete_grid(symbol, "markPriceKlines", grid, path, receipts)
        funding = funding_events(symbol, launch, end, path)
        for kind, frame in (("trade", trade), ("mark", mark), ("funding", funding)):
            file = path / f"{kind}.parquet"
            frame.to_parquet(file)
            result[f"{kind}_sha256"] = sha(file)
        result.update(state="ADMITTED", funding_response_sha256=sha(path / "funding.json"))
    except (OSError, ValueError, KeyError, requests.RequestException) as error:
        result.update(state="UNKNOWN", reason=str(error))
    return result


def capture():
    plan = read(ADMISSION)
    if (
        sha(Path(__file__)) != plan["code_sha256"]
        or sha(OUT / "catalog.json") != plan["catalog_sha256"]
    ):
        raise ValueError("Registered intake source changed")
    (OUT / "data").mkdir()
    with ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(lambda row: admit(row, plan["required_hours"]), plan["included"]))
    result = {
        "registration_sha256": sha(ADMISSION),
        "received_utc": now(),
        "rows": rows,
        "admitted": sum(row["state"] == "ADMITTED" for row in rows),
        "unknown": sum(row["state"] == "UNKNOWN" for row in rows),
    }
    save(OUT / "admission.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}), flush=True)


if __name__ == "__main__":
    {"register": register, "capture": capture}[sys.argv[1]]()
