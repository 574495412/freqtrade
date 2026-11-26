"""Actual source sizing/entry/custom-exit calls with native cached real indicators."""

# ruff: noqa: S101
import itertools
import json
import socket
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from research.crypto.wavelet_policy_native_20261117_v3.native_policy_probe import save
from research.crypto.wavelet_policy_native_20261117_v3.source_policy import (
    ENGINE,
    FEATURE,
    HERE,
    bootstrap,
    sha,
)


def independent_exit(profit, age, guard, params):
    if profit > 0:
        if guard > params["metric_overbought_threshold"]:
            return "metric_overbought"
        if profit > 0.022:
            return "take_profit"
    if age >= params["unclog_12h_hours"] and profit > params["unclog_12h_min_profit"]:
        return "unclog_12h"
    if age >= params["unclog_1d_hours"] and profit >= 0:
        return "unclog_1d"
    if age >= 21 * 24:
        return "max_hold"
    return None


def main():
    pd = bootstrap()["pd"]
    sys.path.insert(0, str(ENGINE))
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import CandleType, RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver

    root = HERE.parents[2] / "user_data/crypto_quant" / HERE.name
    output = root / "callback_probe"
    output.mkdir(exist_ok=False)
    config = json.loads((root / "attempt2/config.json").read_text())
    config["user_data_dir"] = str(output)
    save(output / "config.json", config)
    cfg = setup_optimize_configuration({"config": [str(output / "config.json")]}, RunMode.BACKTEST)
    exchange = ExchangeResolver.load_exchange(cfg, validate=False, load_leverage_tiers=False)
    snapshot = json.loads(
        (FEATURE.with_name("spot_baseline_20261105") / "ccxt-spot-market-rules.json").read_text()
    )
    exchange._markets = snapshot["markets"]
    exchange._leverage_tiers = snapshot["leverage_tiers"]
    exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])

    def no_network(*args, **kwargs):
        raise RuntimeError("Callback probe attempted network")

    socket.socket.connect = no_network
    account = None
    try:
        account = Backtesting(cfg, exchange=exchange)
        strategy = account.strategylist[0]
        account._set_strategy(strategy)
        receipts = []
        for asset in ["BTC", "ETH"]:
            pair = asset + "/USDT"
            source = root / "attempt2" / (asset + "_full2000.parquet")
            data = pd.read_parquet(source)
            assert "rsi" not in data
            account.dataprovider._set_cached_df(pair, "15m", data, CandleType.SPOT)
            account.dataprovider._set_dataframe_max_index(pair, len(data))
            last = data.iloc[-1]
            quote_volume = float(last.volume * last.close)
            assert quote_volume > 1000
            now = datetime(2025, 1, 1, tzinfo=UTC)
            sized = strategy.custom_stake_amount(
                pair, now, float(last.close), quote_volume / 5, 1, quote_volume, 1, None, "long"
            )
            assert abs(sized - quote_volume / 10) < 1e-7
            for quote_size, permitted in [(quote_volume / 20, True), (quote_volume / 5, False)]:
                allowed = strategy.confirm_trade_entry(
                    pair,
                    "market",
                    quote_size / last.close,
                    float(last.close),
                    "GTC",
                    now,
                    None,
                    "long",
                )
                assert allowed == permitted
            trade = LocalTrade(
                pair=pair,
                open_rate=100,
                amount=1,
                stake_amount=100,
                open_date=now,
                is_short=False,
                fee_open=0.002,
                fee_close=0.002,
            )
            params = asdict(strategy._exit_params(trade))
            decisions = []
            for profit, age in itertools.product(
                [-0.2, -0.01, 0, 0.001, 0.01, 0.022, 0.022001, 0.03], [0, 0.5, 1, 12, 24, 21 * 24]
            ):
                actual = strategy.custom_exit(pair, trade, now + timedelta(hours=age), 100, profit)
                expected = independent_exit(profit, age, float(last.guard_metric), params)
                assert actual == expected, (asset, profit, age, actual, expected)
                decisions.append(
                    {
                        "net_profit_argument": profit,
                        "age_hours": age,
                        "reason": actual,
                        "independent_reason": expected,
                    }
                )
            receipts.append(
                {
                    "pair": pair,
                    "real_indicator_input": str(source),
                    "input_sha256": sha(source),
                    "quote_volume": quote_volume,
                    "stake_cap": sized,
                    "entry_headroom_cases": 2,
                    "minimal_populator_has_no_rsi_column": True,
                    "exit_params": params,
                    "custom_exit_cases": decisions,
                }
            )
        assert not LocalTrade.bt_trades and not LocalTrade.bt_trades_open
        save(
            output / "admission.json",
            {
                "passed": True,
                "cases": receipts,
                "actual_native_provider_and_source_sizing_entry_custom_exit_called": True,
                "detached_trade_math_only": True,
                "new_financial_wallets": 0,
                "historical_fills_or_profit_not_tested": True,
            },
        )
        print("SOURCE_CALLBACKS_PASS", flush=True)
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == "__main__":
    main()
