"""Freeze discovery-only residual inputs and verify real-data prefix invariance."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import talib.abstract as ta

from freqtrade.research.data import read, save, sha
from research.crypto.btc_residual import events, features


FIELDS = [
    "beta",
    "residual",
    "residual_sigma",
    "residual_z",
    "ar1_phi",
    "half_life_hours",
    "feature_atr",
    "small_long",
    "small_short",
    "core_long",
    "core_short",
    "stable_small",
    "stable_core",
    "regime_long",
    "regime_short",
]


def build(dataset, output):  # noqa: C901 - sequential source, clock and prefix evidence
    if output.exists():
        raise ValueError("Preserve previous feature panel")
    receipts = read(dataset / "receipts.json")
    symbols = [s for s in read(dataset / "plan.json")["symbols"] if s != "BTCUSDT"]
    start, end = pd.Timestamp("2023-12-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")

    def load(symbol, tf):
        name = f"candles/futures/{symbol.removesuffix('USDT')}_USDT_USDT-{tf}-futures.feather"
        if sha(dataset / name) != receipts[name]:
            raise ValueError("Underlying candle identity changed")
        frame = pd.read_feather(dataset / name)
        frame = frame.loc[frame.date.lt(end)].reset_index(drop=True)
        if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
            raise ValueError("Candle clock is not unique and ordered")
        if not np.isfinite(frame[["open", "high", "low", "close", "volume"]]).all().all():
            raise ValueError("Unknown candle values")
        if frame.close.le(0).any():
            raise ValueError("Nonpositive prices")
        if tf == "5m":
            frame = frame.loc[frame.date.ge(start)].reset_index(drop=True)
            if not pd.DatetimeIndex(frame.date).equals(
                pd.date_range(start, end, freq="5min", inclusive="left")
            ):
                raise ValueError("Missing raw bar; do not fill prices")
        return frame

    def trend(symbol):
        frame = load(symbol, "4h")
        ema = ta.EMA(frame, timeperiod=200)
        return pd.DataFrame(
            {
                "available": frame.date + pd.Timedelta(hours=4),
                "trend_close": frame.close,
                "trend_ema": ema,
            }
        )

    btc, btc_trend = load("BTCUSDT", "5m"), trend("BTCUSDT")
    output.mkdir(parents=True)
    coverage, cases = [], []
    for symbol in symbols:
        price = load(symbol, "5m")
        visible = pd.DataFrame({"available": price.date + pd.Timedelta(minutes=5)})
        own = pd.merge_asof(
            visible,
            trend(symbol),
            on="available",
            direction="backward",
            tolerance=pd.Timedelta(hours=4),
        )
        ref = pd.merge_asof(
            visible,
            btc_trend,
            on="available",
            direction="backward",
            tolerance=pd.Timedelta(hours=4),
        )
        valid = np.isfinite(own[["trend_close", "trend_ema"]]).all(axis=1) & np.isfinite(
            ref[["trend_close", "trend_ema"]]
        ).all(axis=1)
        price["regime_long"] = (
            valid & own.trend_close.gt(own.trend_ema) & ref.trend_close.gt(ref.trend_ema)
        )
        price["regime_short"] = (
            valid & own.trend_close.lt(own.trend_ema) & ref.trend_close.lt(ref.trend_ema)
        )
        result = events(features(price, btc))
        if symbol in ["ETHUSDT", "SOLUSDT", "XRPUSDT"]:
            for clock in ["2024-03-01", "2024-06-01", "2024-09-01"]:
                mask = price.date.lt(pd.Timestamp(clock, tz="UTC"))
                cut = events(features(price.loc[mask], btc.loc[mask]))
                pd.testing.assert_frame_equal(result.loc[mask], cut, check_exact=True)
                cases.append({"symbol": symbol, "cut": clock, "rows": len(cut), "passed": True})
        panel = result[["date", *FIELDS]].copy()
        panel["feature_open"] = panel.date
        # Completed bar t is published at t+5m; native next-bar entry adds another5m.
        panel["date"] = panel.date + pd.Timedelta(minutes=5)
        panel.to_parquet(output / f"{symbol}.parquet", index=False)
        coverage.append(
            {
                "symbol": symbol,
                "rows": len(panel),
                "finite_fit_rows": int(np.isfinite(panel.residual_z).sum()),
            }
        )
    save(
        output / "receipt.json",
        {
            "dataset_receipts_sha256": sha(dataset / "receipts.json"),
            "feature_source_sha256": sha(Path(__file__).with_name("btc_residual.py")),
            "preparer_source_sha256": sha(Path(__file__)),
            "window_5m_bars": 4032,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "coverage": coverage,
            "panel_hashes": {s: sha(output / f"{s}.parquet") for s in symbols},
            "future_outcome_labels_read": False,
            "account_outcomes_computed": False,
        },
    )
    save(
        output / "causality_checks.json",
        {
            "passed": len(cases) == 9,
            "cases": cases,
            "scope": "Exact real-data rolling regression and AR1 prefix equality; four-hour trend "
            "uses source-open+4h availability and backward asof with4h tolerance.",
            "orders_enabled": False,
        },
    )
    print(
        {
            "symbols_prepared": len(coverage),
            "real_prefix_checks": len(cases),
            "native_accounts_computed": 0,
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.dataset, args.output)
