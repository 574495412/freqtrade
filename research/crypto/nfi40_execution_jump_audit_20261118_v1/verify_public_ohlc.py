"""Compare 144 independently queried official public bars to frozen local OHLCV."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    freeze = json.loads((HERE / "public_crosscheck_freeze.json").read_text())
    for path, digest in freeze["files"].items():
        assert sha(path) == digest
    raw = json.loads((HERE / "binance_public_ohlc_raw.json").read_text())
    local = pd.read_csv(HERE / "oct10_5m_prices.csv", dtype=str)
    rows = []
    for item in raw["records"]:
        query = item["query"]
        assert item["status"] == "fulfilled" and not item["isError"]
        bars = item["response"]["result"]
        assert len(bars) == 48
        pair = query["symbol"].removesuffix("USDT") + "/USDT:USDT"
        selected = local[local.pair.eq(pair)].set_index("date")
        assert len(selected) == 48
        checked = 0
        for index, bar in enumerate(bars):
            assert bar[0] == query["startTime"] + index * 300000
            assert bar[6] == bar[0] + 299999
            row = selected.loc[pd.Timestamp(bar[0], unit="ms", tz="UTC").isoformat()]
            for column, value in zip(
                ["open", "high", "low", "close", "volume"], bar[1:6], strict=True
            ):
                assert Decimal(row[column]) == Decimal(value), (pair, bar[0], column)
                checked += 1
        rows.append(
            {
                "symbol": query["symbol"],
                "bars": len(bars),
                "exact_numeric_OHLCV_checks": checked,
                "startTime": query["startTime"],
                "endTime": query["endTime"],
                "passed": True,
            }
        )
    result = {
        "passed": True,
        "source": raw["tool"],
        "retrieved_at": raw["retrieved_at"],
        "symbols": rows,
        "bars": sum(r["bars"] for r in rows),
        "exact_numeric_OHLCV_checks": sum(r["exact_numeric_OHLCV_checks"] for r in rows),
        "raw_response_sha256": sha(HERE / "binance_public_ohlc_raw.json"),
        "scope": "Official public OHLCV matches 3 largest contributing symbols over 20:00-23:55 UTC; not all40 external verification",
        "trade_level_tick_path_verified": False,
        "queue_slippage_or_real_fills_verified": False,
        "new_native_accounts_started": 0,
    }
    with (HERE / "binance_public_ohlc_comparison.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    run()
