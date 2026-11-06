"""Freeze an archive-based USD-M lifecycle universe including inactive symbols."""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import requests

from freqtrade.research.data import read, save, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/listing_reversal_20261107_v001"
PLAN = HERE / "catalog_registration.json"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
PREFIX = "data/futures/um/daily/klines/"
SOURCES = {
    "exchange": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "collector": "https://api.tardis.dev/v1/exchanges/binance-futures",
}
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def now():
    return datetime.now(UTC).isoformat()


def register():
    if PLAN.exists() or OUT.exists():
        raise FileExistsError("Catalog already registered")
    save(
        PLAN,
        {
            "registered_utc": now(),
            "code_sha256": sha(Path(__file__)),
            "origin": S3,
            "prefix": PREFIX,
            "metadata_sources": SOURCES,
            "first_archive_day_start": "2025-01-01",
            "first_archive_day_end_exclusive": "2026-09-01",
            "workers": 12,
            "policy": (
                "Enumerate all Binance USD-M daily archive prefixes, retain all USDT-suffix "
                "symbols without a current trading-status filter, and record first 5m day. "
                "Binance perpetual onboard dates and collector starts supply separate lifecycle "
                "checks. Do not treat a first archive key alone as a verified launch or exclude "
                "delisted contracts. No economic results are read in this stage."
            ),
        },
    )


def fetch(url, params=None):
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=(8, 20))
            response.raise_for_status()
            return response.content
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise RuntimeError("Fetch did not finish")


def list_page(params, path):
    raw = fetch(S3, {"list-type": "2", **params})
    path.write_bytes(raw)
    page = ElementTree.fromstring(raw)  # noqa: S314 -- bounded AWS ListObjects response
    if page.findtext("s3:Name", namespaces=NS) != "data.binance.vision":
        raise ValueError("Unexpected S3 bucket")
    return page


def first_day(symbol, directory):
    prefix = f"{PREFIX}{symbol}/5m/"
    path = directory / f"{symbol}.xml"
    try:
        page = list_page({"prefix": prefix, "max-keys": 4}, path)
        names = [n.text for n in page.findall("s3:Contents/s3:Key", NS)]
        dates = [
            key[len(prefix + symbol + "-5m-") : -4]
            for key in names
            if key.startswith(prefix + symbol + "-5m-") and key.endswith(".zip")
        ]
        if any(
            len(date) != 10 or datetime.fromisoformat(date).date().isoformat() != date
            for date in dates
        ):
            raise ValueError("Invalid archive date")
        return {
            "symbol": symbol,
            "first_archive_day": min(dates) if dates else None,
            "receipt_sha256": sha(path),
        }
    except (OSError, ValueError, requests.RequestException, ElementTree.ParseError) as error:
        return {"symbol": symbol, "scan_error": type(error).__name__, "detail": str(error)}


def capture():
    plan = read(PLAN)
    if sha(Path(__file__)) != plan["code_sha256"]:
        raise ValueError("Registered catalog scanner changed")
    OUT.mkdir()
    raw = OUT / "catalog"
    raw.mkdir()
    first = raw / "first"
    first.mkdir()
    meta = {}
    for source, url in SOURCES.items():
        path = raw / f"{source}.json"
        path.write_bytes(fetch(url))
        meta[source] = sha(path)
    symbols = set()
    pages = {}
    continuation = None
    for number in range(1, 20):
        params = {"prefix": PREFIX, "delimiter": "/", "max-keys": 1000}
        if continuation:
            params["continuation-token"] = continuation
        path = raw / f"page-{number:03d}.xml"
        page = list_page(params, path)
        pages[path.name] = sha(path)
        for prefix in page.findall("s3:CommonPrefixes/s3:Prefix", NS):
            symbol = prefix.text.removeprefix(PREFIX).removesuffix("/")
            if symbol.endswith("USDT") and "/" not in symbol:
                symbols.add(symbol)
        continuation = page.findtext("s3:NextContinuationToken", namespaces=NS)
        if page.findtext("s3:IsTruncated", namespaces=NS) == "false":
            break
        if not continuation:
            raise ValueError("Truncated S3 enumeration without cursor")
    else:
        raise ValueError("S3 pagination exceeded registered bound")
    with ThreadPoolExecutor(max_workers=plan["workers"]) as pool:
        rows = list(pool.map(lambda s: first_day(s, first), sorted(symbols)))

    exchange = {row["symbol"]: row for row in read(raw / "exchange.json")["symbols"]}
    collector = {
        row["id"].upper(): row
        for row in read(raw / "collector.json")["availableSymbols"]
        if row["type"] == "perpetual"
    }
    for row in rows:
        symbol = row["symbol"]
        if symbol in exchange:
            found = exchange[symbol]
            row["exchange_status"] = found.get("status")
            row["contract_type"] = found.get("contractType")
            row["onboard_utc"] = datetime.fromtimestamp(
                found["onboardDate"] / 1000, UTC
            ).isoformat()
        if symbol in collector:
            row["collector_since"] = collector[symbol].get("availableSince")
            row["collector_to"] = collector[symbol].get("availableTo")
    destination = OUT / "catalog.json"
    save(
        destination,
        {
            "registration_sha256": sha(PLAN),
            "captured_utc": now(),
            "page_sha256": pages,
            "metadata_sha256": meta,
            "symbols": rows,
        },
    )
    print(
        json.dumps(
            {
                "scanned": len(rows),
                "with_scan_error": sum("scan_error" in row for row in rows),
                "with_first_day": sum(bool(row.get("first_archive_day")) for row in rows),
                "receipt": str(destination),
                "sha256": sha(destination),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    {"register": register, "capture": capture}[sys.argv[1]]()
