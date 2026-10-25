"""Freeze an author-list universe by pre-evaluation volume, with survivorship disclosed."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import ccxt
import pandas as pd

from freqtrade.research.data import read, read_archive, save, sha
from research.crypto.prepare_nfi import archive


def select(protocol_path: Path, pairlist_path: Path, output: Path):
    protocol = read(protocol_path)
    output.mkdir(parents=True, exist_ok=False)
    author_pairs = read(pairlist_path)["exchange"]["pair_whitelist"]
    exchange = ccxt.binance(
        {
            "enableRateLimit": True,
            "timeout": 20000,
            "options": {
                "defaultType": "future",
                "fetchMarkets": {"types": ["linear"]},
                "fetchCurrencies": False,
            },
        }
    )
    markets = exchange.load_markets()
    save(output / "protocol.json", protocol)
    save(output / "market_metadata.json", {p: markets[p] for p in author_pairs if p in markets})
    warmup_limit = int(pd.Timestamp("2021-10-01", tz="UTC").timestamp() * 1000)
    eligible, excluded = [], []
    for pair in author_pairs:
        market = markets.get(pair)
        if (
            not market
            or not market.get("active")
            or not market.get("linear")
            or not market.get("swap")
        ):
            excluded.append(
                {"pair": pair, "reason": "not currently deployable identical perpetual"}
            )
            continue
        onboard = int(market["info"].get("onboardDate", 0))
        if not onboard or onboard > warmup_limit:
            excluded.append({"pair": pair, "reason": "less than declared historical warmup age"})
            continue
        eligible.append((pair, market["id"]))
    records, failures = [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(archive, symbol, "1d", protocol["ranking_period"], output / "raw"): (
                pair,
                symbol,
            )
            for pair, symbol in eligible
        }
        for future in as_completed(futures):
            pair, symbol = futures[future]
            try:
                source = future.result()
                frame = read_archive(Path(source["path"]))
                expected = pd.date_range(
                    "2023-12-01", "2024-01-01", inclusive="left", freq="D", tz="UTC"
                )
                if not pd.DatetimeIndex(frame.date).equals(expected):
                    raise ValueError("ranking-month calendar incomplete")
                records.append(
                    {
                        "pair": pair,
                        "symbol": symbol,
                        "quote_volume": float(frame.quote_volume.sum()),
                        "source": source,
                    }
                )
            except Exception as error:
                failures.append({"pair": pair, "error": str(error)})
    save(output / "excluded.json", excluded)
    save(output / "failures.json", failures)
    if failures:
        raise ValueError("Volume ranking cannot silently omit download failures")
    records.sort(key=lambda r: (-r["quote_volume"], r["symbol"]))
    save(output / "ranked.json", records)
    if len(records) < protocol["target_size"]:
        raise ValueError(f"Only {len(records)} eligible; do not silently relax the protocol")
    selected = records[: protocol["target_size"]]
    result = {
        "protocol_sha256": sha(protocol_path),
        "author_pairlist_sha256": sha(pairlist_path),
        "author_size": len(author_pairs),
        "eligible_size": len(records),
        "symbols": [r["symbol"] for r in selected],
        "pairs": [r["pair"] for r in selected],
        "survivorship_bias_possible": True,
        "strategy_returns_used": False,
        "orders_enabled": False,
    }
    save(output / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--pairlist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(select(args.protocol.resolve(), args.pairlist.resolve(), args.output.resolve()))
