"""Build price-only cross-sectional features; no forward returns or economic selection."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha


def build(dataset, symbols):
    closes, atrs = {}, {}
    for symbol in sorted(symbols):
        pair = symbol.removesuffix("USDT") + "_USDT_USDT"
        frame = pd.read_feather(dataset / f"candles/futures/{pair}-1h-futures.feather")
        if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
            raise ValueError("Hourly clock is not unique and increasing")
        frame = frame.set_index("date")
        closes[symbol] = frame.close
        previous = frame.close.shift(1)
        atrs[symbol] = (
            pd.concat(
                [
                    frame.high - frame.low,
                    (frame.high - previous).abs(),
                    (frame.low - previous).abs(),
                ],
                axis=1,
            )
            .max(axis=1)
            .rolling(24)
            .mean()
        )
    # A complete clock prevents shift(168) from silently spanning a missing hour.
    raw = pd.DataFrame(closes)
    clock = pd.date_range(raw.index.min(), raw.index.max(), freq="h")
    close = raw.reindex(clock)
    returns = close / close.shift(1) - 1
    momentum = close / close.shift(168) - 1
    vol = returns.rolling(168).std(ddof=0)
    score = momentum / (vol * np.sqrt(168)).replace(0, np.nan)
    complete = score.notna().all(axis=1) & close.rolling(169).count().eq(169).all(axis=1)
    # Stable symbol order resolves exact ties without future information.
    high_rank = score.rank(axis=1, ascending=False, method="first")
    low_rank = score.rank(axis=1, ascending=True, method="first")
    btc = close.BTCUSDT
    btc_mean = btc.rolling(480).mean()
    btc_ret = btc / btc.shift(24) - 1
    own_mean = close.rolling(168).mean()
    chunks = []
    for symbol in sorted(symbols):
        long = (
            complete
            & high_rank[symbol].le(3)
            & momentum[symbol].gt(0)
            & close[symbol].gt(own_mean[symbol])
            & btc.gt(btc_mean)
        )
        short = (
            complete
            & low_rank[symbol].le(3)
            & momentum[symbol].lt(0)
            & close[symbol].lt(own_mean[symbol])
            & btc.lt(btc_mean)
        )
        chunks.append(
            pd.DataFrame(
                {
                    "available_at": clock + pd.Timedelta(hours=1),
                    "symbol": symbol,
                    "hourly_atr": atrs[symbol].reindex(clock).to_numpy(),
                    "momentum_7d": momentum[symbol].to_numpy(),
                    "momentum_score": score[symbol].to_numpy(),
                    "btc_return_24h": btc_ret.to_numpy(),
                    "complete_universe": complete.to_numpy(),
                    "target_long": long.to_numpy(),
                    "target_short": short.to_numpy(),
                }
            )
        )
    return pd.concat(chunks, ignore_index=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = read(args.base_plan)
    dataset = Path(plan["dataset"])
    if sha(dataset / "receipts.json") != plan["data_receipts_sha256"]:
        raise ValueError("Dataset receipts changed")
    for name, expected in read(dataset / "receipts.json").items():
        if sha(dataset / name) != expected:
            raise ValueError(f"Input changed: {name}")
    args.output.mkdir(parents=True, exist_ok=False)
    panel = build(dataset, plan["symbols"])
    panel.to_parquet(args.output / "panel.parquet", index=False)
    save(
        args.output / "receipt.json",
        {
            "builder_sha256": sha(Path(__file__)),
            "panel_sha256": sha(args.output / "panel.parquet"),
            "data_receipts_sha256": plan["data_receipts_sha256"],
            "rows": len(panel),
            "features_only": True,
            "forward_outcomes_computed": False,
            "orders_enabled": False,
        },
    )
    print({"rows": len(panel), "panel": str(args.output / "panel.parquet")})
