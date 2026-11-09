"""Extend immutable official-source spot candles with unsigned public klines."""

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant/nfi_truth_audit_20261109_v001"
SOURCE = ROOT / "user_data/crypto_quant/spot_baseline_data_long_20261105_v001/candles"
END = pd.Timestamp("2026-11-09T05:05:00Z")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():  # noqa: C901 - immutable input admission and streaming public intake
    target = OUT / "spot-candles-v002"
    target.mkdir(exist_ok=False)
    raw = OUT / "spot-public-inputs"
    raw.mkdir(exist_ok=True)
    pairs = json.loads((ROOT / "deploy/configs/spot.json").read_text())["exchange"][
        "pair_whitelist"
    ]

    def prepare(pair):  # noqa: C901 - chronological public data admission
        stem = pair.replace("/", "_")
        price = pd.read_feather(SOURCE / f"{stem}-5m.feather")
        original_hashes = {
            str(SOURCE / f"{stem}-{tf}.feather"): sha(SOURCE / f"{stem}-{tf}.feather")
            for tf in ("5m", "15m", "1h", "4h", "1d")
        }
        cursor = int((price.date.iloc[-1] + pd.Timedelta(minutes=5)).timestamp() * 1000)
        downloaded = []
        with requests.Session() as session:
            session.trust_env = False
            session.proxies.update(https="http://127.0.0.1:7897")
            while cursor < END.timestamp() * 1000:
                cached = raw / f"{stem}-{cursor}.json"
                if cached.exists():
                    rows = json.loads(cached.read_text())
                    downloaded.extend(rows)
                    cursor = int(rows[-1][0]) + 300000
                    continue
                # Single worker and one request/second across the whole intake.
                time.sleep(1)
                for attempt in range(3):
                    try:
                        response = session.get(
                            "https://api.binance.com/api/v3/klines",
                            params={
                                "symbol": pair.replace("/", ""),
                                "interval": "5m",
                                "startTime": cursor,
                                "endTime": int(END.timestamp() * 1000) - 1,
                                "limit": 1000,
                            },
                            timeout=25,
                        )
                        if response.status_code in (418, 429):
                            raise RuntimeError("Public API rate limit: stop intake without retry")
                        response.raise_for_status()
                        rows = response.json()
                        break
                    except (requests.ConnectionError, requests.Timeout):
                        if attempt == 2:
                            raise
                        time.sleep(attempt + 1)
                if not rows:
                    raise ValueError("Missing public spot data; no fabricated bars")
                (raw / f"{stem}-{cursor}.json").write_text(json.dumps(rows) + "\n")
                downloaded.extend(rows)
                cursor = int(rows[-1][0]) + 300000
        if downloaded:
            addition = pd.DataFrame(
                {
                    "date": pd.to_datetime([r[0] for r in downloaded], unit="ms", utc=True),
                    **{
                        name: [float(r[i]) for r in downloaded]
                        for name, i in (
                            ("open", 1),
                            ("high", 2),
                            ("low", 3),
                            ("close", 4),
                            ("volume", 5),
                        )
                    },
                }
            )
            price = pd.concat([price, addition], ignore_index=True)
        price["date"] = pd.to_datetime(price.date, utc=True).dt.as_unit("ns")
        price = price.loc[price.date < END].reset_index(drop=True)
        expected = pd.date_range(price.date.iloc[0], END - pd.Timedelta(minutes=5), freq="5min")
        if (
            not pd.DatetimeIndex(price.date).equals(expected)
            or not np.isfinite(price[["open", "high", "low", "close", "volume"]]).all().all()
        ):
            raise ValueError("Missing/duplicate/nonfinite actual spot candle")
        if not (
            (price.low <= price[["open", "close"]].min(axis=1))
            & (price.high >= price[["open", "close"]].max(axis=1))
            & (price.low > 0)
        ).all():
            raise ValueError("Invalid actual spot OHLC")
        price.to_feather(target / f"{stem}-5m.feather")
        for tf, frequency in (("15m", "15min"), ("1h", "h"), ("4h", "4h"), ("1d", "D")):
            before = pd.read_feather(SOURCE / f"{stem}-{tf}.feather")
            grouped = (
                price.set_index("date")
                .resample(frequency, label="left", closed="left")
                .agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
                )
                .dropna()
                .reset_index()
            )
            added = grouped.loc[grouped.date > before.date.iloc[-1]]
            combined = pd.concat([before, added], ignore_index=True)
            combined["date"] = pd.to_datetime(combined.date, utc=True).dt.as_unit("ns")
            combined.loc[combined.date < END].reset_index(drop=True).to_feather(
                target / f"{stem}-{tf}.feather"
            )
        if any(sha(p) != digest for p, digest in original_hashes.items()):
            raise ValueError("Immutable prior spot input changed")
        return {
            "pair": pair,
            "downloaded_main_bars": len(downloaded),
            "main_rows": len(price),
            "first": str(price.date.iloc[0]),
            "last": str(price.date.iloc[-1]),
            "old_input_hashes_unchanged": original_hashes,
        }

    records = [prepare(pair) for pair in pairs]
    receipts = {p.name: sha(p) for p in sorted(target.glob("*.feather"))}
    (OUT / "spot-data-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
    (HERE / "spot-input-admission.json").write_text(
        json.dumps(
            {
                "pairs": len(pairs),
                "input_files": len(receipts),
                "per_pair": records,
                "source": (
                    "Preserved native official-source history plus public Binance spot klines; "
                    "informative tail aggregated from actual5m prices"
                ),
                "execution_end_exclusive": str(END),
                "invented_quotes": False,
                "orders_enabled": False,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        {
            "spot_pairs": len(pairs),
            "actual_candle_files": len(receipts),
            "public_tail_bars": sum(r["downloaded_main_bars"] for r in records),
        }
    )


if __name__ == "__main__":
    main()
