"""Preserve v001 and restore available native 800-day daily warmup in v002."""

import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from research.crypto.nfi80_capacity_20261109.study import (
    HERE,
    OUT,
    PROTOCOL,
    ROOT,
    START,
    get,
    read,
    ref,
    save,
    sha,
)


OLD = ROOT / "user_data/crypto_quant/nfi80_capacity_20261109_v001"


def clone(source, target):
    # Immutable large inputs may be shared. JSON and repaired daily inputs get real copies.
    if str(source).endswith((".json", "-1d-futures.feather")):
        return shutil.copy2(source, target)
    os.link(source, target)
    return target


def repair(pair):
    from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
    from freqtrade.enums import CandleType

    policy = read(PROTOCOL)
    end = pd.Timestamp(policy["end"])
    since = START - pd.Timedelta(days=800)
    symbol = pair.split(":")[0].replace("/", "")
    folder = OUT / "raw" / symbol
    raw = folder / "native-daily-warmup-api.json"
    rows = get(
        "https://fapi.binance.com/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": "1d",
            "startTime": int(since.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000) - 1,
            "limit": 1500,
        },
    ).json()
    save(raw, rows)
    if not isinstance(rows, list) or not rows or len(rows) == 1500:
        raise ValueError("Daily warmup response empty or truncated")
    rows = [r for r in rows if r[6] < int(end.timestamp() * 1000)]
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows], unit="ms", utc=True),
            **{
                name: [float(r[i]) for r in rows]
                for i, name in enumerate(("open", "high", "low", "close", "volume"), 1)
            },
        }
    )
    expected = pd.date_range(frame.date.iloc[0], end.normalize(), freq="D", inclusive="left")
    if not pd.DatetimeIndex(frame.date).as_unit("ns").equals(expected.as_unit("ns")):
        raise ValueError("Daily warmup has missing real quotes")
    if (
        not np.isfinite(frame.iloc[:, 1:].to_numpy()).all()
        or (frame[["open", "high", "low", "close"]] <= 0).any().any()
    ):
        raise ValueError("Daily warmup values invalid")
    if frame.date.iloc[0] > START - pd.Timedelta(days=60):
        raise ValueError("Fresh daily source contradicts pre-start history admission")
    handler = FeatherDataHandler(OUT / "candles")
    handler.ohlcv_store(pair, "1d", frame, CandleType.FUTURES)
    record = read(folder / "completed.json")
    name = "futures/" + pair.replace("/", "_").replace(":", "_") + "-1d-futures.feather"
    record["candles"][name] = sha(OUT / "candles" / name)
    record["daily_warmup"] = {
        "source": ref(raw),
        "first_observation": str(frame.date.iloc[0]),
        "days_before_start": int((frame.date < START).sum()),
        "native_requested_days": 800,
        "shorter_only_if_actual_listing_history_shorter": True,
    }
    save(folder / "completed.json", record)
    return pair, record


def main():
    if OUT == OLD or OUT.exists():
        raise FileExistsError("Require a fresh technical data revision")
    OUT.mkdir()
    for name in ("candles", "raw"):
        shutil.copytree(OLD / name, OUT / name, copy_function=clone)
    shutil.copy2(OLD / "market-rules.json", OUT / "market-rules.json")
    old_policy = read(PROTOCOL)
    save(HERE / "protocol-before-native-warmup.json", old_policy)
    old_policy.update(
        output_revision=2,
        source_code=ref(HERE / "study.py"),
        daily_warmup_repair=ref(HERE / "repair_warmup.py"),
        prior_protocol=ref(HERE / "protocol-before-native-warmup.json"),
        market_rules=ref(OUT / "market-rules.json"),
        technical_revision_at_utc=datetime.now(UTC).isoformat(),
        technical_revision_reason=(
            "Restore available 800-day daily lookback, preserve prior inputs and "
            "interrupted account; no economic results used or parameters changed"
        ),
    )
    save(PROTOCOL, old_policy)
    shutil.copy2(HERE / "native-plans.json", HERE / "native-plans-v001.json")
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(repair, p) for p in old_policy["pairs"]]
        for future in as_completed(futures):
            pair, record = future.result()
            results[pair] = record
            print({"daily_warmup_complete": len(results), "pair": pair}, flush=True)
    receipts = {name: digest for row in results.values() for name, digest in row["candles"].items()}
    save(OUT / "admission.json", results)
    save(OUT / "data-receipts.json", receipts)
    policy = read(PROTOCOL)
    policy.update(
        data_admission=ref(OUT / "admission.json"), input_receipts=ref(OUT / "data-receipts.json")
    )
    save(PROTOCOL, policy)


if __name__ == "__main__":
    main()
