"""Actual official constructor/DataProvider/indicator callback, never Backtesting.start."""

# ruff: noqa: S101
# Independent native-feature assertions; no account replay.
import hashlib
import json
import shutil
import socket
import sys
from pathlib import Path

from research.crypto.wavelet_feature_native_20261117_v2.source_reference import HERE, bootstrap


ROOT = HERE.parents[2]
ENGINE = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str) + "\n")


def main():
    assert json.loads((HERE / "numerical_admission.json").read_text())["passed"]
    for path, digest in json.loads((HERE / "native_interface_freeze.json").read_text())[
        "files"
    ].items():
        assert sha(path) == digest, path
    libs = bootstrap()
    np, pd = libs["np"], libs["pd"]
    manifest = json.loads((ENGINE.parent / "FREQTRADE_SOURCE.json").read_text())
    assert manifest["commit"] == "c051e9065c5b677f03817dd1f83cb980af1ed684"
    for relative, digest in manifest["files"].items():
        assert sha(ENGINE / relative) == digest, relative
    sys.path.insert(0, str(ENGINE))
    import freqtrade
    from freqtrade.commands.optimize_commands import setup_optimize_configuration
    from freqtrade.enums import RunMode
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.persistence import LocalTrade
    from freqtrade.resolvers import ExchangeResolver

    assert Path(freqtrade.__file__).resolve() == ENGINE / "freqtrade/__init__.py"
    output = ROOT / "user_data/crypto_quant" / HERE.name / "feature_admission"
    output.mkdir(parents=True, exist_ok=False)
    strategies = output / "strategies"
    strategies.mkdir()
    data_root = output / "candles"
    data_root.mkdir()
    shutil.copyfile(HERE / "WaveletFeatureProbe.py", strategies / "WaveletFeatureProbe.py")
    shutil.copyfile(ROOT / "freqtrade/data/wavelet_cpu.py", strategies / "WaveletCpuFeatures.py")
    for asset in ["BTC", "ETH"]:
        frame = pd.read_parquet(HERE / (asset + "_15m.parquet")).iloc[:2000].reset_index()
        frame.to_feather(data_root / (asset + "_USDT-15m.feather"))
    start = pd.Timestamp(frame.date.iloc[0])
    end = pd.Timestamp(frame.date.iloc[-1]) + pd.Timedelta(minutes=15)
    parent = HERE.with_name("trendcore_source_native_20261117_v1")
    config = json.loads((parent / "trendcore_1000_conservative.json").read_text())
    config.update(
        strategy="WaveletFeatureProbe",
        timeframe="15m",
        timeframe_detail="",
        bot_name="wavelet_feature_admission_only",
        research_wavelet_feature_probe=True,
        user_data_dir=str(output),
        strategy_path=str(strategies),
        datadir=str(data_root),
        timerange=f"{int(start.timestamp())}-{int(end.timestamp()) - 1}",
        db_url="sqlite://",
        export="none",
    )
    config.pop("timeframe_detail", None)
    save(output / "config.json", config)
    cfg = setup_optimize_configuration(
        {"config": [str(output / "config.json")], "datadir": str(data_root)}, RunMode.BACKTEST
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
        raise RuntimeError("Native feature admission attempted network")

    socket.socket.connect = no_network
    account = None
    try:
        account = Backtesting(cfg, exchange=exchange)
        strategy = account.strategylist[0]
        account._set_strategy(strategy)
        data, _ = account.load_bt_data()
        receipts = []
        for pair, frame in data.items():
            assert len(frame) == 2000
            actual = strategy.advise_indicators(frame, {"pair": pair})
            asset = pair.split("/")[0]
            reference = pd.read_parquet(
                HERE / "numerical_outputs" / (asset + "_full6000.parquet")
            ).iloc[:2000]
            for field in ["source_gain", "normalized_gain", "source_predicted_gain"]:
                np.testing.assert_array_equal(actual[field], reference[field])
            assert actual.model_ready.sum() == 1000
            for operation in [strategy.populate_entry_trend, strategy.populate_exit_trend]:
                try:
                    operation(actual, {"pair": pair})
                except NotImplementedError:
                    pass
                else:
                    raise AssertionError("Feature probe must refuse financial signal evaluation")
            path = output / (asset + "_native_features.parquet")
            actual.to_parquet(path, index=False)
            receipts.append(
                {
                    "pair": pair,
                    "rows": len(actual),
                    "source_forecasts_exactly_equal": True,
                    "feature_path": str(path),
                    "sha256": sha(path),
                    "training_windows": len(strategy.feature_training),
                }
            )
            print("NATIVE_FEATURE_MATCH", pair, len(actual), flush=True)
        assert not LocalTrade.bt_trades_open and not LocalTrade.bt_trades
        for relative, digest in manifest["files"].items():
            assert sha(ENGINE / relative) == digest, relative
        result = {
            "passed": True,
            "official_engine_commit": manifest["commit"],
            "official_source_files_checked": len(manifest["files"]),
            "real_backtesting_constructor": True,
            "actual_data_provider_and_IStrategy_indicator_calls": True,
            "actual_feature_rows": receipts,
            "source_forecasts_exactly_equal": True,
            "entry_and_exit_evaluation_explicitly_refused": True,
            "financial_start_called": False,
            "native_trades": 0,
            "new_native_financial_wallets": 0,
            "native_results_zip_generated": False,
        }
        save(HERE / "native_feature_admission.json", result)
        print(json.dumps(result))
    finally:
        if account is not None:
            account.cleanup()
        exchange.close()


if __name__ == "__main__":
    main()
