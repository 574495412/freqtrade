"""Capture bounded anonymous L2 samples, retaining receipts without signed download links."""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = ROOT / "user_data/crypto_quant/rd_program_20261105_v1/independent_l2_v002"
CF = "https://candlefeed.ai/api/v1/l2/"
HF = "https://huggingface.co/"
REPO = "rogerdehe/mktdata-binance-2026"
REV = "b705fcb01af082a65d59a8fcd5bcd335a9ca9813"
TARGETS = (("DYDXUSDT", "2026-08-31"), ("DYDXUSDT", "2026-11-01"), ("DOTUSDT", "2026-11-01"))
SAMPLE_DAY = "2026-10-01"
CF_SAMPLE = CF + f"sample?symbol=BTCUSDT&date={SAMPLE_DAY}"
CF_FILES = {"snapshot.parquet": "btc-snapshot.parquet", "depth/00.parquet": "btc-depth-00.parquet"}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def utc():
    return datetime.now(UTC).isoformat()


def fetch(url, limit, *, public_url=None):
    started = utc()
    try:
        with urllib.request.urlopen(url, timeout=35) as reply:
            blob = reply.read(limit + 1)
            if len(blob) > limit:
                raise ValueError("response exceeded registered byte limit")
            return blob, {
                "url": public_url or url,
                "started_at_utc": started,
                "http_status": reply.status,
                "host": urlsplit(reply.geturl()).hostname,
                "bytes": len(blob),
                "sha256": sha(blob),
            }
    except urllib.error.HTTPError as exc:
        if public_url is not None:
            return b"", {"url": public_url, "started_at_utc": started, "http_status": exc.code}
        blob = exc.read(limit + 1)
        if len(blob) > limit:
            raise ValueError("error response exceeded registered byte limit") from None
        return blob, {
            "url": url, "started_at_utc": started, "http_status": exc.code,
            "bytes": len(blob), "sha256": sha(blob),
        }


def retain(name, blob, row):
    path = RAW / name
    with path.open("xb") as stream:
        stream.write(blob)
    row["path"] = str(path.relative_to(ROOT))


def download(name, url, expected, *, listing=None):
    if urlsplit(url).scheme != "https" or not 0 < expected["size"] <= 100_000_000:
        raise ValueError("unsupported download URL or registered sample size")
    blob, row = fetch(url, expected["size"], public_url=listing)
    row["sample_name"] = name
    row["expected_sha256"] = expected["sha256"]
    row["expected_bytes"] = expected["size"]
    if row["http_status"] == 200:
        if row["sha256"] != expected["sha256"] or row["bytes"] != expected["size"]:
            raise ValueError("sample hash or byte count differs from publisher manifest")
        retain(name, blob, row)
    return row


def capture():
    reg = HERE / "registration.json"
    RAW.mkdir(parents=True, exist_ok=True)
    receipts = []
    for name, url in (
        ("candlefeed-btc-coverage.json", CF + "coverage?dataset=book&symbol=BTCUSDT"),
        ("candlefeed-public-samples.json", CF + "sample"),
        ("candlefeed-btc-short-anonymous.json", CF + "sample?symbol=BTCUSDT&date=2026-11-01"),
    ):
        blob, row = fetch(url, 1_000_000)
        retain(name, blob, row)
        receipts.append(row)
    blob, row = fetch(CF_SAMPLE, 1_000_000)
    receipts.append(row)
    if row["http_status"] == 200:
        files = {item["name"]: item for item in json.loads(blob)["days"][0]["files"]}
        for original, name in CF_FILES.items():
            item = files[original]
            expected = {"size": item["size"], "sha256": item["sha256"]}
            receipts.append(download(name, item["url"], expected, listing=CF_SAMPLE))

    listing = f"{HF}api/datasets/{REPO}/revision/{REV}?blobs=true"
    blob, row = fetch(listing, 15_000_000)
    if row["http_status"] != 200:
        raise ValueError("could not verify immutable dataset revision")
    retain("hf-revision.json", blob, row)
    receipts.append(row)
    metadata = json.loads(blob)
    if metadata["sha"] != REV:
        raise ValueError("dataset revision drift")
    files = {entry["rfilename"]: entry for entry in metadata["siblings"]}
    for symbol, day in TARGETS:
        contract = f"{symbol}-PERP"
        original = f"{contract}/{day[:7]}/{contract}_{day.replace('-', '')}_deltas.parquet"
        item = files[original]
        receipts.append(download(
            f"hf-{symbol}-{day}.parquet",
            f"{HF}datasets/{REPO}/resolve/{REV}/{original}",
            {"size": item["size"], "sha256": item["lfs"]["sha256"]},
        ))
    write(HERE / "capture_receipts.json", {
        "captured_at_utc": utc(),
        "registration_sha256": sha(reg.read_bytes()),
        "receipts": receipts,
        "financial_accounts": 0,
    })


if __name__ == "__main__":
    capture()
