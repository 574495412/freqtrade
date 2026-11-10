"""Read the frozen native inputs and recompute only native indicator/signal rows."""

import json
import socket
from pathlib import Path

import pandas as pd

from deploy.mainnet_100_20261109.capital import CappedWallets
from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize import backtesting as native
from freqtrade.resolvers import ExchangeResolver
from research.crypto.nfi_live_window_20261110_v002.replay import OUT, ROOT, save, sha


HERE = Path(__file__).resolve().parent
TARGET = OUT / "entry-chart-v002"
PAIR = "DOT/USDT:USDT"


def main():
    TARGET.mkdir(exist_ok=False)
    run = OUT / "futures/native-run-verified"
    registration = json.loads((run / "registration.json").read_text())
    for path, digest in registration["sources"].items():
        if sha(ROOT / path) != digest:
            raise ValueError("Native registered source changed")
    raw = json.loads((run / "config.json").read_text())
    raw["user_data_dir"] = str(TARGET)
    raw["exportdirectory"] = str(TARGET)
    save(TARGET / "config.json", raw)
    config = setup_optimize_configuration(
        {
            "config": [str(TARGET / "config.json")],
            "datadir": raw["datadir"],
            "exportdirectory": TARGET,
        },
        RunMode.BACKTEST,
    )

    def no_network(*args, **kwargs):
        raise RuntimeError("Chart indicator extraction is offline only")

    socket.socket.connect = no_network
    frozen = json.loads((OUT / "futures/metadata.json").read_text())
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    exchange._markets = frozen["markets"]
    exchange._leverage_tiers = frozen["leverage_tiers"]
    exchange._api.set_markets(frozen["markets"], frozen["currencies"])
    exchange._api_async.set_markets(frozen["markets"], frozen["currencies"])
    native.Wallets = CappedWallets
    bt = native.Backtesting(config, exchange=exchange)
    bt.progress = None
    try:
        data, _ = bt.load_bt_data()
        bt._set_strategy(bt.strategylist[0])
        analyzed = bt.strategy.advise_all_indicators({PAIR: data[PAIR]})[PAIR]
        analyzed = bt.strategy.ft_advise_signals(analyzed, {"pair": PAIR})
        fields = [
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "EMA_12",
            "EMA_26",
            "BBL_20_2.0",
            "BBU_20_2.0",
            "RSI_3",
            "RSI_14",
            "RSI_3_15m",
            "CMF_20",
            "OBV_change_pct_15m",
            "RSI_14_1h",
            "RSI_14_4h",
            "RSI_14_1d",
            "EMA_12_4h",
            "EMA_200_4h",
            "ROC_9_1d",
            "enter_short",
            "enter_tag",
        ]
        display = analyzed.loc[
            analyzed.date >= pd.Timestamp("2026-11-10T06:30:00Z"), fields
        ].reset_index(drop=True)
        signal_time = pd.Timestamp("2026-11-10T09:00:00Z")
        row = display.loc[display.date.eq(signal_time)].iloc[0]
        if row.enter_short != 1 or row.enter_tag.strip() != "562":
            raise ValueError("Recomputed native signal does not match verified replay")
        checks = {
            "close_below_bollinger_lower": row["close"] < row["BBL_20_2.0"],
            "ema12_below_ema26": row["EMA_12"] < row["EMA_26"],
            "rsi3_below20": row["RSI_3"] < 20,
            "rsi3_15m_below35": row["RSI_3_15m"] < 35,
            "rsi14_between15_32": 15 < row["RSI_14"] < 32,
            "cmf_below_minus008": row["CMF_20"] < -0.08,
            "obv15m_negative": row["OBV_change_pct_15m"] < 0,
            "rsi1h_below42": row["RSI_14_1h"] < 42,
            "rsi4h_below48": row["RSI_14_4h"] < 48,
            "rsi1d_between25_50": 25 < row["RSI_14_1d"] < 50,
            "ema12_4h_below_ema200_4h": row["EMA_12_4h"] < row["EMA_200_4h"],
            "daily_momentum_negative": row["ROC_9_1d"] < 0,
        }
        if not all(checks.values()):
            raise ValueError("Core562 predicate failed")
        if display.drop(columns=["date", "enter_tag"]).isna().any().any():
            raise ValueError("Missing displayed input/indicator")
        comparison = json.loads(
            (ROOT / "research/crypto/nfi_live_window_20261110_v002/comparison.json").read_text()
        )
        trade = comparison["accounts"]["futures"]["live"]["window_trades"][0]
        result = {
            "pair": PAIR,
            "timezone": "Asia/Shanghai",
            "timeframe": "5m",
            "source": "Admitted public OHLCV; pinned native strategy indicators/signals",
            "rows": json.loads(display.to_json(orient="records", date_format="iso")),
            "signal_candle_open_utc": str(signal_time),
            "signal_available_utc": str(signal_time + pd.Timedelta(minutes=5)),
            "core_predicates": {k: bool(v) for k, v in checks.items()},
            "live_entry": trade,
            "limits": "Signal/indicator chart only; no new account backtest or live action",
            "source_hashes": {
                "script": sha(__file__),
                "native_registration": sha(run / "registration.json"),
                "comparison": sha(
                    ROOT / "research/crypto/nfi_live_window_20261110_v002/comparison.json"
                ),
            },
        }
        save(HERE / "chart-data.json", result)
        print(
            json.dumps(
                {
                    "rows": len(display),
                    "core_predicates": result["core_predicates"],
                    "signal_row": json.loads(
                        display.loc[display.date.eq(signal_time)].to_json(
                            orient="records", date_format="iso"
                        )
                    ),
                }
            )
        )
    finally:
        bt.cleanup()


if __name__ == "__main__":
    main()
