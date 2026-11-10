"""Offline diagnostic of the native minimum-stake argument difference."""

import json

from freqtrade.enums import TradingMode
from freqtrade.exchange import Binance
from research.crypto.nfi_live_window_20261110_v002.replay import HERE, OUT, ROOT, save, sha


def main():
    # No constructor/client or account is created: this native method only reads
    # frozen symbol limits. Provide its dependencies and inert cleanup state.
    exchange = object.__new__(Binance)
    exchange._config = json.loads((OUT / "futures/native-run-verified/config.json").read_text())
    exchange._markets = json.loads((OUT / "futures/metadata.json").read_text())["markets"]
    exchange.trading_mode = TradingMode.FUTURES
    exchange._exchange_ws = None
    exchange.loop = None
    minimum = exchange.get_min_pair_stake_amount
    backtest_min = minimum("DOT/USDT:USDT", 0.8501, -0.05, 3)
    live_min = minimum("DOT/USDT:USDT", 0.8493, -0.99, 3)
    if abs(backtest_min - 1.842105263157895) >= 1e-12 or live_min != 2.5:
        raise ValueError("Native minimum-stake diagnostic changed")
    sources = [
        "freqtrade/optimize/backtesting.py",
        "freqtrade/freqtradebot.py",
        "freqtrade/exchange/exchange.py",
        str((HERE / "allocation_probe.py").relative_to(ROOT)),
    ]
    save(
        HERE / "allocation-diagnostic.json",
        {
            "method": "Actual native Binance.get_min_pair_stake_amount, offline diagnostic only",
            "backtest_initial_stoploss_argument": -0.05,
            "live_initial_stoploss_argument": -0.99,
            "backtest_min_stake_usdt": backtest_min,
            "live_min_stake_usdt": live_min,
            "market_min_notional_usdt": 5,
            "leverage": 3,
            "backtest_actual_stake_usdt": 2.04024,
            "live_actual_stake_usdt": 2.4918666666666667,
            "source_sha256": {p: sha(ROOT / p) for p in sources},
            "conclusion": (
                "Native initial minimum-stake reserve differs between backtest and live; "
                "no fix or parameter change applied"
            ),
            "simulation_returns_recomputed": False,
        },
    )
    print(json.dumps({"native_minimum_stake": backtest_min, "live_minimum_stake": live_min}))


if __name__ == "__main__":
    main()
