"""Run fixed source policy through native constructor/provider/callbacks, no wallet."""

# ruff: noqa: S101
import argparse
import json
import shutil
import socket
import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research.crypto.wavelet_policy_native_20261117_v3.source_policy import (
    ENGINE,
    FEATURE,
    HERE,
    PROJECTION_RECEIPTS,
    bootstrap,
    sha,
)


ROOT = HERE.parents[2]


def save(path, value):
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")


def parameters(strategy):
    resolved = {name: par.value for name, par in strategy.enumerate_parameters()}
    for name, expected in {**strategy.buy_params, **strategy.sell_params}.items():
        if name in resolved:
            assert resolved[name] == expected, (name, resolved[name], expected)
    assert strategy.stoploss == -0.05 and strategy.minimal_roi == {0: 0.03}
    assert strategy.exit_profit_only and strategy.ignore_roi_if_entry_signal
    assert not strategy.position_adjustment_enable and not strategy.trailing_stop
    assert not strategy.strategy_config.aggregate_pairs
    assert not strategy.use_mlx and not strategy.scale_results
    assert strategy._ft_params_from_file == {}
    assert type(strategy.forecaster).__name__ == "sgd_forecaster"
    assert strategy.forecaster_type.name == "SGD"
    return {
        "resolved_hyper_parameters": resolved,
        "source_dictionaries_loaded_exactly": True,
        "parameter_file_values": strategy._ft_params_from_file,
        "stoploss": strategy.stoploss,
        "minimal_roi": strategy.minimal_roi,
        "exit_profit_only": strategy.exit_profit_only,
        "ignore_roi_if_entry_signal": strategy.ignore_roi_if_entry_signal,
        "position_adjustment_enable": strategy.position_adjustment_enable,
        "trailing_stop": strategy.trailing_stop,
        "aggregate_pairs": strategy.strategy_config.aggregate_pairs,
        "scale_results": strategy.scale_results,
        "strategy_config_scale_results": strategy.strategy_config.scale_results,
        "use_mlx": strategy.use_mlx,
        "forecaster_display_name": strategy.forecaster.get_name(),
        "forecaster_class": type(strategy.forecaster).__name__,
    }


def signal_arithmetic(frame, strategy, np):
    profit = frame.gain.clip(lower=0)
    loss = frame.gain.clip(upper=0)
    pt = (profit.rolling(40).mean() + 1.9 * profit.rolling(40).std()).clip(lower=0.5).fillna(0)
    lt = (loss.rolling(40).mean() - 1.8 * abs(loss.rolling(40).std())).clip(upper=-0.5).fillna(0)
    np.testing.assert_array_equal(frame.target_profit, pt)
    np.testing.assert_array_equal(frame.target_loss, lt)
    entry = (
        (frame.predicted_gain > pt)
        & (frame.volume > 0)
        & (frame.rvol > -1)
        & (frame.atr_pct_roll > -1)
        & (frame.guard_metric < 0)
        & (frame.close_norm < 1.1)
        & (frame.adx > -1)
        & (frame.bb_width > -1)
    )
    exit_ = (frame.predicted_gain < lt) & (frame.rvol > 2) & (frame.guard_metric > 0)
    np.testing.assert_array_equal(frame.enter_long.fillna(0).astype(bool), entry)
    np.testing.assert_array_equal(frame.exit_long.fillna(0).astype(bool), exit_)
    return {
        "rows": len(frame),
        "independent_targets_and_masks_equal": True,
        "entry_signals": int(entry.sum()),
        "exit_signals": int(exit_.sum()),
        "signals_are_not_trades": True,
    }


def stop_contract(strategy):
    from freqtrade.persistence import LocalTrade

    start = datetime(2025, 1, 1, tzinfo=UTC)
    trade = LocalTrade(
        pair="BTC/USDT",
        open_rate=100,
        amount=1,
        stake_amount=100,
        open_date=start,
        is_short=False,
        fee_open=0.002,
        fee_close=0.002,
    )
    # Native strategy method calls native LocalTrade.adjust_stop_loss.
    strategy.ft_stoploss_adjust(100, trade, start, 0, 0, after_fill=True)
    assert abs(trade.stop_loss - 83) < 1e-10
    strategy.ft_stoploss_adjust(110, trade, start + timedelta(hours=2), 0.1, 0)
    assert abs(trade.stop_loss - 83) < 1e-10
    strategy.ft_stoploss_adjust(110, trade, start + timedelta(hours=2.6), 0.1, 0)
    assert abs(trade.stop_loss - 95) < 1e-10
    strategy.ft_stoploss_adjust(120, trade, start + timedelta(hours=3), 0.2, 0)
    assert abs(trade.stop_loss - 95) < 1e-10
    confirmations = []
    for age, reason, allowed in [
        (0, "stop_loss", True),
        (0.5, "roi", False),
        (0.5, "exit_signal", False),
        (1, "roi", True),
        (0, "force_exit", True),
    ]:
        actual = strategy.confirm_trade_exit(
            "BTC/USDT", trade, "market", 1, 100, "GTC", reason, start + timedelta(hours=age)
        )
        assert actual == allowed
        confirmations.append({"age_hours": age, "reason": reason, "allowed": actual})
    return {
        "native_after_fill_stop": 83,
        "stop_during_grace_at_price110": 83,
        "native_stop_after2_6hours": 95,
        "stop_at_price120_does_not_trail": 95,
        "confirmations": confirmations,
        "detached_native_trade_only": True,
        "financial_wallets": 0,
    }


def verify_interface_freeze():
    freeze = json.loads((HERE / "interface_freeze.json").read_text())
    for path, digest in freeze["files"].items():
        assert sha(path) == digest, path


def run(attempt):
    verify_interface_freeze()
    libs = bootstrap()
    pd, np = libs["pd"], libs["np"]
    sys.path.insert(0, str(ENGINE))
    import freqtrade
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver

    assert Path(freqtrade.__file__).resolve() == ENGINE / "freqtrade/__init__.py"
    official = json.loads((ENGINE.parent / "FREQTRADE_SOURCE.json").read_text())
    for relative, digest in official["files"].items():
        assert sha(ENGINE / relative) == digest
    output = ROOT / "user_data/crypto_quant" / HERE.name / attempt
    output.mkdir(parents=True, exist_ok=False)
    strategies = output / "strategies"
    strategies.mkdir()
    shutil.copyfile(HERE / "SourceWaveletCPU.py", strategies / "SourceWaveletCPU.py")
    # Reuse previous exact 2000-row two-asset native input, no new market data.
    candles = ROOT / "user_data/crypto_quant" / FEATURE.name / "feature_admission/candles"
    config = json.loads(
        (
            HERE.with_name("trendcore_source_native_20261117_v1")
            / "trendcore_1000_conservative.json"
        ).read_text()
    )
    for key in [
        "stoploss",
        "minimal_roi",
        "trailing_stop",
        "position_adjustment_enable",
        "max_entry_position_adjustment",
        "research_trendcore_nooi",
        "research_matched_nfi",
        "signal_prices",
        "timeframe_detail",
    ]:
        config.pop(key, None)
    config.update(
        strategy="SourceWaveletCPU",
        timeframe="15m",
        bot_name="source_policy_probe_only",
        user_data_dir=str(output),
        strategy_path=str(strategies),
        datadir=str(candles),
        timerange="1727740800-1729540799",
        db_url="sqlite://",
        export="none",
    )
    save(output / "config.json", config)
    cfg = setup_optimize_configuration(
        {"config": [str(output / "config.json")], "datadir": str(candles)}, RunMode.BACKTEST
    )
    exchange = ExchangeResolver.load_exchange(cfg, validate=False, load_leverage_tiers=False)
    snapshot = json.loads(
        (HERE.with_name("spot_baseline_20261105") / "ccxt-spot-market-rules.json").read_text()
    )
    exchange._markets = snapshot["markets"]
    exchange._leverage_tiers = snapshot["leverage_tiers"]
    exchange._api.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange._api_async.set_markets(snapshot["markets"], snapshot["currencies"])
    exchange.validate_config(cfg)

    def no_network(*args, **kwargs):
        raise RuntimeError("Policy probe attempted network")

    socket.socket.connect = no_network
    account = None
    stage = "native_constructor"
    try:
        account = Backtesting(cfg, exchange=exchange)
        strategy = account.strategylist[0]
        account._set_strategy(strategy)
        stage = "native_parameters"
        save(output / "effective_parameters.json", parameters(strategy))
        save(output / "source_projection.json", PROJECTION_RECEIPTS)
        stage = "native_stop_and_confirmation_callbacks"
        save(output / "stop_contract.json", stop_contract(strategy))
        data, _ = account.load_bt_data()
        receipts = []
        for pair, frame in data.items():
            assert len(frame) == 2000
            asset = pair.split("/")[0]
            variants = {
                "full2000": frame.copy(),
                "prefix1800": frame.iloc[:1800].copy(),
                "future_shock": frame.copy(),
            }
            for col in ["open", "high", "low", "close"]:
                variants["future_shock"].loc[1800:, col] *= np.linspace(2, 10, 200)
            results = {}
            for case, input_frame in variants.items():
                stage = f"native_indicators_{asset}_{case}"
                actual = strategy.advise_all_indicators({pair: input_frame})[pair]
                stage = f"native_signals_{asset}_{case}"
                actual = strategy.ft_advise_signals(actual, {"pair": pair})
                contract = signal_arithmetic(actual, strategy, np)
                path = output / (asset + "_" + case + ".parquet")
                actual.to_parquet(path, index=False)
                results[case] = actual
                receipts.append(
                    {"pair": pair, "case": case, **contract, "path": str(path), "sha256": sha(path)}
                )
                print("POLICY_CASE_PASS", asset, case, flush=True)
            reference = pd.read_parquet(
                FEATURE / "numerical_outputs" / (asset + "_full6000.parquet")
            )
            np.testing.assert_array_equal(
                results["full2000"].predicted_gain, reference.source_predicted_gain.iloc[:2000]
            )
            for case in ["prefix1800", "future_shock"]:
                for col in [
                    "gain",
                    "predicted_gain",
                    "target_profit",
                    "target_loss",
                    "enter_long",
                    "exit_long",
                    "guard_metric",
                    "rvol",
                    "close_norm",
                    "atr_pct_roll",
                    "adx",
                    "bb_width",
                ]:
                    np.testing.assert_array_equal(
                        results["full2000"][col].iloc[:1800], results[case][col].iloc[:1800]
                    )
        assert not LocalTrade.bt_trades and not LocalTrade.bt_trades_open
        for relative, digest in official["files"].items():
            assert sha(ENGINE / relative) == digest
        save(
            output / "admission.json",
            {
                "passed": True,
                "cases": receipts,
                "actual_native_constructor_provider_full_indicators_signals_callbacks": True,
                "predictive_power_proven": False,
                "financial_start_called": False,
                "new_financial_wallets": 0,
                "goal_complete": False,
            },
        )
        print("NATIVE_POLICY_ADMISSION_PASS", flush=True)
    except Exception as exc:
        save(
            output / "failure.json",
            {
                "status": "POLICY_ADMISSION_FAILED",
                "stage": stage,
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "financial_start_called": False,
                "new_financial_wallets": 0,
                "source_policy_rejection_not_automatic": True,
            },
        )
        raise
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", required=True)
    run(parser.parse_args().attempt)
